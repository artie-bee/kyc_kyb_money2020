"""
Customer follow-up: view tracking, per-message reminders and the no-response
clock, on a fake clock.

The schedule (kb/communication_schedule.csv) is not changed: one reminder at
14 days, closure at 30, both counted from the FIRST request. Viewing a message
is audited and read by the console panel only; reminders never look at it.

Run: python -m pytest tests/test_follow_up.py -q
"""

import re
import shutil
import sys
import threading
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import data                                                  # noqa: E402
from orchestrator import clock, db, holds, sharpness                  # noqa: E402
from orchestrator.clock import DEMO_START                             # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication, document_quality       # noqa: E402
from portal import server as portal_server                            # noqa: E402
from test_portal_uploads import JPEG, PDF, Browser                    # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render                                                # noqa: E402

KB = KnowledgeBase()
CASE = "WAL-ONB-0014"
EXTRA = "bank_statement"
# A request that names a document an analyst added is a follow-up: its one
# reminder comes after 3 business days (Monday 28 Sep -> Thursday 1 Oct, day 3)
# rather than 14 calendar days. The tests below that add a second item send
# exactly such a request.
FOLLOW_UP_DAY = 3
REMIND, CLOSE = (KB.communication_schedule[k] for k in
                 ("first_reminder_after_days", "close_after_days"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The demo start state, built once: every case up to its first human action."""
    path = tmp_path_factory.mktemp("follow_up") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def env(built, tmp_path, monkeypatch):
    """A fresh copy of the demo database, and a fake clock at the demo start
    that every stamp and every day count reads."""
    path = tmp_path / "onboarding.db"
    shutil.copy(built, path)
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    conn = data.connect(path)
    with clock.use(clock.FakeClock(DEMO_START)) as fake:
        yield {"conn": conn, "clock": fake, "path": path, "tmp": tmp_path}
    conn.close()


def owed_labels(conn):
    return [communication.item_label(i) for i in communication.owed_items(conn, CASE)]


def request(conn):
    """The presenter sends the request, as the console's Send a message does."""
    result = communication.send_required_message(conn, CASE, "resubmission", KB,
                                                 approver="ops.test")
    conn.commit()
    return result


def add_second_item(conn):
    assert EXTRA in data.addable_document_types()
    return data.add_checklist_item(conn, CASE, EXTRA, "analyst.test", "a second item owed")


def run_days(env, days, on_day=None):
    """Chase once a day for `days` days. {day: situation of what was sent}."""
    conn, fake = env["conn"], env["clock"]
    sent = {}
    for day in range(days + 1):
        if day:
            fake.advance(1)
        if on_day:
            on_day(day)
        result = communication.chase(conn, CASE, KB, fake)
        if result is not None and result.communication_id:
            sent[day] = conn.execute("SELECT situation FROM communication WHERE"
                                     " communication_id = ?",
                                     (result.communication_id,)).fetchone()[0]
    conn.commit()
    return sent


def blurred(monkeypatch):
    """Every upload from here measures as too blurred to read (QR-13)."""
    monkeypatch.setattr(sharpness, "measure", lambda path: 5.0)


# ---------------------------------------------------------------------------
# One reminder per message
# ---------------------------------------------------------------------------

def test_two_outstanding_items_produce_one_reminder_listing_both(env):
    conn = env["conn"]
    add_second_item(conn)
    owed = communication.owed_items(conn, CASE)
    assert len(owed) == 2
    request(conn)

    sent = run_days(env, REMIND + 5)
    assert sent == {FOLLOW_UP_DAY: "reminder"},         "one reminder, on the follow-up schedule's day (the request names an added document)"
    reminders = conn.execute("SELECT c.template_id, o.body FROM outbox o JOIN communication c"
                             " USING (communication_id) WHERE c.case_id = ? AND"
                             " c.situation = 'reminder'", (CASE,)).fetchall()
    assert len(reminders) == 1, "never one reminder per document"
    template, body = reminders[0]
    assert template == "TPL-0001" and KB.message_templates[template]["approval_status"] == \
        "approved", "the wording is an approved template's"
    for label in owed_labels(conn):
        assert label in body
    assert "[item_list]" not in body and not communication.scan(body)
    chased = conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                          " AND action IN ('applicant_chased', 'follow_up_reminder_sent')",
                          (CASE,)).fetchone()[0]
    assert all(i["item_id"] in chased for i in owed)


def test_an_item_no_longer_requested_drops_out_of_the_reminder(env):
    conn = env["conn"]
    extra = add_second_item(conn)
    request(conn)
    conn.execute("UPDATE checklist_item SET status = 'waived' WHERE item_id = ?", (extra,))
    run_days(env, REMIND)
    body = conn.execute("SELECT o.body FROM outbox o JOIN communication c USING"
                        " (communication_id) WHERE c.case_id = ? AND c.situation = 'reminder'",
                        (CASE,)).fetchone()[0]
    assert "proof of address" in body and "bank statement" not in body
    status = [r["status"] for r in data.follow_up(conn, CASE)["requests"]]
    assert status == ["Reminder sent"]


# ---------------------------------------------------------------------------
# Viewing
# ---------------------------------------------------------------------------

def test_opening_the_messages_page_records_a_view_by_the_applicant(env):
    conn, path = env["conn"], env["path"]
    sent = request(conn)
    portal = portal_server.serve(0, db_path=path, demo=True, uploads_dir=env["tmp"] / "up")
    threading.Thread(target=portal.serve_forever, daemon=True).start()
    try:
        b = Browser(f"http://127.0.0.1:{portal.server_address[1]}").open_as(CASE)
        assert b.get("/")[0] == 200 and b.get("/checklist")[0] == 200
        assert not conn.execute("SELECT 1 FROM audit_event WHERE action = 'message_viewed'"
                                ).fetchone(), "only the Messages page counts as a view"
        status, page = b.get("/messages")
        assert status == 200 and sent.communication_id not in page, "no id on the page"
    finally:
        portal.shutdown()
        portal.server_close()
    view = conn.execute("SELECT * FROM audit_event WHERE case_id = ? AND"
                        " action = 'message_viewed'", (CASE,)).fetchone()
    assert (view["actor_type"], view["actor_id"]) == ("applicant", "customer")
    assert sent.communication_id in view["payload_summary"]
    shown = data.follow_up(conn, CASE)["requests"][0]
    assert shown["viewed_at"] == view["timestamp"]


def test_viewing_a_message_does_not_stop_or_delay_reminders(env, tmp_path):
    conn = env["conn"]
    request(conn)
    # The same case, never viewed, as the control.
    control = data.connect(shutil.copy(env["path"], tmp_path / "control.db"))
    with clock.use(clock.FakeClock(DEMO_START)) as control_clock:
        control_sent = run_days(dict(env, conn=control, clock=control_clock), CLOSE + 2)
    control.close()

    def view_every_day(day):
        shown = [r[0] for r in conn.execute(
            "SELECT communication_id FROM outbox WHERE case_id = ?", (CASE,))]
        data.record_messages_viewed(conn, CASE, shown)

    sent = run_days(env, CLOSE + 2, on_day=view_every_day)
    assert sent == control_sent == {REMIND: "reminder", CLOSE: "case_closed"}
    assert conn.execute("SELECT COUNT(*) FROM audit_event WHERE case_id = ? AND"
                        " action = 'message_viewed'", (CASE,)).fetchone()[0] == CLOSE + 3


# ---------------------------------------------------------------------------
# Attempts and the no-response clock
# ---------------------------------------------------------------------------

def test_repeated_bad_uploads_do_not_extend_the_no_response_limit(env, monkeypatch):
    conn = env["conn"]
    extra = add_second_item(conn)
    poa = next(i["item_id"] for i in communication.owed_items(conn, CASE)
               if i["document_type"] == "proof_of_address")
    request(conn)
    first = communication.first_request_at(conn, CASE)
    blurred(monkeypatch)

    uploads = {5: extra, 10: poa, 20: extra}

    def upload(day):
        if day in uploads:
            result = data.upload_document(conn, CASE, uploads[day], "scan.jpg", JPEG)
            assert result.quality_status == "resubmission_required"

    sent = run_days(env, CLOSE + 2, on_day=upload)
    assert sent == {FOLLOW_UP_DAY: "reminder", CLOSE: "case_closed"}, \
        "the deadlines count from the first request, not the latest attempt"
    assert communication.first_request_at(conn, CASE) == first
    placed = max(r[0] for r in conn.execute(
        "SELECT placed_at FROM case_hold WHERE case_id = ? AND owner = 'customer'", (CASE,)))
    assert placed > first, "Step 3 re-placed its hold on each upload; the clock ignored it"
    attempts = dict(conn.execute("SELECT item_id, resubmission_attempts FROM checklist_item"
                                 " WHERE item_id IN (?, ?)", (extra, poa)).fetchall())
    assert attempts == {extra: 2, poa: 2}
    audit = [r[0] for r in conn.execute("SELECT action FROM audit_event WHERE case_id = ? AND"
                                        " action IN ('resubmission_requested',"
                                        " 'reminders_stopped')", (CASE,))]
    assert audit == ["resubmission_requested"] * 3, "an unreadable upload stops nothing"
    closed = conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ? AND"
                          " action = 'case_closed_no_response'", (CASE,)).fetchone()[0]
    assert f"no response {CLOSE} days" in closed


def test_the_three_attempt_limit_still_sends_the_item_to_an_analyst(env, monkeypatch):
    conn = env["conn"]
    poa = communication.owed_items(conn, CASE)[0]["item_id"]
    request(conn)
    blurred(monkeypatch)

    def upload(day):
        if day in (1, 2):       # the screenshot on file was attempt 1; two more reach 3
            data.upload_document(conn, CASE, poa, "scan.jpg", JPEG)

    run_days(env, 3, on_day=upload)
    item = conn.execute("SELECT status, resubmission_attempts FROM checklist_item"
                        " WHERE item_id = ?", (poa,)).fetchone()
    assert tuple(item) == ("manual_review", 3)
    assert conn.execute("SELECT 1 FROM audit_event WHERE case_id = ? AND"
                        " action = 'resubmission_limit_reached'", (CASE,)).fetchone()
    assert any(h.owner == "analyst" for h in holds.open_holds(conn, CASE))
    assert run_days(env, CLOSE + 2) == {}, "nothing is owed, so nothing is chased or closed"
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (CASE,)).fetchone()[0] != "closed_withdrawn"


def test_an_acceptable_replacement_stops_the_reminders(env):
    conn = env["conn"]
    poa = communication.owed_items(conn, CASE)[0]["item_id"]
    request(conn)

    def upload(day):
        if day == 5:
            data.upload_document(conn, CASE, poa, "utility_bill.pdf", PDF)

    assert run_days(env, CLOSE + 2, on_day=upload) == {}
    assert data.follow_up(conn, CASE)["requests"][0]["status"] == "Completed"


def test_reminders_go_while_an_analyst_holds_another_document_but_it_never_auto_closes(env):
    """The bank statement goes to an analyst (mock mode's visual check); the
    proof of address is still owed. The customer is reminded on schedule, but
    the case is not withdrawn while the team still has work on it."""
    conn = env["conn"]
    extra = add_second_item(conn)
    request(conn)
    data.upload_document(conn, CASE, extra, "statement.pdf", PDF)
    open_now = holds.open_holds(conn, CASE)
    assert any(h.owner == "analyst" for h in open_now), "an analyst holds the statement"
    assert [i["document_type"] for i in communication.owed_items(conn, CASE)] == \
        ["proof_of_address"], "the customer still owes the other item"

    assert run_days(env, CLOSE + 10) == {FOLLOW_UP_DAY: "reminder"}, \
        "reminded on schedule; not closed while the customer is not the sole blocker"
    body = conn.execute("SELECT o.body FROM outbox o JOIN communication c USING"
                        " (communication_id) WHERE c.case_id = ? AND c.situation = 'reminder'",
                        (CASE,)).fetchone()[0]
    assert "proof of address" in body and "bank statement" not in body, \
        "the item under review is not asked for again"
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (CASE,)).fetchone()[0] != "closed_withdrawn"
    f = data.follow_up(conn, CASE)
    assert f["closes_on"] is None and f["closure_paused"]

    # Once the team has nothing left, the customer is the only blocker, and the
    # limit - still counted from the first request - applies at once.
    held = conn.execute("SELECT MAX(document_id) FROM checklist_item_document WHERE item_id = ?",
                        (extra,)).fetchone()[0]
    data.release_document(conn, held, "analyst.test", "accept", "looked at it: clear")
    for document_id, fields in data.awaiting_fields(conn, CASE).items():
        data.enter_fields(conn, document_id, "analyst.test",
                          {f["name"]: "synthetic" for f in fields})
    assert [h.owner for h in holds.open_holds(conn, CASE)] == ["customer"]
    assert communication.customer_blocking(conn, CASE)[1]
    closed = communication.chase(conn, CASE, KB, env["clock"])
    assert closed is not None and conn.execute(
        "SELECT status FROM onboarding_case WHERE case_id = ?",
        (CASE,)).fetchone()[0] == "closed_withdrawn"


def test_case_14_still_auto_closes_on_its_existing_schedule(env):
    assert (REMIND, CLOSE) == (14, 30), "the schedule itself is unchanged"
    conn = env["conn"]
    request(conn)
    assert run_days(env, CLOSE + 5) == {REMIND: "reminder", CLOSE: "case_closed"}
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (CASE,)).fetchone()[0] == "closed_withdrawn"
    assert [r["status"] for r in data.follow_up(conn, CASE)["requests"]] == ["Closed"]


# ---------------------------------------------------------------------------
# The console panel
# ---------------------------------------------------------------------------

def test_the_panel_follows_the_request_through_its_statuses(env):
    conn, fake = env["conn"], env["clock"]
    assert data.follow_up(conn, CASE)["requests"] == []
    request(conn)
    f = data.follow_up(conn, CASE)
    (r,) = f["requests"]
    assert r["status"] == "Awaiting customer" and r["viewed_at"] is None
    assert r["next_reminder_due"] == (DEMO_START + timedelta(days=REMIND)).date().isoformat()
    assert f["closes_on"] == (DEMO_START + timedelta(days=CLOSE)).date().isoformat()
    assert [d["label"] for d in r["documents"]] == owed_labels(conn)

    run_days(env, REMIND)
    (r,) = data.follow_up(conn, CASE)["requests"]
    assert r["status"] == "Reminder sent" and r["next_reminder_due"] is None
    assert [m["sent_at"][:10] for m in r["reminders"]] == [fake.today().isoformat()]


def test_the_panel_shows_the_timezone_and_never_says_delivered(env):
    conn = env["conn"]
    request(conn)
    run_days(env, REMIND)
    page = render.case_detail(conn, CASE, tab="Communications")
    panel = page.split("<h2>Communications</h2>")[0]
    assert "Customer follow-up" in panel
    for heading in ("Document(s) requested", "Request sent", "Delivery", "Viewed",
                    "Next reminder due", "Reminder history", "Follow-up status"):
        assert heading in panel
    assert "View not recorded" in panel and "All times UTC" in panel
    assert panel.count(communication.DELIVERY_LABEL) >= 2, "the request and the reminder"
    assert "delivered" not in re.sub(r"<[^>]+>", " ", panel).lower()


def test_delivery_is_always_labelled_simulated():
    """Every request and reminder on every case of a full run."""
    conn = db.connect(":memory:")
    run_demo(conn, verbose=False)
    seen = 0
    for (case_id,) in conn.execute("SELECT case_id FROM onboarding_case"):
        for r in communication.follow_up(conn, case_id, KB)["requests"]:
            for m in [r] + r["reminders"]:
                assert m["delivery"] == "Simulated - sent to demo outbox"
                seen += 1
    assert seen >= 3, "case 2's request, case 14's request and its reminder at least"

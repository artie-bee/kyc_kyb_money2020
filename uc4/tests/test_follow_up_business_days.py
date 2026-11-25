"""
A follow-up request after review - a clarification, or a request for a document
an analyst added - gets one reminder after 3 business days (Monday to Friday)
if the customer still owes it. The first document request keeps its 14-day
reminder and 30-day closure. On a fake clock; the demo starts on a Monday.

Run: python -m pytest tests/test_follow_up_business_days.py -q
"""

import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import customer_view                           # noqa: E402
from orchestrator import clock, db                                    # noqa: E402
from orchestrator.clock import DEMO_START                             # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication as C                     # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render                                                # noqa: E402

KB = KnowledgeBase()
PDF = b"%PDF-1.4\n% synthetic demo file\n"
READY = "WAL-ONB-0001"          # every document in, ready for a decision


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("business_days") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def env(built, tmp_path, monkeypatch):
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    conn = data.connect(shutil.copy(built, tmp_path / "onboarding.db"))
    with clock.use(clock.FakeClock(DEMO_START)) as fake:
        yield {"conn": conn, "clock": fake}
    conn.close()


def ask_for_more(conn, case_id=READY, doc="bank_statement", situation="resubmission"):
    item = data.add_checklist_item(conn, case_id, doc, "analyst.test", "needed after review")
    sent = data.send_message(conn, case_id, situation, "analyst.test")
    return item, sent.communication_id


def run_days(env, days, case_id=READY, on_day=None):
    """{date: situation} of what the daily chase sent."""
    conn, fake, out = env["conn"], env["clock"], {}
    for day in range(1, days + 1):
        fake.advance(1)
        if on_day:
            on_day(day)
        r = C.chase(conn, case_id, KB, fake)
        if r is not None and r.communication_id:
            out[fake.today().isoformat()] = conn.execute(
                "SELECT situation FROM communication WHERE communication_id = ?",
                (r.communication_id,)).fetchone()[0]
    conn.commit()
    return out


def test_the_schedule_setting_is_three_business_days():
    assert KB.communication_schedule["follow_up_reminder_business_days"] == 3
    assert KB.communication_schedule["first_reminder_after_days"] == 14, "unchanged"
    assert DEMO_START.strftime("%A") == "Monday"


def test_an_additional_document_request_is_reminded_after_three_business_days(env):
    conn = env["conn"]
    item, request = ask_for_more(conn)
    assert C.follow_up_due(conn, READY, KB) == (request, "2026-10-01")      # Thursday
    sent = run_days(env, 12)
    assert sent == {"2026-10-01": "reminder"}, "once, on the third business day"
    body = conn.execute("SELECT rendered_text, template_id FROM communication WHERE case_id = ?"
                        " AND situation = 'reminder'", (READY,)).fetchone()
    assert body["template_id"] == "TPL-0001" and "bank statement" in body["rendered_text"]
    event = conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ? AND"
                         " action = 'follow_up_reminder_sent'", (READY,)).fetchone()[0]
    assert event.startswith(request + " not answered 3 business days") and item in event
    # it reaches the customer: unread in the portal
    view = customer_view(conn, READY)
    assert view["unread"] == 2 and "bank statement" in view["messages"][-1]["text"]


def test_the_weekend_does_not_count(env):
    env["clock"].advance(4)                                   # Friday 2 Oct
    assert env["clock"].today().strftime("%a") == "Fri"
    _, request = ask_for_more(env["conn"])
    assert C.follow_up_due(env["conn"], READY, KB) == (request, "2026-10-07")  # Wednesday
    assert run_days(env, 7) == {"2026-10-07": "reminder"}


def test_an_acceptable_upload_stops_it_and_viewing_does_not_delay_it(env, tmp_path):
    conn = env["conn"]
    item, request = ask_for_more(conn)
    control = data.connect(shutil.copy(conn.execute("PRAGMA database_list").fetchone()[2],
                                       tmp_path / "control.db"))
    with clock.use(clock.FakeClock(DEMO_START)) as other:
        viewed = run_days(dict(env, conn=control, clock=other), 5,
                          on_day=lambda d: data.record_messages_viewed(control, READY, [request]))
    control.close()
    assert viewed == {"2026-10-01": "reminder"}, "viewing the request does not move it"

    def upload(day):
        if day == 1:
            data.upload_document(conn, READY, item, "statement.pdf", PDF)
    assert run_days(env, 10, on_day=upload) == {}, "the document arrived: no reminder"


def test_a_clarification_is_a_follow_up_too(env):
    conn = env["conn"]
    case = "WAL-ONB-0003"
    item = data.add_checklist_item(conn, case, "registry_extract", "analyst.test",
                                   "confirm the registered address")
    sent = data.send_message(conn, case, "clarification", "analyst.test")
    assert [r["communication_id"] for r in C.follow_up_requests(conn, case)] == \
        [sent.communication_id]
    assert run_days(env, 5, case_id=case) == {"2026-10-01": "reminder"}
    assert "registry extract" in conn.execute(
        "SELECT rendered_text FROM communication WHERE case_id = ? AND situation = 'reminder'",
        (case,)).fetchone()[0]
    assert item


def test_the_first_document_request_keeps_its_fourteen_days(env):
    conn = env["conn"]
    C.send_required_message(conn, "WAL-ONB-0014", "resubmission", KB, approver="ops.test")
    assert C.follow_up_requests(conn, "WAL-ONB-0014") == []
    sent = run_days(env, 20, case_id="WAL-ONB-0014")
    assert sent == {(DEMO_START + timedelta(days=14)).date().isoformat(): "reminder"}


def test_a_newly_added_document_is_asked_for_before_it_is_reminded(env):
    conn = env["conn"]
    case = "WAL-ONB-0002"
    data.send_message(conn, case, "resubmission", "analyst.test")        # the first request
    assert data.suggested_message(conn, case)["situation"] == "reminder"
    data.add_checklist_item(conn, case, "bank_statement", "analyst.test", "after review")
    suggestion = data.suggested_message(conn, case)
    assert suggestion["situation"] == "resubmission"
    assert "bank statement" in suggestion["why"] and "3 business days" in suggestion["why"]


def test_the_panel_timeline_and_dashboard_show_the_business_day_date(env):
    conn = env["conn"]
    ask_for_more(conn)
    assert C.waiting_timing(conn, READY, KB)["next_due"] == "2026-10-01"
    panel = render.case_detail(conn, READY, tab=render.MESSAGES_TAB)
    assert "2026-10-01 UTC" in panel, "next reminder due on the follow-up panel"
    assert "Reminder due 2026-10-01" in render.case_detail(conn, READY, tab="Timeline")


def test_a_message_naming_a_document_puts_it_on_the_checklist(env):
    """TPL-0003 asks for an ownership chart by name. Sent on a case that owes
    nothing, it adds the chart to the checklist first, so the customer has
    somewhere to upload it and the follow-up reminder covers it."""
    conn = env["conn"]
    assert not C.owed_items(conn, READY)
    preview = data.manual_previews(conn, READY, ["missing_items"])["missing_items"]
    assert preview["template_id"] == "TPL-0003" and preview["adds"] == "ownership_chart"
    # The chart is named once, in the template's own words.
    assert preview["text"].count("ownership chart") == 1
    sent = data.send_message(conn, READY, "missing_items", "analyst.test")
    owed = C.owed_items(conn, READY)
    assert [i["document_type"] for i in owed] == ["ownership_chart"]
    assert owed[0]["item_id"] in C._items_requested(conn, READY)[sent.communication_id]
    assert C.follow_up_due(conn, READY, KB)[0] == sent.communication_id
    from app.customer_view import customer_checklist
    assert any(i["checklist_item_id"] == owed[0]["item_id"] and i["can_upload"]
               for i in customer_checklist(conn, READY)["items"])
    # Asked for and owed: sending it again adds nothing more.
    assert data.manual_previews(conn, READY, ["missing_items"])["missing_items"]["adds"] is None
    assert data.suggested_message(conn, READY)["situation"] == "reminder"


def _ack_column(conn, case_id=READY):
    import html as _html
    import re as _re
    page = render.dashboard(conn)
    row = next(t for t in _re.findall(r'<tr class="rowlink".*?</tr>', page, _re.S)
               if "/case/" + case_id + '"' in t)
    cell = _re.search(r'<td class="cell-ack".*?</td>', row, _re.S).group(0)
    return _re.sub(r"\s+", " ", _html.unescape(_re.sub(r"<[^>]+>", " ", cell))).strip()


def test_the_dashboard_shows_acknowledgement_reminder_and_what_the_customer_saw(env):
    conn = env["conn"]
    ask_for_more(conn)
    # Only the customer owes something: nobody's alert to acknowledge, but the
    # request and its reminder date are there.
    text = _ack_column(conn)
    assert "acknowledged" not in text.lower()
    assert "Request sent" in text and "not seen yet" in text and "Reminder due" in text
    # A staff alert, then an analyst opening the case.
    from orchestrator import holds as H
    H.place(conn, READY, "step.verification", "eligibility", "eligibility: check it",
            "analyst", KB)
    conn.commit()
    assert "Not acknowledged yet" in _ack_column(conn)
    data.record_alert_opened(conn, READY, "analyst.test", "analyst")
    assert "Acknowledged by analyst.test" in _ack_column(conn)
    # The customer reads the request; the reminder goes and is read too.
    sent = C.follow_up(conn, READY, KB)["requests"][-1]["communication_id"]
    data.record_messages_viewed(conn, READY, [sent])
    reminder = data.send_message(conn, READY, "reminder", "analyst.test")
    text = _ack_column(conn)
    assert "Reminder sent" in text and text.count("seen") >= 1 and "not seen yet" in text
    data.record_messages_viewed(conn, READY, [reminder.communication_id])
    assert "not seen yet" not in _ack_column(conn)

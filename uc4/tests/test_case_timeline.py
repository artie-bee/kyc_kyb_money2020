"""
The case timeline, the automatic "alert_opened" record, and "Alert came in"
on the dashboard.

The opened record is the only thing here that writes: the first time someone
who may act on an open hold opens the case detail page, once per hold. Listing
a case, a queue, a preview or another case records nothing.

Run: python -m pytest tests/test_case_timeline.py -q
"""

import shutil
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import clock, db, holds                             # noqa: E402
from orchestrator.clock import DEMO_START                             # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication, decision               # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render, server as console_server                      # noqa: E402

KB = KnowledgeBase()
PDF = b"%PDF-1.4\n% synthetic demo file\n"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("timeline") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


@pytest.fixture()
def site(built, tmp_path, monkeypatch):
    """The console on a real socket over a fresh copy, and a fake clock."""
    path = shutil.copy(built, tmp_path / "onboarding.db")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    console_server.drop_connection()
    console_server._conn = data.connect(path)
    httpd = console_server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()),
                                         NoRedirect())

    def go(p, fields=None):
        req = urllib.request.Request(base + p, urlencode(fields).encode() if fields else None)
        try:
            with opener.open(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def act_as(role, name):
        go("/action/settings", {"role": role, "reviewer": name, "back": "/"})

    conn = data.connect(path)
    with clock.use(clock.FakeClock(DEMO_START)) as fake:
        yield {"go": go, "as": act_as, "conn": conn, "clock": fake}
    conn.close()
    httpd.shutdown()
    httpd.server_close()
    console_server.drop_connection()


def opened(conn, case_id):
    return [tuple(r) for r in conn.execute(
        "SELECT actor_type, actor_id, substr(payload_summary, 1, 8) FROM audit_event"
        " WHERE case_id = ? AND action = 'alert_opened' ORDER BY event_id", (case_id,))]


def open_hold_ids(conn, case_id):
    return [h.hold_id for h in holds.open_holds(conn, case_id)]


# ---------------------------------------------------------------------------
# The opened record
# ---------------------------------------------------------------------------

def test_opening_case_detail_records_the_alert_once(site):
    conn, (hold,) = site["conn"], open_hold_ids(site["conn"], "WAL-ONB-0002")
    site["as"]("analyst", "analyst.one")
    site["clock"].advance(0.5)                                  # 12 hours after the alert
    assert site["go"]("/case/WAL-ONB-0002")[0] == 200
    assert opened(conn, "WAL-ONB-0002") == [("analyst", "analyst.one", hold)]
    payload = conn.execute("SELECT payload_summary FROM audit_event WHERE action ="
                           " 'alert_opened'").fetchone()[0]
    assert "opened by analyst.one (analyst) 720 min after the alert" in payload
    # reopening, another tab, or another permitted person: nothing more
    site["go"]("/case/WAL-ONB-0002")
    site["go"]("/case/WAL-ONB-0002?tab=Documents")
    site["as"]("analyst", "analyst.two")
    site["go"]("/case/WAL-ONB-0002")
    assert len(opened(conn, "WAL-ONB-0002")) == 1
    # a record only: the case, its holds and its owner are as they were
    assert open_hold_ids(conn, "WAL-ONB-0002") == [hold]
    page = site["go"]("/case/WAL-ONB-0002")[1]
    assert "Opened by analyst.one" in page and "720 min after the alert" in page


def test_listing_queueing_previewing_or_another_case_records_nothing(site):
    site["as"]("analyst", "analyst.one")
    for path in ("/", "/queue", "/customer/WAL-ONB-0002", "/export/WAL-ONB-0002",
                 "/export/WAL-ONB-0002.json", "/case/WAL-ONB-0003",
                 "/case/WAL-ONB-0002?tab=Decision"):          # an old link: a redirect
        site["go"](path)
    assert opened(site["conn"], "WAL-ONB-0002") == []


def test_only_someone_who_may_act_on_the_alert_is_recorded(site):
    """Case 6's alert is settled by a decision only compliance may take."""
    conn = site["conn"]
    assert not decision.permitted_decisions(conn, "WAL-ONB-0006", "analyst", KB)
    site["as"]("analyst", "analyst.one")
    site["go"]("/case/WAL-ONB-0006")
    assert opened(conn, "WAL-ONB-0006") == [], "an analyst cannot act on it"
    site["as"]("compliance", "compliance.one")
    site["go"]("/case/WAL-ONB-0006")
    assert [o[:2] for o in opened(conn, "WAL-ONB-0006")] == [("compliance", "compliance.one")]


def test_a_new_hold_is_a_new_alert_with_its_own_open(site):
    conn = site["conn"]
    site["as"]("analyst", "analyst.one")
    site["go"]("/case/WAL-ONB-0002")
    first = open_hold_ids(conn, "WAL-ONB-0002")
    site["clock"].advance(1)
    data.add_checklist_item(conn, "WAL-ONB-0002", "bank_statement", "analyst.one",
                            "one more document")          # Step 3 re-places its hold
    second = open_hold_ids(conn, "WAL-ONB-0002")
    assert second != first
    site["go"]("/case/WAL-ONB-0002")
    assert [o[2] for o in opened(conn, "WAL-ONB-0002")] == first + second


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------

def titles(conn, case_id):
    return [(m["title"], m["future"]) for m in data.case_milestones(conn, case_id)]


def in_order(seen, wanted):
    """Each wanted title appears, in this order, among what is seen."""
    it = iter(seen)
    return all(any(w == s for s in it) for w in wanted)


def test_case_2_timeline(site):
    conn, fake = site["conn"], site["clock"]
    site["as"]("analyst", "analyst.one")
    fake.advance(0.1)
    site["go"]("/case/WAL-ONB-0002")
    comm = data.send_message(conn, "WAL-ONB-0002", "resubmission", "analyst.one")
    assert ("Not viewed yet", True) in titles(conn, "WAL-ONB-0002")
    fake.advance(1)
    data.record_messages_viewed(conn, "WAL-ONB-0002", [comm.communication_id])
    fake.advance(1)
    director = next(i["item_id"] for i in communication.owed_items(conn, "WAL-ONB-0002")
                    if i["document_type"] == "id_document")
    data.upload_document(conn, "WAL-ONB-0002", director, "passport.pdf", PDF)
    seen = titles(conn, "WAL-ONB-0002")
    past = [t for t, future in seen if not future]
    assert in_order(past, ["Application received", "Alert", "Opened by analyst.one",
                           "Request sent to customer", "Viewed by customer",
                           "Replacement uploaded"]), past
    assert "Not viewed yet" not in [t for t, _ in seen]
    ms = data.case_milestones(conn, "WAL-ONB-0002")
    request = next(m for m in ms if m["title"] == "Request sent to customer")
    assert "id document for Denton Halliwell" in request["detail"]
    upload = next(m for m in ms if m["title"] == "Replacement uploaded")
    assert "passport.pdf" in upload["detail"]
    page =render.case_detail(conn, "WAL-ONB-0002", tab="Timeline")
    assert page.index("Case timeline") < page.index("<h2>Audit trail</h2>")
    assert "All times UTC." in page and ' UTC</span>' in page


def test_case_6_timeline(site):
    conn = site["conn"]
    site["as"]("compliance", "compliance.one")
    site["go"]("/case/WAL-ONB-0006")
    decision.record_decision(conn, "WAL-ONB-0006", "compliance.one", "compliance", "escalate",
                             "possible_sanctions_match", "possible match", [],
                             escalation_target="sanctions.desk", kb=KB)
    past = [t for t, future in titles(conn, "WAL-ONB-0006") if not future]
    assert in_order(past, ["Application received", "Alert", "Opened by compliance.one",
                           "Decision recorded"]), past
    alert = next(m for m in data.case_milestones(conn, "WAL-ONB-0006") if m["title"] == "Alert")
    assert "possible sanctions match" in alert["detail"], "the console is internal"


def test_case_14_timeline_scheduled_items_turn_real(site):
    conn = site["conn"]
    communication.send_required_message(conn, "WAL-ONB-0014", "resubmission", KB,
                                        approver="ops.test")
    seen = titles(conn, "WAL-ONB-0014")
    future = [t for t, f in seen if f]
    assert future == ["Not viewed yet", "Reminder due 2026-10-12",
                      "Closes if no reply 2026-10-28"], future
    page = render.case_detail(conn, "WAL-ONB-0014", tab="Timeline")
    assert page.count('class="ms ms--future"') == 3, "dashed until they happen"

    for days in (7, 7, 7, 7, 1, 1):
        data.advance_demo_clock(conn, days, "analyst.one")
    seen = titles(conn, "WAL-ONB-0014")
    past = [t for t, f in seen if not f]
    assert in_order(past, ["Application received", "Alert", "Request sent to customer",
                           "Reminder sent", "Case closed"]), past
    assert [t for t, f in seen if f] == [], "nothing is scheduled on a closed case"
    closed = next(m for m in data.case_milestones(conn, "WAL-ONB-0014")
                  if m["title"] == "Case closed")
    assert closed["detail"] == "no response from the customer"
    assert closed["when"][:10] == "2026-10-28"


# ---------------------------------------------------------------------------
# Alert came in
# ---------------------------------------------------------------------------

def test_alert_came_in_is_the_current_open_holds_placed_time(site):
    conn, fake = site["conn"], site["clock"]

    def alert_at(case_id):
        return {r["case_id"]: r for r in data.dashboard(conn)}[case_id]["alert_at"]

    placed = conn.execute("SELECT MAX(placed_at) FROM case_hold WHERE case_id = 'WAL-ONB-0002'"
                          " AND released_at IS NULL").fetchone()[0]
    assert alert_at("WAL-ONB-0002") == placed == "2026-09-28T09:00:00Z"
    page = render.dashboard(conn, {"view": "all"})
    assert "Alert came in" in page and "2026-09-28 09:00 UTC" in page

    fake.advance(2)
    data.add_checklist_item(conn, "WAL-ONB-0002", "bank_statement", "analyst.one", "one more")
    assert alert_at("WAL-ONB-0002") == (DEMO_START + timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"), "a new hold replaced the old one"

    decision.record_decision(conn, "WAL-ONB-0010", "analyst.one", "analyst", "approve",
                             "all_mandatory_checks_passed", "clean", [], kb=KB)
    assert alert_at("WAL-ONB-0010") == "", "no open alert, so blank"

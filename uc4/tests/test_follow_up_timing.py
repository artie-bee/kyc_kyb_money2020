"""
Follow-up timing on the dashboard, the optional early nudge, and the demo
clock - all on a fake clock.

The schedule is unchanged: one reminder at 14 days and closure at 30, counted
from the FIRST request, whatever the customer has or has not viewed. The nudge
is off unless kb/communication_schedule.csv gives it a day count.

Run: python -m pytest tests/test_follow_up_timing.py -q
"""

import copy
import re
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
from orchestrator import clock, db, settings                          # noqa: E402
from orchestrator.clock import DEMO_START                             # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication                         # noqa: E402
from portal import server as portal_server                            # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render, server as console_server                      # noqa: E402

KB = KnowledgeBase()
CASE = "WAL-ONB-0014"
NUDGE_DAYS = 3


def with_nudge(days):
    kb = copy.copy(KB)
    kb.communication_schedule = dict(KB.communication_schedule, unviewed_nudge=days)
    return kb


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("timing") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def env(built, tmp_path, monkeypatch):
    path = tmp_path / "onboarding.db"
    shutil.copy(built, path)
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    conn = data.connect(path)
    with clock.use(clock.FakeClock(DEMO_START)) as fake:
        yield {"conn": conn, "clock": fake, "path": path, "tmp": tmp_path}
    conn.close()


def request(conn):
    result = communication.send_required_message(conn, CASE, "resubmission", KB,
                                                 approver="ops.test")
    conn.commit()
    return result.communication_id


def run_days(env, days, kb, on_day=None):
    """Chase once a day. {day: situation of what was sent that day}."""
    conn, fake, sent = env["conn"], env["clock"], {}
    for day in range(days + 1):
        if day:
            fake.advance(1)
        if on_day:
            on_day(day)
        before = conn.execute("SELECT COALESCE(MAX(communication_id), '') FROM communication"
                              ).fetchone()[0]
        communication.chase(conn, CASE, kb, fake)
        for (situation,) in conn.execute(
                "SELECT situation FROM communication WHERE case_id = ? AND"
                " communication_id > ? AND sent_status = 'sent' ORDER BY communication_id",
                (CASE, before)):
            sent.setdefault(day, []).append(situation)
    conn.commit()
    return sent


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------

def row(page, case_id):
    return re.search(r'<tr class="rowlink" data-href="/case/' + case_id + r'".*?</tr>',
                     page, re.S).group(0)


def test_the_dashboard_shows_follow_up_timing_for_cases_2_and_14(env):
    conn, fake = env["conn"], env["clock"]
    rows = {r["case_id"]: r for r in data.dashboard(conn)}
    for case_id in ("WAL-ONB-0002", CASE):
        w = rows[case_id]["waiting"]
        assert w["since"][:10] == "2026-09-28" and w["days"] == 0
        assert w["last_viewed"] is None
        assert (w["next_due"], w["next_kind"]) == ("2026-10-12", "reminder")
    assert rows["WAL-ONB-0001"]["waiting"] is None, "case 1 owes nothing"

    comm = request(conn)
    fake.advance(5)
    data.record_messages_viewed(conn, CASE, [comm])
    page = render.dashboard(conn, {"view": "all"})
    fourteen, two, one = row(page, CASE), row(page, "WAL-ONB-0002"), row(page, "WAL-ONB-0001")
    # the dashboard keeps the filter; the timing columns moved to the case detail
    assert 'data-waiting="1"' in fourteen and 'data-waiting="1"' in two
    assert 'data-waiting="0"' in one
    waiting = sum(1 for r in data.dashboard(conn) if r["waiting"])
    assert f'name="waiting" value="1"><span>Waiting on customer ({waiting})' in page
    for heading in ("Waiting on customer since", "Last message viewed", "Next reminder due"):
        assert heading not in page
    detail = render.case_detail(conn, CASE, tab=render.MESSAGES_TAB)
    assert "2026-10-03 09:00:00 UTC" in detail, "the customer's view, on the follow-up panel"
    assert "2026-10-12 UTC" in detail, "the next reminder due"
    assert "Reminder due 2026-10-12" in render.case_detail(conn, CASE, tab="Timeline")


def test_a_closed_case_is_not_waiting_on_the_customer(env):
    request(env["conn"])
    run_days(env, 30, KB)
    rows = {r["case_id"]: r for r in data.dashboard(env["conn"])}
    assert rows[CASE]["status"] == "closed_withdrawn" and rows[CASE]["waiting"] is None


# ---------------------------------------------------------------------------
# The optional nudge
# ---------------------------------------------------------------------------

def test_the_nudge_is_off_by_default_and_never_sent(env):
    assert KB.communication_schedule["unviewed_nudge"] == 0
    request(env["conn"])
    sent = run_days(env, 32, KB)
    assert sent == {14: ["reminder"], 30: ["case_closed"]}
    assert not env["conn"].execute("SELECT 1 FROM audit_event WHERE action ="
                                   " 'unviewed_nudge_sent'").fetchone()


def test_when_on_one_nudge_goes_to_an_unviewed_request(env):
    comm = request(env["conn"])
    sent = run_days(env, 32, with_nudge(NUDGE_DAYS))
    assert sent == {NUDGE_DAYS: ["unviewed_nudge"], 14: ["reminder"], 30: ["case_closed"]}, \
        "one nudge, and the normal schedule unchanged"
    (payload,) = env["conn"].execute("SELECT payload_summary FROM audit_event WHERE"
                                     " action = 'unviewed_nudge_sent'").fetchone()
    assert payload.startswith(comm + " not viewed 3 days")
    nudge = env["conn"].execute("SELECT template_id, rendered_text FROM communication WHERE"
                                " situation = 'unviewed_nudge'").fetchone()
    assert nudge["template_id"] == "TPL-0001", "the approved request wording"
    assert "proof of address" in nudge["rendered_text"]
    assert not communication.scan(nudge["rendered_text"])


def test_no_nudge_once_the_customer_has_viewed_the_request(env):
    conn = env["conn"]
    comm = request(conn)

    def view(day):
        if day == 1:
            data.record_messages_viewed(conn, CASE, [comm])

    sent = run_days(env, 32, with_nudge(NUDGE_DAYS), on_day=view)
    assert sent == {14: ["reminder"], 30: ["case_closed"]}, \
        "viewed, so no nudge - and the reminders neither stop nor move"


def test_viewing_never_stops_or_delays_the_normal_reminders_with_the_nudge_on(env, tmp_path):
    conn = env["conn"]
    comm = request(conn)
    control = data.connect(shutil.copy(env["path"], tmp_path / "control.db"))
    with clock.use(clock.FakeClock(DEMO_START)) as other:
        unviewed = run_days(dict(env, conn=control, clock=other), 32, with_nudge(NUDGE_DAYS))
    control.close()
    viewed = run_days(env, 32, with_nudge(NUDGE_DAYS),
                      on_day=lambda day: data.record_messages_viewed(conn, CASE, [comm]))
    strip = lambda s: {d: [x for x in v if x != "unviewed_nudge"] for d, v in s.items()  # noqa
                       if [x for x in v if x != "unviewed_nudge"]}
    assert strip(viewed) == strip(unviewed) == {14: ["reminder"], 30: ["case_closed"]}


def test_the_dashboard_shows_the_nudge_when_it_is_due_first(env, monkeypatch):
    request(env["conn"])
    monkeypatch.setattr(data, "kb", lambda: with_nudge(NUDGE_DAYS))
    w = {r["case_id"]: r for r in data.dashboard(env["conn"])}[CASE]["waiting"]
    assert (w["next_due"], w["next_kind"]) == ("2026-10-01", "nudge")


# ---------------------------------------------------------------------------
# The demo clock
# ---------------------------------------------------------------------------

def test_advancing_the_demo_clock_30_days_closes_case_14_as_withdrawn(env):
    conn = env["conn"]
    for days in (7, 7, 7, 7, 1, 1):
        data.advance_demo_clock(conn, days, "analyst.test")
    assert data.demo_clock_days(conn) == 30
    assert conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (CASE,)).fetchone()[0] == "closed_withdrawn"
    closed = conn.execute("SELECT payload_summary, timestamp FROM audit_event WHERE"
                          " case_id = ? AND action = 'case_closed_no_response'",
                          (CASE,)).fetchone()
    assert closed["payload_summary"].startswith("no response 30 days")
    assert closed["timestamp"][:10] == "2026-10-28", "stamped on the demo clock's day"
    advances = conn.execute("SELECT actor_id, payload_summary FROM audit_event WHERE"
                            " case_id = ? AND action = 'demo_clock_advanced'",
                            (CASE,)).fetchall()
    assert len(advances) == 6 and all(a["actor_id"] == "analyst.test" for a in advances)
    assert all(data.DEMO_CLOCK_LABEL in a["payload_summary"] for a in advances)
    # the reminder went on its day, before the closure
    kinds = [r[0] for r in conn.execute("SELECT situation FROM communication WHERE"
                                        " case_id = ? ORDER BY communication_id", (CASE,))]
    assert kinds == ["reminder", "case_closed"]


def test_each_click_moves_the_demo_clock_one_day_inside_a_console_request(env):
    # The console runs every request on the demo clock already; the advance
    # must not add the days so far a second time.
    conn = env["conn"]
    start = clock.current().today()
    for n in (1, 2, 3):
        with clock.use(data.demo_clock(conn)):
            result = data.advance_demo_clock(conn, 1, "analyst.test")
        assert result["total"] == n
        assert result["date"] == (start + timedelta(days=n)).isoformat()


def test_minus_one_day_moves_only_the_date_and_never_before_the_start(env):
    conn = env["conn"]
    start = clock.current().today()
    with pytest.raises(ValueError):
        data.rewind_demo_clock(conn, "analyst.test")
    assert 'value="-1" title="Moves the date back only: messages already sent stay ' \
           'sent" disabled' in render.page("x", "", "Case detail", conn=conn, case_id=CASE)
    for days in (7, 7):
        data.advance_demo_clock(conn, days, "analyst.test")
    sent = conn.execute("SELECT COUNT(*) FROM communication WHERE case_id = ? AND"
                        " sent_status = 'sent'", (CASE,)).fetchone()[0]
    result = data.rewind_demo_clock(conn, "analyst.test")
    assert result["total"] == data.demo_clock_days(conn) == 13
    assert result["date"] == (start + timedelta(days=13)).isoformat()
    assert conn.execute("SELECT COUNT(*) FROM communication WHERE case_id = ? AND"
                        " sent_status = 'sent'", (CASE,)).fetchone()[0] == sent, \
        "messages already sent stay sent"
    # forward again from where it stands, not from the highest total ever
    assert data.advance_demo_clock(conn, 1, "analyst.test")["total"] == 14


def test_reset_demo_puts_the_demo_clock_back_to_the_start(env):
    data.advance_demo_clock(env["conn"], 7, "analyst.test")
    env["conn"].close()
    data.reset_demo(env["path"])
    env["conn"] = data.connect(env["path"])
    assert data.demo_clock_days(env["conn"]) == 0


def test_the_demo_clock_only_moves_1_or_7_days_and_only_in_a_demo_build(env, monkeypatch):
    with pytest.raises(ValueError):
        data.advance_demo_clock(env["conn"], 30, "analyst.test")
    monkeypatch.setattr(settings, "DEMO_BUILD", False)
    with pytest.raises(PermissionError):
        data.advance_demo_clock(env["conn"], 1, "analyst.test")
    assert "democlock" not in render.page("x", "", "Case detail", conn=env["conn"],
                                          case_id=CASE)


def _serve(httpd):
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


def test_the_console_has_the_labelled_control_and_the_portal_has_none(env):
    path = env["path"]
    request(env["conn"])            # so the portal's Messages page has one to view
    console_server.drop_connection()
    console_server._conn = data.connect(path)
    console = console_server.serve(0)
    portal = portal_server.serve(0, db_path=path, demo=True, uploads_dir=env["tmp"] / "up")
    cbase, pbase = _serve(console), _serve(portal)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()),
                                         NoRedirect())

    def go(base, p, fields=None):
        req = urllib.request.Request(base + p, urlencode(fields).encode() if fields else None)
        try:
            with opener.open(req) as r:
                return r.status, r.headers.get("Location"), r.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Location"), err.read().decode()
    try:
        page = go(cbase, "/case/" + CASE)[2]
        rail = page.split('<main class="main">')[0]
        assert "Demo clock - not real time" in rail and 'action="/action/demo-clock"' in rail
        assert "+1 day" in rail and "+7 days" in rail
        status, where, _ = go(cbase, "/action/demo-clock",
                              {"days": "7", "back": "/case/" + CASE})
        assert status == 303 and "advanced%207%20day" in where
        assert "(+7 days)" in go(cbase, "/case/" + CASE)[2]
        status, where, _ = go(cbase, "/action/demo-clock",
                              {"days": "-1", "back": "/case/" + CASE})
        assert status == 303 and "moved%20back%201%20day" in where
        assert "(+6 days)" in go(cbase, "/case/" + CASE)[2]

        # the portal: no control, no label - but it keeps the console's date
        status, _, _ = go(pbase, "/demo/open", {"case_id": CASE})
        for p in ("/", "/checklist", "/messages", "/demo"):
            text = go(pbase, p)[2]
            assert "Demo clock" not in text and "demo-clock" not in text, p
        assert go(pbase, "/action/demo-clock", {"days": "1"})[0] == 404
        view = data.connect(path).execute(
            "SELECT timestamp FROM audit_event WHERE case_id = ? AND action = 'message_viewed'"
            " ORDER BY event_id DESC", (CASE,)).fetchone()
        assert view[0][:10] == (DEMO_START + timedelta(days=6)).date().isoformat(), \
            "the portal stamps its events on the console's demo date"
    finally:
        for httpd in (console, portal):
            httpd.shutdown()
            httpd.server_close()
        console_server.drop_connection()
    text = (ROOT / "portal" / "render.py").read_text(encoding="utf-8")
    assert "democlock" not in text and "Demo clock" not in text and "demo-clock" not in text

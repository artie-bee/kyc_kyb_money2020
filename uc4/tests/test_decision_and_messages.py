"""
One "Decision & messages" tab in place of Decision and Communications, and
the sidebar's "Customer" card. Presentation and navigation only: every value
is read from existing records and every action is an existing one.

Run: python -m pytest tests/test_decision_and_messages.py -q
"""

import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import db                                           # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication, decision               # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render, server                                        # noqa: E402

KB = KnowledgeBase()
TAB = "/case/{}?tab=Decision-messages"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Two start states: the demo after Reset (no human action yet), and the
    dataset's full run (decisions recorded, case 14 closed)."""
    folder = tmp_path_factory.mktemp("decision_and_messages")
    paths = {}
    for name, stop in (("reset", True), ("full", False)):
        conn = db.connect(folder / f"{name}.db")
        run_demo(conn, verbose=False, stop_before_human_actions=stop)
        conn.commit()
        conn.close()
        paths[name] = folder / f"{name}.db"
    return paths


@pytest.fixture()
def fresh(built, tmp_path):
    opened = []

    def open_copy(name):
        conn = data.connect(shutil.copy(built[name], tmp_path / f"{name}{len(opened)}.db"))
        opened.append(conn)
        return conn
    yield open_copy
    for c in opened:
        c.close()


@pytest.fixture()
def site(built, tmp_path):
    path = shutil.copy(built["reset"], tmp_path / "site.db")
    server.drop_connection()
    server._conn = data.connect(path)
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()),
                                         NoRedirect())

    def go(p):
        try:
            with opener.open(f"http://127.0.0.1:{httpd.server_address[1]}" + p) as r:
                return r.status, r.headers.get("Location"), r.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Location"), err.read().decode()
    yield go
    httpd.shutdown()
    httpd.server_close()
    server.drop_connection()


def summary(conn, case_id):
    s = data.customer_summary(conn, case_id)
    return s["latest"], s["state"]


# ---------------------------------------------------------------------------
# The merged tab
# ---------------------------------------------------------------------------

def test_the_merged_tab_holds_everything_both_tabs_had_in_order(fresh):
    conn = fresh("full")
    page = render.case_detail(conn, "WAL-ONB-0012", tab=render.MESSAGES_TAB,
                              role="compliance")
    assert render.TAB_LABELS[render.MESSAGES_TAB] == "Decision & messages"
    assert ">Decision &amp; messages</a>" in page
    assert "?tab=Decision\"" not in page and "?tab=Communications" not in page
    marks = ['id="follow-up"', "Customer follow-up", 'id="decision"', "<h2>Decision</h2>",
             "System recommends", "Allowed at band", "Not offered at band",
             'class="actionbox"', '<span class="actionbox__n">1</span>Record a decision',
             'action="/action/record-decision"', 'id="customer-message"',
             'class="actionbox__or"', 'id="send"',
             '<span class="actionbox__n">2</span>Send a message only',
             'action="/action/send-message"',
             'id="messages"', "<h2>Message history</h2>", "Compliance task"]
    where = [page.find(m) for m in marks]
    assert -1 not in where, [m for m, w in zip(marks, where) if w == -1]
    assert where == sorted(where),         "follow-up, then one action box (decide OR send a message only), then the history"
    # the preview sits beside the form, in the same two-column row
    row = page.split('<div class="grid2 decide">')[1]
    assert row.find("record-decision") < row.find('id="customer-message"')
    # every message to the customer is listed
    for (comm_id,) in conn.execute("SELECT communication_id FROM communication WHERE"
                                   " case_id = 'WAL-ONB-0012'"):
        assert comm_id in page.split('id="messages"')[1]


def test_old_tab_links_redirect_to_the_matching_section(site):
    for old, section in (("Decision", "decision"), ("Communications", "messages")):
        status, where, _ = site(f"/case/WAL-ONB-0002?tab={old}")
        assert status == 303
        assert where == f"/case/WAL-ONB-0002?tab=Decision-messages#{section}"
    # an outcome carried by a form's old back address survives the redirect
    status, where, _ = site("/case/WAL-ONB-0002?tab=Decision&ok=Recorded%20it")
    assert status == 303
    assert where == "/case/WAL-ONB-0002?tab=Decision-messages&ok=Recorded%20it#decision"
    status, _, page = site(where.split("#")[0])
    assert status == 200 and "Recorded it" in page and 'class="tab tab--on"' in page


def test_the_customer_card_is_on_every_tab_and_links_to_message_history(site):
    for tab in render.TABS:
        _, _, page = site(f"/case/WAL-ONB-0002?tab={tab}")
        rail = page.split('<main class="main">')[0]
        assert 'aria-label="Customer summary"' in rail, tab
        assert "No messages yet" in rail and "Open - waiting on customer" in rail, tab
    assert 'aria-label="Customer summary"' not in site("/")[2], "only on a case screen"


def test_the_portal_never_shows_the_customer_card():
    for p in (ROOT / "portal").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "railcard" not in text and "customer_summary" not in text, p.name


# ---------------------------------------------------------------------------
# The sidebar summary, case by case
# ---------------------------------------------------------------------------

def test_case_1_approval_appears_in_the_sidebar(fresh):
    conn = fresh("full")
    decision.record_decision(conn, "WAL-ONB-0001", "analyst.test", "analyst", "approve",
                             "demo_decision", "clean freelancer", [], kb=KB)
    latest, state = summary(conn, "WAL-ONB-0001")
    assert (latest["status"], latest["template_id"]) == ("sent", "TPL-0007")
    assert latest["subject"] == "Approval notification"
    assert latest["delivery"] == "Simulated - sent to demo outbox"
    assert state["text"] == "Closed - approved" and state["date"] == latest["at"][:10]
    rail = render.page("x", "", "Case detail", conn=conn, case_id="WAL-ONB-0001")
    assert "Approval notification" in rail and " UTC" in rail
    assert render.tab_href("WAL-ONB-0001", render.MESSAGES_TAB, "messages") in rail


def test_case_2_no_messages_then_the_request(fresh):
    conn = fresh("reset")
    latest, state = summary(conn, "WAL-ONB-0002")
    assert latest is None and state["text"] == "Open - waiting on customer"
    data.send_message(conn, "WAL-ONB-0002", "resubmission", "analyst.test")
    latest, _ = summary(conn, "WAL-ONB-0002")
    assert (latest["status"], latest["template_id"]) == ("sent", "TPL-0001")


def test_case_6_shows_the_generic_message(fresh):
    conn = fresh("full")
    latest, state = summary(conn, "WAL-ONB-0006")
    assert (latest["status"], latest["template_id"]) == ("sent", "TPL-0004")
    assert not communication.scan(latest["snippet"])
    assert state["open"] and state["text"].startswith("Open - waiting on ")


def test_case_12_awaiting_compliance_approval_until_it_is_given(fresh):
    conn = fresh("reset")
    before = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    decision.record_decision(conn, "WAL-ONB-0012", "compliance.test", "compliance", "escalate",
                             "demo_decision", "confirmed match", [],
                             escalation_target="mlro.queue", kb=KB)
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == before, \
        "the decision alone sends nothing"
    latest, state = summary(conn, "WAL-ONB-0012")
    assert latest["status"] == "awaiting compliance approval" and latest["template_id"] is None
    assert state["text"] == "Open - waiting on compliance"

    data.send_message(conn, "WAL-ONB-0012", "manual_review_underway", "compliance.test")
    latest, _ = summary(conn, "WAL-ONB-0012")
    assert (latest["status"], latest["template_id"]) == ("sent", "TPL-0004")


def test_case_14_closed_no_response(fresh):
    conn = fresh("full")
    latest, state = summary(conn, "WAL-ONB-0014")
    assert state["text"] == "Closed - closed: no response"
    closed = conn.execute("SELECT timestamp FROM audit_event WHERE case_id = 'WAL-ONB-0014'"
                          " AND action = 'case_closed_no_response'").fetchone()[0]
    assert state["date"] == closed[:10]
    assert (latest["status"], latest["template_id"]) == ("sent", "TPL-0012")
    rail = render.page("x", "", "Case detail", conn=conn, case_id="WAL-ONB-0014")
    assert re.search(r"Closed - closed: no response &middot; \d{4}-\d{2}-\d{2}", rail)

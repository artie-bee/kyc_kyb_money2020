"""
Sending a message by hand - the Request / Send reminder button on a checklist
row, the only place it is offered now that the Decision tab records decisions
only. The server's rules still stand whatever is posted: outcome messages go
with the decision, a reminder needs something owed, a decided case takes no
further message. The result shows in the page banner and in Message history.

Run: python -m pytest tests/test_send_message_feedback.py -q
"""

import re
import sys
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from web import render, server as console_server                      # noqa: E402

CASE = "WAL-ONB-0002"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


@pytest.fixture()
def site(tmp_path):
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
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
                return r.status, r.headers.get("Location"), r.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Location"), err.read().decode()
    yield go
    httpd.shutdown()
    httpd.server_close()
    console_server.drop_connection()


def messages_section(page):
    """The decision section and Message history below it."""
    return page.split('<section id="decision">')[1]


def banner(page):
    """The page-wide note a posted action leaves at the top of the main area."""
    return page.split('<main class="main">')[1].split("<h2>")[0]


def send(go, situation, case_id=CASE):
    """Post as the checklist row does: back to the Checklist tab."""
    status, where, _ = go("/action/send-message", {
        "case_id": case_id, "situation": situation,
        "back": render.tab_href(case_id, "Checklist")})
    assert status == 303, where
    return where, go(where)[2]


def test_the_decision_tab_no_longer_has_a_message_form(site):
    section = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    assert "Send a message only" not in section and 'id="send"' not in section
    assert 'action="/action/send-message"' not in section
    assert "use its row on the Checklist tab" in section


def test_a_request_from_the_checklist_is_sent_and_shown(site):
    where, page = send(site, "resubmission")
    comm = re.search(r"Message%20(COM-\d+)", where).group(1)
    assert comm in banner(page) and "TPL-0001" in banner(page)
    history = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    assert comm in history


def test_sending_the_same_situation_again_is_still_allowed(site):
    send(site, "status_update")
    send(site, "status_update")
    conn = console_server._conn
    assert conn.execute("SELECT COUNT(*) FROM communication WHERE case_id = ? AND"
                        " situation = 'status_update'", (CASE,)).fetchone()[0] == 2


def test_a_decided_case_refuses_a_message(site):
    conn = console_server._conn
    from orchestrator.kb import KnowledgeBase
    from orchestrator.steps import decision
    decision.record_decision(conn, "WAL-ONB-0010", "analyst.test", "analyst", "approve",
                             "all_mandatory_checks_passed", "clean", [], kb=KnowledgeBase())
    conn.commit()
    where, _ = send(site, "status_update", "WAL-ONB-0010")
    assert "err=" in where and "decided" in where
    assert conn.execute("SELECT COUNT(*) FROM communication WHERE case_id = 'WAL-ONB-0010'"
                        " AND situation = 'status_update'").fetchone()[0] == 0


@pytest.mark.parametrize("outcome", ["case_closed", "final_outcome_approved",
                                     "final_outcome_declined"])
def test_an_outcome_message_is_refused_even_if_posted(site, outcome):
    where, page = send(site, outcome)
    assert "err=" in where and "not sent by hand" in banner(page)
    conn = console_server._conn
    assert not conn.execute("SELECT 1 FROM communication WHERE case_id = ? AND situation = ?",
                            (CASE, outcome)).fetchone()


def test_a_reminder_lists_what_is_owed_and_is_refused_when_nothing_is(site):
    # Case 2 owes two documents: the reminder names them
    send(site, "reminder")
    body = console_server._conn.execute(
        "SELECT rendered_text FROM communication WHERE case_id = ? AND situation = 'reminder'",
        (CASE,)).fetchone()[0]
    assert "ubo declaration" in body and "id document for Denton Halliwell" in body
    # Case 1 owes nothing: no reminder, and the page says why
    where, page = send(site, "reminder", "WAL-ONB-0001")
    assert "err=" in where and "Nothing is outstanding from this customer" in banner(page)


def test_no_choice_is_refused_not_sent(site):
    status, where, _ = site("/action/send-message", {
        "case_id": CASE, "back": render.tab_href(CASE, "Communications")})
    assert status == 303 and "err=" in where and "Choose%20a%20situation" in where


# ---------------------------------------------------------------------------
# The decision form's one optional Comments box
# ---------------------------------------------------------------------------

def decide(go, **fields):
    fields = {"case_id": CASE, "rationale": "recorded by a test",
              "back": render.tab_href(CASE, render.MESSAGES_TAB), **fields}
    status, where, _ = go("/action/record-decision", fields)
    assert status == 303, where
    return where


def test_the_form_has_one_optional_comments_box(site):
    section = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    assert 'name="comments"' in section and "Comments (optional)" in section
    assert 'name="override_reason"' not in section and 'name="escalation_target"' not in section
    assert "Only when they apply" not in section


def test_an_override_takes_its_reason_from_comments(site):
    # Case 2 owes documents, so the recommendation is request_more_information.
    where = decide(site, choice="withdrawn", reason_code="applicant_withdrew")
    assert "err=" in where and "Say%20why%20in%20Comments" in where
    where = decide(site, choice="withdrawn", reason_code="applicant_withdrew",
                   comments="The customer emailed to stop the application")
    assert "err=" not in where, where
    row = console_server._conn.execute(
        "SELECT override_flag, override_reason FROM human_decision WHERE case_id = ?",
        (CASE,)).fetchone()
    assert row["override_flag"] == 1
    assert row["override_reason"] == "The customer emailed to stop the application"


def test_a_comment_on_the_recommended_decision_is_kept_with_the_rationale(site):
    where = decide(site, choice="request_more_information",
                   reason_code="required_document_outstanding", comments="Also asked by phone")
    assert "err=" not in where, where
    row = console_server._conn.execute(
        "SELECT rationale, override_flag, override_reason FROM human_decision"
        " WHERE case_id = ?", (CASE,)).fetchone()
    assert row["rationale"].endswith("Comment: Also asked by phone")
    assert row["override_flag"] == 0 and row["override_reason"] is None


def test_a_refused_decision_is_said_beside_the_form(site):
    section = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    assert '<textarea name="rationale" required' in section, "the browser asks for it first"
    where = decide(site, choice="request_more_information",
                   reason_code="required_document_outstanding", rationale="")
    assert "err=" in where and "rationale" in where
    section = messages_section(site(where)[2])
    decide_at = section.index('class="grid2 decide"')
    assert "Not recorded" in section[:decide_at] and "needs a rationale" in section[:decide_at]
    assert console_server._conn.execute(
        "SELECT COUNT(*) FROM human_decision WHERE case_id = ?", (CASE,)).fetchone()[0] == 0

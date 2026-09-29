"""
Send a message says, where it is used, that it has sent: a confirmation beside
the button, the new message marked "Just sent", and every situation already
sent to the customer named in the dropdown. Sending again is still allowed.

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
    return page.split('<section id="messages">')[1].split("</section>")[0]


def send(go, situation):
    status, where, _ = go("/action/send-message", {
        "case_id": CASE, "situation": situation,
        "back": render.tab_href(CASE, "Communications")})
    assert status == 303 and where.endswith("#messages"), where
    return where, messages_section(go(where.split("#")[0])[2])


def test_before_anything_is_sent_nothing_says_already_sent(site):
    section = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    assert "already sent" not in section and "Just sent" not in section
    assert "<form" in section and "data-once" in section, "the button locks on click"


def test_after_sending_the_confirmation_is_beside_the_button(site):
    where, section = send(site, "resubmission")
    comm = re.search(r"Message%20(COM-\d+)", where).group(1)
    form_at = section.index("<h3>Send a message</h3>")
    done = section[form_at:]
    assert "Sent" in done and comm in done and "TPL-0001" in done, \
        "the confirmation sits under the Send a message heading, next to the button"
    assert "Simulated - sent to demo outbox" in done
    assert section.count('<span class="justsent">') == 1 and comm in section[:form_at]
    assert re.search(r'<details class="doc doc--fresh" open>', section), "opened and marked"


def test_the_dropdown_and_list_say_which_situations_already_went(site):
    send(site, "resubmission")
    where, section = send(site, "status_update")
    options = dict(re.findall(r'<option value="([a-z_]+)">([^<]+)</option>', section))
    assert "already sent" in options["resubmission"]
    assert "already sent" in options["status_update"]
    assert "already sent" not in options["verification_delay"]
    listed = re.search(r'<p class="sentlist">(.*?)</p>', section, re.S).group(1)
    assert "resubmission" in listed and "status_update" in listed
    # a reload without the outcome in the address keeps the list, drops "Just sent"
    plain = messages_section(site(where.split("?")[0] + "?tab=" + render.MESSAGES_TAB)[2])
    assert "Just sent" not in plain and "already sent" in plain


def test_sending_the_same_situation_again_is_still_allowed(site):
    send(site, "status_update")
    _, section = send(site, "status_update")
    assert section.count('<span class="justsent">') == 1
    assert len(re.findall(r"situation status_update", section)) == 2


# ---------------------------------------------------------------------------
# Outcome messages are not sent by hand; a reminder needs something owed
# ---------------------------------------------------------------------------

def test_the_situation_is_filled_in_from_the_case_and_no_outcome_is_offered(site):
    """Case 2 owes two documents and has not been asked: resubmission, with
    the exact text shown before anything is sent."""
    section = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    select = re.search(r'<select name="situation" required data-send-previews>(.*?)</select>',
                       section, re.S).group(1)
    assert '<option value="resubmission" selected>resubmission (suggested)' in select
    assert '<option value="" disabled>Choose a situation…</option>' in select
    values = re.findall(r'<option value="([a-z_]+)"', select)
    for outcome in ("case_closed", "final_outcome_approved", "final_outcome_declined"):
        assert outcome not in values
    assert {"resubmission", "reminder", "status_update"} <= set(values)
    assert "Suggested:</strong> resubmission" in section
    shown = re.search(r'<div class="preview" data-send-preview-for="resubmission">(.*?)</div></div>',
                      section, re.S).group(1)
    assert "ubo declaration" in shown and "id document for Denton Halliwell" in shown
    others = re.findall(r'data-send-preview-for="([a-z_]+)" hidden', section)
    assert "resubmission" not in others and "status_update" in others


def test_the_suggestion_moves_on_as_the_case_does(site):
    send(site, "resubmission")
    section = messages_section(site(render.tab_href(CASE, render.MESSAGES_TAB))[2])
    assert '<option value="reminder" selected>reminder (suggested)' in section
    one = messages_section(site(render.tab_href("WAL-ONB-0001", render.MESSAGES_TAB))[2])
    assert '<option value="status_update" selected>status_update (suggested)' in one
    six = messages_section(site(render.tab_href("WAL-ONB-0006", render.MESSAGES_TAB))[2])
    assert ('<option value="manual_review_underway" selected>manual_review_underway '
            "(suggested)") in six, "restricted: the generic holding message"


def test_a_decided_case_offers_no_form_and_refuses_a_message(site):
    conn = console_server._conn
    from orchestrator.kb import KnowledgeBase
    from orchestrator.steps import decision
    decision.record_decision(conn, "WAL-ONB-0010", "analyst.test", "analyst", "approve",
                             "demo_decision", "clean", [], kb=KnowledgeBase())
    conn.commit()
    section = messages_section(site(render.tab_href("WAL-ONB-0010", render.MESSAGES_TAB))[2])
    assert 'action="/action/send-message"' not in section
    assert "went out with the decision" in section
    status, where, _ = site("/action/send-message", {
        "case_id": "WAL-ONB-0010", "situation": "status_update",
        "back": render.tab_href("WAL-ONB-0010", "Communications")})
    assert "err=" in where and "decided" in where
    assert conn.execute("SELECT COUNT(*) FROM communication WHERE case_id = 'WAL-ONB-0010'"
                        " AND situation = 'status_update'").fetchone()[0] == 0


@pytest.mark.parametrize("outcome", ["case_closed", "final_outcome_approved",
                                     "final_outcome_declined"])
def test_an_outcome_message_is_refused_even_if_posted(site, outcome):
    status, where, _ = site("/action/send-message", {
        "case_id": CASE, "situation": outcome, "back": render.tab_href(CASE, "Communications")})
    assert status == 303 and "err=" in where
    section = messages_section(site(where.split("#")[0])[2])
    assert "Not sent" in section and "not sent by hand" in section
    conn = console_server._conn
    assert not conn.execute("SELECT 1 FROM communication WHERE case_id = ? AND situation = ?",
                            (CASE, outcome)).fetchone()


def test_a_reminder_lists_what_is_owed_and_is_refused_when_nothing_is(site):
    # Case 2 owes two documents: the reminder names them
    _, section = send(site, "reminder")
    body = re.search(r'<details class="doc doc--fresh" open>.*?<p>(.*?)</p>', section,
                     re.S).group(1)
    assert "ubo declaration" in body and "id document for Denton Halliwell" in body
    # Case 1 owes nothing: no reminder, and the reason is beside the button
    status, where, _ = site("/action/send-message", {
        "case_id": "WAL-ONB-0001", "situation": "reminder",
        "back": render.tab_href("WAL-ONB-0001", "Communications")})
    assert "err=" in where
    section = messages_section(site(where.split("#")[0])[2])
    assert "Nothing is outstanding from this customer" in section


def test_no_choice_is_refused_not_sent(site):
    status, where, _ = site("/action/send-message", {
        "case_id": CASE, "back": render.tab_href(CASE, "Communications")})
    assert status == 303 and "err=" in where and "Choose%20a%20situation" in where

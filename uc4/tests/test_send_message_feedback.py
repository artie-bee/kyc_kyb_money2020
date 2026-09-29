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
    assert "already sent" not in options["case_closed"]
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

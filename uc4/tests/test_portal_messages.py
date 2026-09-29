"""
The portal's messages: an unread count on the Messages tab, a card that pops
up for a new message (and keeps up while the page is open), and an inbox that
marks what is new - all built from the messages actually sent, with no
wording of its own, and every page still passing the leak check.

Run: python -m pytest tests/test_portal_messages.py -q
"""

import json
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
from app.customer_view import leaks, snippet                          # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import decision                              # noqa: E402
from portal import server as portal_server                            # noqa: E402

CASE = "WAL-ONB-0001"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


@pytest.fixture()
def portal(tmp_path):
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    httpd = portal_server.serve(0, db_path=path, demo=True, uploads_dir=tmp_path / "up")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    class Customer:
        def __init__(self):
            self.opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect())

        def get(self, p):
            try:
                with self.opener.open(base + p) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as err:
                return err.code, err.read().decode()

        def open_as(self, case_id):
            req = urllib.request.Request(base + "/demo/open",
                                         urlencode({"case_id": case_id}).encode())
            try:
                self.opener.open(req)
            except urllib.error.HTTPError:
                pass
            return self

    yield {"path": path, "customer": Customer}
    httpd.shutdown()
    httpd.server_close()


def approve(path):
    conn = data.connect(path)
    decision.record_decision(conn, CASE, "analyst.test", "analyst", "approve",
                             "all_mandatory_checks_passed", "clean", [], kb=KnowledgeBase())
    conn.commit()
    comm = conn.execute("SELECT MAX(communication_id) FROM outbox WHERE case_id = ?",
                        (CASE,)).fetchone()[0]
    conn.close()
    return comm


def badge(page):
    m = re.search(r'<span class="pnav__badge" data-unread="(\d+)"( hidden)?', page)
    return int(m.group(1)), bool(m.group(2))


def test_no_messages_no_badge_and_no_card(portal):
    c = portal["customer"]().open_as(CASE)
    page = c.get("/")[1]
    assert badge(page) == (0, True), "the count is in the page, hidden at zero"
    assert 'class="toast"' not in page


def test_an_unread_message_shows_a_count_and_a_card_until_it_is_read(portal):
    comm = approve(portal["path"])
    c = portal["customer"]().open_as(CASE)
    home = c.get("/")[1]
    assert badge(home) == (1, False), "one unread message"
    card = re.search(r'<aside class="toast".*?</aside>', home, re.S).group(0)
    assert f'data-toast="{comm}"' in card and 'href="/messages">Read message' in card
    assert "New message from Wallester" in card and "has been approved" in card
    assert 'class="toast__close" type="button" aria-label="Dismiss" hidden' in card, \
        "the dismiss button appears only when the script runs"
    assert badge(c.get("/checklist")[1]) == (1, False), "on every page but Messages"

    inbox = c.get("/messages")[1]
    assert 'class="toast"' not in inbox and badge(inbox) == (0, True)
    assert "1 message &middot; 1 new" in inbox
    assert re.search(r'<li class="msg">.*?<span class="newtag">New</span>', inbox, re.S)

    # opening Messages marked it read: the count and the card are gone
    after = c.get("/")[1]
    assert badge(after) == (0, True) and 'class="toast"' not in after
    assert "New</span>" not in c.get("/messages")[1]


def test_the_live_check_counts_without_marking_read(portal):
    c = portal["customer"]().open_as(CASE)
    assert json.loads(c.get("/api/unread")[1]) == {"signed_in": True, "unread": 0,
                                                   "newest": None}
    comm = approve(portal["path"])
    for _ in range(2):                                  # asking twice changes nothing
        status, body = c.get("/api/unread")
        answer = json.loads(body)
        assert status == 200 and answer["unread"] == 1
        assert answer["newest"]["id"] == comm and "has been approved" in answer["newest"]["text"]
    c.get("/messages")
    assert json.loads(c.get("/api/unread")[1])["unread"] == 0
    stranger = portal["customer"]()
    assert stranger.get("/api/unread")[0] == 401, "signed out: nothing to tell"


def test_the_page_asks_for_updates_only_when_signed_in(portal):
    c = portal["customer"]()
    assert 'data-poll="1"' not in c.get("/demo")[1]
    c.open_as(CASE)
    assert '<body data-poll="1">' in c.get("/")[1]


@pytest.mark.parametrize("case_id", ["WAL-ONB-0005", "WAL-ONB-0006", "WAL-ONB-0012",
                                     "WAL-ONB-0014"])
def test_cards_previews_and_the_live_check_never_leak(portal, case_id):
    conn = data.connect(portal["path"])
    data.send_message(conn, case_id, "manual_review_underway", "analyst.test")
    conn.close()
    c = portal["customer"]().open_as(case_id)
    for p in ("/", "/checklist", "/messages", "/api/unread"):
        status, body = c.get(p)
        assert status in (200,), (case_id, p, status)
        assert not leaks(body), (case_id, p, leaks(body))


def test_the_preview_is_the_messages_own_words():
    text = ("Hello Kaari Mets, good news. The Wallester Business account for Kaari Mets "
            "(reference WAL-ONB-0001) has been approved.")
    preview = snippet(text)
    assert preview.startswith("Good news. The Wallester Business account")
    assert preview[:-1].lower() in text.lower() or preview.lower() in text.lower()

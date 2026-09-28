"""
The portal's demo selector: a start card, a search, and every demo customer
grouped by what they need - all in customer-safe words.

Run: python -m pytest tests/test_portal_demo_selector.py -q
"""

import re
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from app.customer_view import DEMO_GROUPS, demo_card, leaks          # noqa: E402
from orchestrator import db                                           # noqa: E402
from portal import server as portal_server                            # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("selector")
    paths = {}
    for name, stop in (("reset", True), ("full", False)):
        conn = db.connect(folder / f"{name}.db")
        run_demo(conn, verbose=False, stop_before_human_actions=stop)
        conn.commit()
        conn.close()
        paths[name] = folder / f"{name}.db"
    return paths


@pytest.fixture()
def portal(built, tmp_path):
    servers = []

    def start(name):
        path = shutil.copy(built[name], tmp_path / f"{name}.db")
        httpd = portal_server.serve(0, db_path=Path(path), demo=True,
                                    uploads_dir=tmp_path / "uploads")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        return lambda p: urllib.request.urlopen(base + p).read().decode()
    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def groups(page):
    """{group heading: [case ids in it]}, as the page shows them."""
    out = {}
    for head, body in re.findall(r'<h2 class="pgroup__head">(.*?) <span.*?<ul class="picks">'
                                 r"(.*?)</ul>", page, re.S):
        out[head] = re.findall(r'name="case_id" value="([^"]+)"', body)
    return out


def test_every_customer_is_in_exactly_one_group_and_the_page_is_clean(portal):
    page = portal("reset")("/demo")
    shown = groups(page)
    assert set(shown) <= set(DEMO_GROUPS)
    ids = [c for cases in shown.values() for c in cases]
    assert len(ids) == len(set(ids)) == 14 == page.count("Open as this customer")
    assert "WAL-ONB-0002" in shown["Needs your action"]
    assert "WAL-ONB-0006" in shown["In review"]
    assert not leaks(page), leaks(page)
    assert "2 documents needed" in page, "case 2 owes two documents"
    assert "Start a new demo application" in page and 'role="search"' in page


def test_closed_customers_are_grouped_as_closed(portal):
    shown = groups(portal("full")("/demo"))
    assert "WAL-ONB-0014" in shown["Closed"], "case 14 closed for want of a reply"
    assert "WAL-ONB-0011" in shown["Closed"], "case 11 was declined"


def test_search_by_name_or_reference_works_without_the_script(portal):
    get = portal("reset")
    by_name = get("/demo?q=northbridge")
    assert by_name.count("Open as this customer") == 1 and "WAL-ONB-0002" in by_name
    by_ref = get("/demo?q=WAL-ONB-0014")
    assert by_ref.count("Open as this customer") == 1
    none = get("/demo?q=no-such-company")
    assert none.count("Open as this customer") == 0
    assert '<p class="empty" id="pick-empty">' in none, "the no-match line is shown"
    assert 'value="no-such-company"' in none and 'href="/demo">Clear' in none


def test_a_card_says_what_the_customer_would_see_first(built):
    conn = data.connect(built["reset"])
    four = demo_card(conn, "WAL-ONB-0004")
    assert four["group"] == "Needs your action"
    assert four["status_text"] == "Upload your source of wealth statement"
    one = demo_card(conn, "WAL-ONB-0001")
    assert one["group"] == "In review" and one["still_needed"] == 0
    assert one["last_message"] is None
    conn.close()

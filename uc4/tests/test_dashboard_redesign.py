"""
The redesigned operations dashboard: action cards, the "Needs attention" list,
and two inline-SVG charts - every count adds up to the open cases, every card,
slice, legend row and bar is a plain link that filters the table, and the page
needs no script.

Run: python -m pytest tests/test_dashboard_redesign.py -q
"""

import html
import re
import sys
import threading
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import db                                           # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render, server as console_server                      # noqa: E402


@pytest.fixture(scope="module", params=["reset", "full"])
def conn(request):
    """The demo after Reset (nothing decided) and the dataset's full run
    (decisions recorded, case 14 closed) - the counts must hold for both."""
    c = db.connect(":memory:")
    run_demo(c, verbose=False, stop_before_human_actions=request.param == "reset")
    return c


def open_rows(conn):
    return [r for r in data.dashboard(conn) if r["group"] != "closed"]


def listed(page):
    return re.findall(r'<tr class="rowlink" data-href="/case/([^"]+)"', page)


def follow(conn, href):
    """Render the dashboard at a link's address, as the server would."""
    return render.dashboard(conn, parse_qs(urlparse(html.unescape(href)).query))


def test_the_counts_add_up_to_the_open_cases(conn):
    rows = open_rows(conn)
    page = render.dashboard(conn)
    cards = [int(v) for v in re.findall(r'class="card__v"><span class="num">(\d+)</span>', page)]
    ready, white = map(int, re.search(r"(\d+) cases? (?:is|are) ready for a decision</a>"
                                      r" &middot; <a[^>]*>(\d+) on the white-label", page).groups())
    assert len(cards) == 3 and sum(cards) + ready + white == len(rows), \
        "every open case is in exactly one group"
    donut = [int(v) for v in re.findall(r'legend__v num">(\d+)<', page)]
    bars = [int(v) for v in re.findall(r'bars__v num">(\d+)<', page)]
    assert sum(donut) == sum(bars) == len(rows)
    assert re.search(r'class="donut__n" x="60" y="60">' + str(len(rows)) + "<", page), \
        "the number of open cases in the centre"
    # and they match the table
    attention = listed(page)
    assert sorted(attention) == sorted(r["case_id"] for r in rows if r["alert_at"])
    assert sum(cards) == len(attention)


def test_every_card_slice_legend_row_bar_and_count_filters_to_exactly_its_cases(conn):
    rows = {r["case_id"]: r for r in data.dashboard(conn)}
    live = [r for r in rows.values() if r["group"] != "closed"]
    page = render.dashboard(conn)
    hrefs = set(re.findall(r'href="(/\?[^"]+)"', page))
    checked = 0
    for href in hrefs:
        q = {k: v[0] for k, v in parse_qs(urlparse(html.unescape(href)).query).items()}
        shown = sorted(listed(follow(conn, href)))
        if q.get("need") == "oldest":
            oldest = min((r for r in live if r["alert_at"]), key=lambda r: r["alert_at"])
            expected = [oldest["case_id"]]
        elif "need" in q:
            expected = [r["case_id"] for r in live if r["group"] == q["need"]]
        elif "status" in q:
            expected = [c for c, r in rows.items() if r["status"] == q["status"]]
        elif "band" in q:
            expected = [r["case_id"] for r in live if r["band_group"] == q["band"]]
        else:
            continue                                   # "Show all", "Clear filter"
        assert shown == sorted(expected), (href, shown, expected)
        checked += 1
    assert checked >= 10, f"only {checked} filter links followed"


def test_the_attention_list_is_oldest_alert_first_with_plain_reasons(conn):
    page = render.dashboard(conn)
    keys = re.findall(r'<td class="cell-alert" data-key="([^"]+)"', page)
    assert keys == sorted(keys), "oldest alert first"
    waiting = re.findall(r'<td class="cell-wait-for">([^<]+)', page)
    words = set(data.kb().hold_reason_wording.values())
    assert waiting and all(w.strip() in words for w in waiting), \
        "every reason is the KB's plain wording, never a hold code"
    assert data.kb().hold_reason_wording["insufficient_evidence"] == "Missing evidence"


def test_restricted_cases_keep_their_tag(conn):
    page = render.dashboard(conn)
    for r in data.dashboard(conn):
        if r["restricted"] and r["alert_at"] and r["group"] != "closed":
            row = re.search(r'data-href="/case/' + r["case_id"] + r'">.*?</tr>', page,
                            re.S).group(0)
            assert 'class="restricted"' in row, r["case_id"]
    all_view = render.dashboard(conn, {"view": ["all"]})
    assert all_view.count('class="restricted"') == sum(1 for r in data.dashboard(conn)
                                                       if r["restricted"])


def test_show_all_keeps_the_existing_columns_and_filters(conn):
    page = render.dashboard(conn, {"view": ["all"]})
    assert len(listed(page)) == len(data.dashboard(conn))
    for legend in ("Status", "Next action owner", "Follow-up"):
        assert "<legend>" + legend + "</legend>" in page
    for col in ("Type", "Owner", "Score", "Holds", "Hold detail", "Alert came in"):
        assert ">" + col in page


def test_the_page_works_without_javascript(conn):
    page = render.page("Operations dashboard", render.dashboard(conn), "Operations dashboard",
                       conn=conn)
    main = page.split('<main class="main">')[1].split("</main>")[0]
    assert "<script" not in main and not re.search(r"\son[a-z]+=", main), \
        "no script in the dashboard itself"
    assert "<svg" in main and '<a href="/?view=all&amp;status=' in main, \
        "the chart slices are plain links"
    assert '<a class="card" href="/?need=analyst">' in main, "so are the cards"
    # the filters work from the address alone, which is what a browser with
    # scripts off sends when a link is followed
    assert listed(render.dashboard(conn, {"need": ["customer"]})) == \
        [r["case_id"] for r in sorted(open_rows(conn), key=lambda r: r["alert_at"])
         if r["group"] == "customer"]


def test_the_server_serves_every_view(tmp_path):
    console_server.drop_connection()
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    console_server._conn = data.connect(path)
    httpd = console_server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for p in ("/", "/?need=analyst", "/?need=oldest", "/?view=all",
                  "/?view=all&status=analyst_review_required", "/?view=all&band=not+scored"):
            with urllib.request.urlopen(base + p) as r:
                assert r.status == 200, p
                text = r.read().decode()
            assert "Operations dashboard" in text and "Cases by status" in text, p
        with urllib.request.urlopen(base + "/?need=analyst") as r:
            assert "Showing: <strong>Needs analyst</strong>" in r.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()
        console_server.drop_connection()

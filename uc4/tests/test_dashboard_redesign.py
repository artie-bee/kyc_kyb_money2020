"""
The operations dashboard, light Datadog-style layout: four coloured tiles, a
charts row (status bars, risk-band donut), the hold strip and the case table.
Every count adds up and matches the table; every tile, bar, legend row and
strip block is a plain link that filters; colours come only from the shared
role variables; tiles meet WCAG AA; light is the default; no script needed.

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

CSS = (ROOT / "web" / "static" / "app.css").read_text(encoding="utf-8")
JS = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")


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


def nums(page, cls):
    return [int(v) for v in re.findall(r'class="' + cls + r'(?: num)?">(\d+)<', page)]


# ---------------------------------------------------------------------------
# The counts
# ---------------------------------------------------------------------------

def test_tiles_bars_donut_and_strip_add_up_to_the_open_cases(conn):
    live = open_rows(conn)
    page = render.dashboard(conn)
    tiles = nums(page, "tile__v")
    white = int(re.search(r"(\d+) on the white-label branch", page).group(1))
    assert len(tiles) == 4 and sum(tiles) + white == len(live), \
        "every open case is in exactly one tile, or on the white-label branch"
    assert sum(nums(page, "hbar__v")) == len(live), "status bars"
    assert sum(nums(page, "legend__v")) == len(live), "donut legend"
    assert re.search(r'class="donut__n" x="60" y="60">' + str(len(live)) + "<", page)
    with_holds, no_holds, restricted = nums(page, "strip__v")
    assert with_holds + no_holds == len(live)
    assert restricted == sum(1 for r in live if r["restricted"])
    # and they match the table
    assert sorted(listed(page)) == sorted(r["case_id"] for r in data.dashboard(conn))
    assert with_holds == sum(1 for r in live if r["open_holds"])


def test_every_tile_bar_legend_row_and_strip_block_filters_to_exactly_its_cases(conn):
    rows = {r["case_id"]: r for r in data.dashboard(conn)}
    live = [r for r in rows.values() if r["group"] != "closed"]
    page = render.dashboard(conn)
    checked = 0
    for href in sorted(set(re.findall(r'href="(/\?[^"]+)"', page))):
        q = {k: v[0] for k, v in parse_qs(urlparse(html.unescape(href)).query).items()}
        shown = sorted(listed(follow(conn, href)))
        if q.get("need") == "oldest":
            expected = [min((r for r in live if r["alert_at"]),
                            key=lambda r: r["alert_at"])["case_id"]]
        elif "need" in q:
            expected = [r["case_id"] for r in live if r["group"] == q["need"]]
        elif "status" in q:
            expected = [c for c, r in rows.items() if r["status"] == q["status"]]
        elif "band" in q:
            expected = [r["case_id"] for r in live if r["band_group"] == q["band"]]
        elif "holds" in q:
            expected = [r["case_id"] for r in live
                        if bool(r["open_holds"]) == (q["holds"] == "with")]
        elif "restricted" in q:
            expected = [r["case_id"] for r in live if r["restricted"]]
        else:
            continue
        assert shown == sorted(expected), (href, shown, expected)
        checked += 1
    assert checked >= 14, f"only {checked} filter links followed"


def test_the_table_keeps_its_columns_is_oldest_first_and_has_plain_words(conn):
    page = render.dashboard(conn)
    head = re.findall(r'<th scope="col"[^>]*>(\w[\w ]*)', page.split('id="cases"')[1])
    assert head == ["Case", "Status", "Owner", "Band", "Score", "Holds", "Hold detail",
                    "Alert came in"]
    keys = [k for k in re.findall(r'<td class="cell-alert" data-key="([^"]+)"', page)]
    alerted = [k for k in keys if k != "~"]
    assert alerted == sorted(alerted) and keys[len(alerted):] == ["~"] * (len(keys)
                                                                          - len(alerted))
    body = page.split('id="cases"')[1]
    for status in render.STATUS_WORDS:
        assert ">" + status + "<" not in body, "status in plain words, not its code"
    details = re.findall(r'<td class="cell-detail">([^<]+)</td>', body)
    words = set(data.kb().hold_reason_wording.values())
    for d in details:
        for part in d.split("; "):
            reason, owner = re.match(r"(.+) \((\w+)\)$", html.unescape(part)).groups()
            assert reason in words and owner in ("analyst", "compliance", "customer")
    # the existing filters are kept
    for legend in ("Status", "Next action owner", "Follow-up"):
        assert "<legend>" + legend + "</legend>" in page


def test_restricted_cases_keep_their_tag(conn):
    page = render.dashboard(conn)
    for r in data.dashboard(conn):
        row = re.search(r'data-href="/case/' + r["case_id"] + r'".*?</tr>', page, re.S).group(0)
        assert ('class="restricted"' in row) == bool(r["restricted"]), r["case_id"]


# ---------------------------------------------------------------------------
# Colour, contrast and theme
# ---------------------------------------------------------------------------

def _block(selector):
    body = CSS.split(selector + " {", 1)[1]
    return body.split("\n}", 1)[0]


def _var(block, name):
    return re.search(r"--" + name + r":\s*(#[0-9a-fA-F]{6})", block).group(1)


def _lum(hexcolour):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(hexcolour[i:i + 2]) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", [":root", ':root[data-theme="dark"]'])
@pytest.mark.parametrize("role", ["analyst", "compliance", "customer", "ready"])
def test_tile_text_meets_wcag_aa(theme, role):
    block = _block(theme)
    ratio = contrast(_var(block, f"tile-{role}-bg"), _var(block, f"tile-{role}-fg"))
    assert ratio >= 4.5, f"{theme} {role}: {ratio:.2f}"


THEMES = {":root": "#ffffff", ':root[data-theme="dark"]': "#171c22"}    # the surface
ROLES = ("analyst", "compliance", "customer", "ready", "neutral")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("role", ROLES)
def test_badge_text_meets_wcag_aa_on_its_background(theme, role):
    block = _block(theme)
    ratio = contrast(_var(block, f"role-{role}-ink"), _var(block, f"role-{role}-bg"))
    assert ratio >= 4.5, f"{theme} {role} badge: {ratio:.2f}"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("mark", [*ROLES, "analyst-2"])
def test_chart_marks_stand_out_from_the_page(theme, mark):
    """WCAG 1.4.11: a bar or slice that carries meaning needs 3:1 against its
    background - and every chart shows its numbers as well."""
    ratio = contrast(_var(_block(theme), f"role-{mark}"), THEMES[theme])
    assert ratio >= 3, f"{theme} {mark}: {ratio:.2f}"


def test_high_and_medium_are_told_apart():
    """Both are the analyst's amber family, but not the same colour."""
    for theme in THEMES:
        block = _block(theme)
        assert _var(block, "role-analyst") != _var(block, "role-analyst-2")


def test_the_colour_language_is_defined_once_for_both_themes():
    light, dark = _block(":root"), _block(':root[data-theme="dark"]')
    for role in ROLES:
        for part in ("", "-ink", "-bg", "-line"):
            assert re.search(r"--role-" + role + part + r":\s*#", light), (role, part)
            assert re.search(r"--role-" + role + part + r":\s*#", dark), (role, part)
    # the badge tones are those same roles' text shades, not a second palette
    for tone, role in (("warn", "analyst"), ("bad", "compliance"), ("info", "customer"),
                       ("ok", "ready")):
        assert f"--{tone}:var(--role-{role}-ink)" in light
    assert render.OWNER_TONE == {"analyst": "warn", "compliance": "bad", "customer": "info",
                                 "system": "neutral"}
    assert render.BAND_TONE["critical"] == "bad" and render.BAND_TONE["low"] == "ok"


def test_the_dashboard_uses_only_the_shared_colour_variables(conn):
    page = render.dashboard(conn)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", page), "no colour codes in the markup"
    for style in re.findall(r'style="([^"]+)"', page):
        for value in re.findall(r"(?:background|stroke|border-top-color|color):([^;]+)",
                                style):
            assert value.strip().startswith("var(--role-"), style
    dash_css = CSS.split("/* ---------- Operations dashboard")[1].split(
        "/* The case timeline")[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", dash_css), "dashboard CSS uses variables only"


def test_light_is_the_default_and_dark_still_works():
    assert "prefers-color-scheme" not in CSS, "the operating system does not pick the theme"
    assert "color-scheme: light;" in _block(":root")
    assert "color-scheme: dark;" in _block(':root[data-theme="dark"]')
    assert "matchMedia" not in JS and '"dark" ? "light" : "dark"' in JS
    page = render.page("x", "", "Operations dashboard", conn=None)
    assert 'id="theme"' in page and 'data-theme="dark"' not in page


# ---------------------------------------------------------------------------
# Without a script, and served
# ---------------------------------------------------------------------------

def test_the_page_works_without_javascript(conn):
    page = render.page("Operations dashboard", render.dashboard(conn), "Operations dashboard",
                       conn=conn)
    main = page.split('<main class="main">')[1].split("</main>")[0]
    assert "<script" not in main and not re.search(r"\son[a-z]+=", main)
    assert '<a class="tile tile--analyst" href="/?need=analyst">' in main
    assert '<svg class="donut"' in main and '<a href="/?band=' in main
    assert '<a class="strip__block" href="/?holds=with"' in main
    # the address alone filters: what a browser sends when a link is followed
    assert sorted(listed(render.dashboard(conn, {"need": ["customer"]}))) == \
        sorted(r["case_id"] for r in open_rows(conn) if r["group"] == "customer")


def test_the_header_shows_the_demo_clock_and_the_oldest_alert(conn):
    page = render.dashboard(conn)
    live = [r for r in open_rows(conn) if r["alert_at"]]
    oldest = min(live, key=lambda r: r["alert_at"])
    assert "Demo clock - not real time: " in page
    assert "Oldest alert: " + data.ago(oldest["alert_at"]) in page


def test_the_server_serves_every_view(tmp_path):
    console_server.drop_connection()
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    console_server._conn = data.connect(path)
    httpd = console_server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for p in ("/", "/?need=analyst", "/?need=oldest", "/?need=ready", "/?holds=none",
                  "/?restricted=1", "/?status=analyst_review_required", "/?band=not+scored",
                  "/?view=all"):
            with urllib.request.urlopen(base + p) as r:
                assert r.status == 200, p
                text = r.read().decode()
            assert "Cases by status" in text and "Hold status" in text, p
        with urllib.request.urlopen(base + "/?need=analyst") as r:
            assert "Showing: <strong>Needs analyst</strong>" in r.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()
        console_server.drop_connection()

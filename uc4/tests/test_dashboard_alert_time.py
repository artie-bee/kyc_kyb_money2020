"""
The dashboard's single time column: when the current alert came in, and how
long ago on the injected clock (so it follows the demo clock).

Run: python -m pytest tests/test_dashboard_alert_time.py -q
"""

import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import clock, db                                    # noqa: E402
from orchestrator.clock import DEMO_START                             # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication, decision               # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render                                                # noqa: E402

KB = KnowledgeBase()
PLACED = "2026-09-28T09:00:00Z"        # every hold in the reset demo


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("alert_time") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def conn(built, tmp_path):
    c = data.connect(shutil.copy(built, tmp_path / "onboarding.db"))
    yield c
    c.close()


def cell(page, case_id):
    row = re.search(r'<tr class="rowlink" data-href="/case/' + case_id + r'".*?</tr>',
                    page, re.S).group(0)
    return re.search(r'<td class="cell-alert"[^>]*>(.*?)</td>', row, re.S).group(1)


def text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).replace("&middot;", "·").split())


@pytest.mark.parametrize("after, expected", [
    (timedelta(minutes=25), "25 min ago"),
    (timedelta(hours=1), "1 hr ago"),
    (timedelta(hours=3), "3 hrs ago"),
    (timedelta(days=1), "1 day ago"),
    (timedelta(days=3), "3 days ago"),
])
def test_the_format_at_each_step(conn, after, expected):
    with clock.use(clock.FakeClock(DEMO_START + after)):
        shown = text(cell(render.dashboard(conn, {"view": "all"}), "WAL-ONB-0002"))
    assert shown == "2026-09-28 09:00 UTC · " + expected


def test_advancing_the_demo_clock_one_day_turns_3_hrs_into_1_day(conn):
    with clock.use(clock.FakeClock(DEMO_START + timedelta(hours=3))):
        assert text(cell(render.dashboard(conn, {"view": "all"}), "WAL-ONB-0006")).endswith("3 hrs ago")
        data.advance_demo_clock(conn, 1, "analyst.test")
        # a console request runs on the demo clock
        with clock.use(data.demo_clock(conn)):
            shown = text(cell(render.dashboard(conn, {"view": "all"}), "WAL-ONB-0006"))
    assert shown == "2026-09-28 09:00 UTC · 1 day ago"


def test_no_open_alert_shows_a_dash_and_sorts_last(conn):
    decision.record_decision(conn, "WAL-ONB-0010", "analyst.test", "analyst", "approve",
                             "demo_decision", "clean", [], kb=KB)
    page = render.dashboard(conn, {"view": "all"})
    assert text(cell(page, "WAL-ONB-0010")) == "—"
    assert 'data-key="~"' in page and f'data-key="{PLACED}"' in page, \
        "the sort key is the stored time; no alert sorts after every time"


def test_the_removed_columns_are_gone_but_the_case_detail_keeps_the_data(conn):
    communication.send_required_message(conn, "WAL-ONB-0014", "resubmission", KB,
                                        approver="ops.test")
    page = render.dashboard(conn, {"view": "all"})
    for heading in ("Waiting on customer since", "Last message viewed", "Next reminder due"):
        assert heading not in page
    assert page.count('class="cell-alert"') == 14, "one time column"
    assert 'name="waiting" value="1"' in page, "the filter stays"
    follow = render.case_detail(conn, "WAL-ONB-0014", tab=render.MESSAGES_TAB)
    assert "View not recorded" in follow and "Next reminder due" in follow
    timeline = render.case_detail(conn, "WAL-ONB-0014", tab="Timeline")
    assert "Reminder due 2026-10-12" in timeline and "Not viewed yet" in timeline


def test_case_and_applicant_stay_in_view(conn):
    css = (ROOT / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert "#cases th:nth-child(1), #cases td:nth-child(1) { position:sticky; left:0;" in css
    assert "#cases th:nth-child(2), #cases td:nth-child(2) { position:sticky; left:150px;" in css
    head = re.findall(r"<th scope=\"col\"[^>]*>(\w[\w ]*)", render.dashboard(conn, {"view": "all"}))
    assert head[:2] == ["Case", "Applicant"] and len(head) == 10

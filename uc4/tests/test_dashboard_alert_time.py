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


def closed(page, case_id):
    row = re.search(r'<tr class="rowlink" data-href="/case/' + case_id + r'".*?</tr>',
                    page, re.S).group(0)
    return re.search(r'<td class="cell-closed"[^>]*>(.*?)</td>', row, re.S).group(1)


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


def test_a_decided_case_says_how_it_ended_and_sorts_last(conn):
    decision.record_decision(conn, "WAL-ONB-0010", "analyst.test", "analyst", "approve",
                             "all_mandatory_checks_passed", "clean", [], kb=KB)
    page = render.dashboard(conn, {"view": "all"})
    assert text(closed(page, "WAL-ONB-0010")).endswith("approve"), "how it closed"
    shown = text(cell(page, "WAL-ONB-0010"))
    assert shown.endswith("UTC") and "ago" not in shown, "when it came in; a closed alert has no age"
    assert 'data-key="~' in page and f'data-key="{PLACED}"' in page, \
        "open alerts sort by their time; closed ones after every open one"


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


def test_the_case_column_comes_first_and_the_table_is_compact(conn):
    """The applicant and case id share the first column, so the table fits a
    laptop without a sideways scroll (checked by eye at 1366 px)."""
    head = re.findall(r"<th scope=\"col\"[^>]*>(\w[\w ]*)",
                      render.dashboard(conn, {"view": "all"}).split('id="cases"')[1])
    assert head[0] == "Case" and len(head) == 10 and "Applicant" not in head
    assert head[-3:] == ["Alert came in", "Alert closed", "Acknowledged "], head[-3:]


def test_a_case_ready_for_decision_shows_when_it_became_ready(conn):
    # No hold is open, but the case waits on a person: its alert is the moment
    # its evidence pack was assembled. Once decided, the dash returns.
    row = conn.execute("SELECT status FROM onboarding_case WHERE case_id = 'WAL-ONB-0010'"
                       ).fetchone()
    assert row["status"] == "ready_for_decision"
    ready_at = conn.execute("SELECT MAX(timestamp) FROM audit_event WHERE case_id ="
                            " 'WAL-ONB-0010' AND action = 'evidence_pack_generated'"
                            ).fetchone()[0]
    page = render.dashboard(conn, {"view": "all"})
    assert ready_at[:16].replace("T", " ") in text(cell(page, "WAL-ONB-0010"))
    assert text(closed(page, "WAL-ONB-0010")) == "Still open"
    decision.record_decision(conn, "WAL-ONB-0010", "analyst.test", "analyst", "approve",
                             "all_mandatory_checks_passed", "clean", [], kb=KB)
    page = render.dashboard(conn, {"view": "all"})
    assert ready_at[:16].replace("T", " ") in text(cell(page, "WAL-ONB-0010")), \
        "still says when the alert came in"
    assert text(closed(page, "WAL-ONB-0010")).endswith("approve")
    row = re.search(r'<tr class="rowlink" data-href="/case/WAL-ONB-0010".*?</tr>', page,
                    re.S).group(0)
    assert "Decided by analyst.test" in text(row)

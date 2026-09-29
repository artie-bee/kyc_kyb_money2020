"""
A final decision (approved, rejected, withdrawn) closes any hold still open on
the case, "case decided", audited - so the dashboard's hold counts show only
cases someone still needs to act on. Non-final decisions leave holds alone.

Run: python -m pytest tests/test_holds_on_decided_cases.py -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import db, holds                                    # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import decision                              # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render                                                # noqa: E402

KB = KnowledgeBase()


@pytest.fixture(scope="module")
def full():
    conn = db.connect(":memory:")
    run_demo(conn, verbose=False)
    return conn


def test_case_11_rejected_has_no_open_hold_and_the_release_is_audited(full):
    assert full.execute("SELECT status FROM onboarding_case WHERE case_id = 'WAL-ONB-0011'"
                        ).fetchone()[0] == "rejected"
    assert holds.open_holds(full, "WAL-ONB-0011") == []
    released = full.execute(
        "SELECT released_by, release_reason FROM case_hold WHERE case_id = 'WAL-ONB-0011'"
        " AND release_reason LIKE 'case decided:%'").fetchall()
    assert released and released[0]["release_reason"].startswith("case decided: reject")
    event = full.execute("SELECT actor_id, payload_summary FROM audit_event WHERE"
                         " case_id = 'WAL-ONB-0011' AND action = 'case_hold_released'"
                         " AND payload_summary LIKE '%case decided%'").fetchone()
    assert event is not None


def test_no_case_with_a_final_status_keeps_an_open_hold(full):
    for (case_id,) in full.execute("SELECT case_id FROM onboarding_case WHERE status IN"
                                   " ('approved', 'rejected', 'closed_withdrawn')"):
        assert holds.open_holds(full, case_id) == [], case_id


def test_the_dashboard_counts_only_cases_someone_must_act_on(full):
    rows = {r["case_id"]: r for r in data.dashboard(full)}
    assert rows["WAL-ONB-0011"]["open_holds"] == 0
    with_holds = sum(1 for r in rows.values() if r["open_holds"])
    page = render.dashboard(full)
    assert ('<span class="stat__k">With holds</span><span class="stat__v num">'
            + str(with_holds) + "</span>") in page
    assert all(r["status"] not in decision.FINAL_STATUSES for r in rows.values()
               if r["open_holds"])


def test_a_non_final_decision_leaves_the_holds_open(full):
    """Case 6 was escalated: still with compliance, so its hold stands."""
    assert full.execute("SELECT decision FROM human_decision WHERE case_id = 'WAL-ONB-0006'"
                        ).fetchone()[0] == "escalate"
    assert holds.open_holds(full, "WAL-ONB-0006"), "an escalation is not the end of the case"

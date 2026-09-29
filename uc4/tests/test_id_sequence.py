"""
No id is ever issued twice: not after a row is deleted, and not after Reset
demo rebuilds the database. The scripted dataset is rebuilt with its same
fixed ids; everything issued afterwards continues above the highest number
ever issued.

Run: python -m pytest tests/test_id_sequence.py -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import db                                           # noqa: E402
from orchestrator.steps import document_quality                      # noqa: E402
from portal import apply                                              # noqa: E402
from tools import make_sample_documents as samples                    # noqa: E402


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "PORTAL_APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    path = tmp_path / "onboarding.db"
    data.reset_demo(path)
    state = {"path": path, "conn": data.connect(path)}
    yield state
    state["conn"].close()


def apply_as_new_customer(conn) -> str:
    answers = {key: value for _, _, key, value in samples.FORM_VALUES}
    return data.submit_application(conn, apply.build_application(answers, "PORTAL-TEST"))


def all_ids(conn) -> set:
    out = set()
    for table, (col, _) in db.ID_PREFIX.items():
        out |= {r[0] for r in conn.execute(f"SELECT {col} FROM {table}")}
    return out


def test_create_delete_create_gives_the_next_number_not_the_old_one(demo):
    conn = demo["conn"]
    first = apply_as_new_customer(conn)
    assert first == "WAL-DEMO-0001"
    issued = all_ids(conn)
    data.delete_demo_case(conn, first, "analyst.test")
    second = apply_as_new_customer(conn)
    assert second == "WAL-DEMO-0002", "WAL-DEMO-0001 is never issued again"
    gone = issued - all_ids(conn)
    assert gone, "the deleted case's rows are gone"
    assert not gone & all_ids(conn), "and none of their ids came back"


def test_after_reset_demo_the_sequence_continues(demo):
    conn = demo["conn"]
    assert apply_as_new_customer(conn) == "WAL-DEMO-0001"
    before = db.id_floors(conn)
    conn.close()

    data.reset_demo(demo["path"])
    demo["conn"] = conn = data.connect(demo["path"])
    assert (demo["path"].with_suffix(".ids.json")).exists(), "the floors live beside the db"
    assert conn.execute("SELECT COUNT(*) FROM onboarding_case WHERE case_id LIKE"
                        " 'WAL-DEMO-%'").fetchone()[0] == 0, "Reset removed the demo case"
    assert apply_as_new_customer(conn) == "WAL-DEMO-0002"
    newest_event = conn.execute("SELECT MAX(event_id) FROM audit_event").fetchone()[0]
    assert int(newest_event[4:]) > before["EVT-"], "audit ids continue above the old ones"


def test_the_scripted_dataset_keeps_its_fixed_ids_through_reset(demo, tmp_path):
    conn = demo["conn"]
    apply_as_new_customer(conn)
    scripted = sorted(r[0] for r in conn.execute(
        "SELECT item_id FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
        " WHERE p.case_id LIKE 'WAL-ONB-%'"))
    conn.close()
    data.reset_demo(demo["path"])
    demo["conn"] = conn = data.connect(demo["path"])
    assert [r[0] for r in conn.execute("SELECT case_id FROM onboarding_case ORDER BY 1")] == \
        [f"WAL-ONB-{n:04d}" for n in range(1, 15)]
    assert sorted(r[0] for r in conn.execute("SELECT item_id FROM checklist_item")) == scripted


def test_ids_only_go_up_when_the_newest_row_is_deleted(demo):
    conn = demo["conn"]
    case_id = apply_as_new_customer(conn)
    hold = db.next_id(conn, "case_hold")
    conn.execute("INSERT INTO case_hold (hold_id, case_id, placed_by_step, reason, owner,"
                 " placed_at) VALUES (?, ?, 'step.test', 'manual_review: test', 'analyst',"
                 " '2026-09-28T09:00:00Z')", (hold, case_id))
    conn.execute("DELETE FROM case_hold WHERE hold_id = ?", (hold,))
    again = db.next_id(conn, "case_hold")
    assert int(again[4:]) == int(hold[4:]) + 1, "a deleted id is not handed out again"

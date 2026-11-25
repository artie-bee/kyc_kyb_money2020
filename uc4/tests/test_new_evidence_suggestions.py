"""
A document asked for after review arrives on an assessed case: it is read at
once, even while another hold waits for an analyst, and the console suggests
what to do with it - keep or re-run, and which message to send.

Run: python -m pytest tests/test_new_evidence_suggestions.py -q
"""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import db, demo_samples, holds                      # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication as C                     # noqa: E402
from orchestrator.steps import document_quality                       # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402

KB = KnowledgeBase()
READY = "WAL-ONB-0001"          # every document in, ready for a decision
CHART = ROOT / "demo_packs" / "lumen_harbour_followup" / "13_ownership_chart.pdf"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("new_evidence") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def conn(built, tmp_path, monkeypatch):
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(document_quality, "UPLOADS", tmp_path / "uploads")
    c = data.connect(shutil.copy(built, tmp_path / "onboarding.db"))
    yield c
    c.close()


def ask_and_send_chart(conn, other_hold=False):
    data.send_message(conn, READY, "missing_items", "analyst.test")
    if other_hold:
        holds.place(conn, READY, "step.verification", "eligibility",
                    "eligibility: a register mismatch waiting for an analyst", "analyst", KB)
        conn.commit()
    item = next(i for i in C.owed_items(conn, READY) if i["document_type"] == "ownership_chart")
    return data.upload_document(conn, READY, item["item_id"], "chart.pdf", CHART.read_bytes())


def fields(conn, document_id):
    return {r["name"]: r["entry_method"] for r in conn.execute(
        "SELECT name, entry_method FROM extracted_field WHERE document_id = ?", (document_id,))}


def test_the_chart_is_a_recognised_sample():
    entry = demo_samples.recognise(path=CHART)
    assert entry and entry["document_type"] == "ownership_chart"
    assert {f["name"] for f in entry["fields"]} == {"intermediate_entity", "chart_summary"}


def test_a_late_chart_is_read_even_while_another_hold_is_open(conn):
    paid = [conn.execute(f"SELECT COUNT(*) FROM {t} WHERE case_id = ?", (READY,)).fetchone()[0]
            for t in ("registry_check", "risk_assessment")]
    r = ask_and_send_chart(conn, other_hold=True)
    assert r.quality_status == "accepted_for_checks"
    assert fields(conn, r.document_id) == {"intermediate_entity": "extracted",
                                           "chart_summary": "extracted"}
    # Reading it ran no paid check again, and the other hold is untouched.
    assert paid == [conn.execute(f"SELECT COUNT(*) FROM {t} WHERE case_id = ?",
                                 (READY,)).fetchone()[0]
                    for t in ("registry_check", "risk_assessment")]
    assert any("register mismatch" in h.reason for h in data.open_holds(conn, READY))
    assert data.reassessment_hold(conn, READY) is not None


def test_a_chart_that_matches_the_application_suggests_keeping(conn):
    r = ask_and_send_chart(conn)
    evidence = data.new_evidence(conn, READY)
    assert [d["document_id"] for d in evidence["documents"]] == [r.document_id]
    assert evidence["documents"][0]["read"]
    assert evidence["suggest"]["choice"] == "keep"
    assert "assessment stands" in evidence["suggest"]["reason"]
    # The message: confirm it arrived - once.
    assert data.suggested_message(conn, READY)["situation"] == "status_update"
    data.send_message(conn, READY, "status_update", "analyst.test")
    assert data.suggested_message(conn, READY)["situation"] != "status_update"
    # Keeping it clears the suggestion with the hold.
    data.keep_assessment(conn, READY, "analyst.test", evidence["suggest"]["reason"])
    assert data.new_evidence(conn, READY) is None


def test_a_chart_naming_a_company_suggests_re_running():
    documents = [{"document_id": "DOC-9999", "document_type": "ownership_chart",
                  "label": "Ownership chart", "read": True,
                  "fields": [("intermediate_entity", "Harbour Holdings SA")]}]
    suggest = data._suggest_reassessment(READY, documents)
    assert suggest["choice"] == "rerun" and "Harbour Holdings SA" in suggest["reason"]
    unread = [dict(documents[0], read=False, fields=[])]
    assert data._suggest_reassessment(READY, unread)["choice"] is None

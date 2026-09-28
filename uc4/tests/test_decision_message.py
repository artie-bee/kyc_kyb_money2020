"""
The Decision tab's "Customer message" section: a preview of the message a
decision would trigger, what was sent after it, and - on a confirmed sanctions
match - no automatic message, with compliance's approval as a separate act.

Run: python -m pytest tests/test_decision_message.py -q
"""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import data                                                  # noqa: E402
from orchestrator import db                                           # noqa: E402
from orchestrator.kb import KnowledgeBase                             # noqa: E402
from orchestrator.steps import communication, decision               # noqa: E402
from tools.run_demo import run as run_demo                            # noqa: E402
from web import render                                                # noqa: E402

KB = KnowledgeBase()
# The templates a case with an internal finding may be sent: those that state no reason.
GENERIC = {r["template_id"] for r in KB.communication_rules
           if r["allowed_when_restricted"].lower() == "true"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Every case up to its first human action: no decision recorded yet."""
    path = tmp_path_factory.mktemp("decision_message") / "demo.db"
    conn = db.connect(path)
    run_demo(conn, verbose=False, stop_before_human_actions=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def fresh(built, tmp_path):
    copies = []

    def open_copy():
        path = shutil.copy(built, tmp_path / f"copy{len(copies)}.db")
        conn = data.connect(path)
        copies.append(conn)
        return conn
    yield open_copy
    for c in copies:
        c.close()


def cases(conn):
    return [r[0] for r in conn.execute("SELECT case_id FROM onboarding_case ORDER BY case_id")]


def record(conn, case_id, choice, role):
    recommended = data.risk(conn, case_id)["assessment"]
    recommended = recommended["recommended_action"] if recommended else "insufficient_evidence"
    return decision.record_decision(
        conn, case_id, role + ".test", role, choice, "demo_decision", "recorded by a test", [],
        override_reason=None if choice == recommended else "a test override",
        escalation_target="compliance.desk" if choice == "escalate" else None, kb=KB)


def sent_after(conn, before):
    return conn.execute("SELECT c.template_id, o.body FROM outbox o JOIN communication c"
                        " USING (communication_id) WHERE o.audience = 'applicant' AND"
                        " o.outbox_id > ? ORDER BY o.outbox_id", (before,)).fetchall()


def last_outbox(conn):
    return conn.execute("SELECT COALESCE(MAX(outbox_id), '') FROM outbox").fetchone()[0]


# ---------------------------------------------------------------------------

def test_the_preview_is_the_message_actually_sent_after_the_decision(built, fresh):
    probe = fresh()
    compared = 0
    for case_id in cases(probe):
        for role in ("analyst", "compliance"):
            for choice in decision.permitted_decisions(probe, case_id, role, KB):
                conn = fresh()
                preview = decision.preview_customer_message(conn, case_id, choice, KB)
                before = last_outbox(conn)
                record(conn, case_id, choice, role)
                sent = sent_after(conn, before)
                if preview["kind"] == "message":
                    assert [(t, b) for t, b in sent] == [(preview["template_id"],
                                                          preview["text"])], (case_id, choice)
                    compared += 1
                else:
                    assert sent == [], (case_id, choice, preview["kind"])
                conn.close()
    assert compared >= 10, f"only {compared} previews compared with a real send"


def test_every_preview_passes_the_restricted_wording_scanner(built, fresh):
    conn = fresh()
    for case_id in cases(conn):
        for choice in data.available_decisions(conn, case_id):
            p = decision.preview_customer_message(conn, case_id, choice, KB)
            assert p["kind"] != "blocked", (case_id, choice)
            if p["text"]:
                assert not communication.scan(p["text"]), (case_id, choice)


@pytest.mark.parametrize("case_id", ["WAL-ONB-0005", "WAL-ONB-0006", "WAL-ONB-0012"])
def test_the_sensitive_cases_preview_only_generic_or_no_wording(fresh, case_id):
    conn = fresh()
    previews = data.decision_message_previews(conn, case_id,
                                              data.available_decisions(conn, case_id))
    assert previews
    for choice, p in previews.items():
        assert p["restricted"], "each of these cases carries an internal finding"
        if case_id == "WAL-ONB-0012":
            assert p["kind"] == "withheld" and p["text"] is None, choice
        else:
            assert p["kind"] in ("message", "none"), choice
            assert p["kind"] == "none" or p["template_id"] in GENERIC, (choice, p["template_id"])
    page = render.case_detail(conn, case_id, tab="Decision")
    if case_id == "WAL-ONB-0012":
        assert render.WITHHELD_TEXT in page and render.GENERIC_NOTE not in page
    else:
        assert render.GENERIC_NOTE in page


def test_case_12_the_decision_alone_sends_nothing_and_approval_is_separate(fresh):
    conn = fresh()
    case_id = "WAL-ONB-0012"
    choice = decision.permitted_decisions(conn, case_id, "compliance", KB)[0]
    before = last_outbox(conn)
    result = record(conn, case_id, choice, "compliance")
    assert sent_after(conn, before) == [], "the decision alone sends nothing"
    assert result.customer_message.compliance_task_id, "a compliance task is raised instead"
    page = render.case_detail(conn, case_id, tab="Decision", role="compliance")
    assert render.WITHHELD_TEXT in page
    assert 'action="/action/send-message"' in page.split("<h2>Decision</h2>")[1] \
        .split('action="/action/record-decision"')[0], "the existing approval action"

    # The existing compliance message-approval action, as the button posts it.
    situation = data.messages_after_decision(conn, case_id)["situation"]
    approved = data.send_message(conn, case_id, situation, "compliance.test")
    assert approved.sent_status == "sent" and approved.template_id in GENERIC
    events = conn.execute("SELECT event_id, action, actor_id FROM audit_event WHERE"
                          " case_id = ? AND action IN ('human_decision_recorded',"
                          " 'customer_communication_approved') ORDER BY event_id",
                          (case_id,)).fetchall()
    assert [e["action"] for e in events] == ["human_decision_recorded",
                                             "customer_communication_approved"]
    assert events[0]["event_id"] != events[1]["event_id"]
    after = data.messages_after_decision(conn, case_id)
    assert [m["communication_id"] for m in after["sent"]] == [approved.communication_id]


def test_after_the_decision_the_tab_shows_what_was_sent_and_links_to_communications(fresh):
    conn = fresh()
    case_id = "WAL-ONB-0010"
    assert data.messages_after_decision(conn, case_id) is None
    record(conn, case_id, "approve", "analyst")
    after = data.messages_after_decision(conn, case_id)
    (m,) = after["sent"]
    assert m["template_id"] == "TPL-0007" and m["delivery"] == communication.DELIVERY_LABEL
    page = render.case_detail(conn, case_id, tab="Decision")
    assert m["communication_id"] in page and "TPL-0007" in page
    assert communication.DELIVERY_LABEL in page and " UTC" in page
    assert ('<a href="/case/' + case_id + '?tab=Communications">' + render.SEE_ALL + "</a>"
            ) in page
    # The Communications tab is still the full history, follow-up panel first.
    comms = render.case_detail(conn, case_id, tab="Communications")
    assert "Customer follow-up" in comms and m["communication_id"] in comms


def test_the_preview_shows_one_block_per_decision_for_the_script_to_choose(fresh):
    conn = fresh()
    case_id = "WAL-ONB-0010"
    options = data.available_decisions(conn, case_id)
    page = render.case_detail(conn, case_id, tab="Decision")
    assert 'select name="choice" data-previews' in page
    for choice in options:
        assert f'data-preview-for="{choice}"' in page
    assert "hidden" not in page.split('id="customer-message"')[1], \
        "every block is visible without the script"

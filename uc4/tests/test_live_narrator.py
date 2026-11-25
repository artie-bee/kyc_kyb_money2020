"""
The case summary written by a real model (LiveNarrator).

No network here: a fake client stands in for Groq. What is checked is the fence
around the model - the facts it is given, the references it may cite, and what
happens when it fails - not the model's prose.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import llm_client, narrator                         # noqa: E402
from orchestrator.narrator import LiveNarrator, MockNarrator          # noqa: E402

PACK = {
    "applicant_summary": "Example OU, private limited company registered in EE as 123.",
    "open_holds": [{"hold_id": "HLD-0001", "reason": "proof of address unreadable"}],
    "risk_factors": [{"factor": "high_risk_activity", "weight": 20,
                      "evidence_refs": ["CHK-0001"]}],
    "missing_or_conflicting_evidence": "",
    "risk_band": "high", "risk_score": 55,
    "recommended_next_action": "enhanced_due_diligence",
    "requires_human_signoff": True,
}
IDS = {"HLD-0001", "CHK-0001", "DOC-0001"}


class FakeClient:
    """Records what was asked and answers with a canned reply."""

    def __init__(self, data=None, error=None):
        self.data, self.error, self.asked = data, error, []

    def ask(self, document_path, prompt, instruction, max_tokens=1500):
        self.asked.append((document_path, prompt, instruction))
        if self.error:
            raise self.error
        return llm_client.Call(data=self.data, model="fake-model",
                               prompt_version=prompt.stamp, provider="groq")


def test_the_model_writes_the_summary_and_the_audit_names_it():
    fake = FakeClient({"text": "Example OU is held for an unreadable proof of address.",
                       "evidence_refs": ["HLD-0001"], "confidence": 0.8})
    n = LiveNarrator(client=fake)
    out = n.compliance_narrative(PACK, IDS)
    assert out.text.startswith("Example OU is held")
    assert out.evidence_refs == ["HLD-0001"]
    assert n.mode == "live (groq)"
    assert n.version == "groq/fake-model/narrative_v1"


def test_it_is_text_only_and_is_given_the_computed_decision_and_the_ids():
    fake = FakeClient({"text": "x", "evidence_refs": [], "confidence": 1})
    LiveNarrator(client=fake).compliance_narrative(PACK, IDS)
    document, prompt, instruction = fake.asked[0]
    assert document is None, "no file is sent for a summary"
    assert prompt.stamp == "narrative_v1"
    assert "do not question, change, soften or" in prompt.text
    assert '"enhanced_due_diligence"' in instruction and '"risk_band": "high"' in instruction
    for i in IDS:
        assert i in instruction


def test_an_invented_reference_falls_back_to_the_template_and_says_so():
    fake = FakeClient({"text": "Cites a row that is not there.",
                       "evidence_refs": ["DOC-9999"], "confidence": 0.9})
    n = LiveNarrator(client=fake)
    out = n.compliance_narrative(PACK, IDS)
    assert out.text == MockNarrator().compliance_narrative(PACK, IDS).text
    assert "DOC-9999" not in out.text and "DOC-9999" not in out.evidence_refs
    assert n.mode.startswith("mock (live summary failed - UnknownEvidenceReference")
    assert n.version == "template - the live summary call failed"


@pytest.mark.parametrize("error", [llm_client.CallFailed("network down"),
                                   llm_client.MissingApiKey("GROQ_API_KEY is not set")])
def test_a_failed_call_never_stops_the_case(error):
    n = LiveNarrator(client=FakeClient(error=error))
    out = n.compliance_narrative(PACK, IDS)
    assert out.text == MockNarrator().compliance_narrative(PACK, IDS).text
    assert "live summary failed" in n.mode


def test_an_empty_reply_is_not_a_summary():
    n = LiveNarrator(client=FakeClient({"text": "  ", "evidence_refs": [], "confidence": 1}))
    n.compliance_narrative(PACK, IDS)
    assert "live summary failed" in n.mode


def test_factor_explanations_stay_the_kbs_own_words():
    factor = {"description": "Activity on the high-risk list."}
    out = LiveNarrator(client=FakeClient(error=AssertionError("not called"))).explain_factor(
        factor, {"evidence_refs": ["CHK-0001"]})
    assert out.text == "Activity on the high-risk list."


def test_mock_unless_switched_on(monkeypatch):
    assert isinstance(narrator.get_narrator(), MockNarrator)
    monkeypatch.setenv("WALLESTER_UC4_NARRATOR", "live")
    assert isinstance(narrator.get_narrator(), LiveNarrator)
    monkeypatch.setenv("WALLESTER_UC4_NARRATOR", "nonsense")
    with pytest.raises(ValueError):
        narrator.get_narrator()


def test_the_openai_compatible_client_sends_no_image_for_a_text_only_request(monkeypatch):
    sent = {}

    class Completions:
        def create(self, **kw):
            sent.update(kw)

            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": '{"a": 1}'})})]
                usage = None
            return R()

    fake_openai = type("O", (), {"chat": type("Ch", (), {"completions": Completions()})()})()
    client = llm_client.GroqClient(model="m")
    monkeypatch.setattr(client, "_client", lambda: fake_openai)
    call = client.ask(None, llm_client.load_prompt("narrative"), "facts")
    content = sent["messages"][0]["content"]
    assert all(part["type"] == "text" for part in content)
    assert call.data == {"a": 1} and call.transport == "text only: no document sent"

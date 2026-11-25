"""
STEP 8b - Human decision  (brief Sections 5.10, 10.7, 18)

The decision is a person's. This module records it, and refuses to record one
that the configured taxonomy does not permit:

  - nothing is approved while a hold is open. A hold means something is
    outstanding, and approving over it would be the system deciding the hold did
    not matter;
  - kb/analyst_decision_taxonomy.csv says which role may take which decision at
    which band. A critical case, or any case with a sanctions or serious-media
    finding, needs compliance;
  - override_flag is COMPUTED by comparing the decision with the recommended
    action. It is not passed in, because whether a reviewer overrode the system
    is a fact about the two values, not a claim the reviewer gets to make. An
    override needs a reason;
  - escalate needs somewhere to escalate to;
  - evidence_relied_on must cite ids that exist. A decision resting on a
    reference nobody can follow is not auditable.

Recording a decision sets the final status from the taxonomy and triggers the
matching customer message, subject to the restricted and sanctions rules in
communication.py.
"""

from dataclasses import dataclass, field

from .. import db, holds
from ..kb import KnowledgeBase
from . import communication, evidence_pack

ACTOR = "step.decision"
APPROVING = ("approve", "conditional_approve")
# Statuses a decision can leave a case in that end it.
FINAL_STATUSES = ("approved", "rejected", "closed_withdrawn")
COMPLIANCE_ONLY_BANDS = ("critical",)
# The reason for asking the customer for what they still owe. Its message is the
# request that names every outstanding item (TPL-0001), not a clarification.
REQUIRED_OUTSTANDING = "required_document_outstanding"
# request_more_information reasons that ask for documents: each sends that same
# request, and each needs something the customer still owes.
DOCUMENT_REASONS = (REQUIRED_OUTSTANDING, "document_unreadable", "document_expired",
                    "ubo_declaration_missing", "director_identity_missing",
                    "proof_of_address_too_old")
# request_more_information reasons that ask about a finding: the message states
# that reason, so it is only allowed when the case has that finding.
FINDING_REASONS = {
    "registered_address_mismatch": ("clarification", "This case has no address mismatch "
                                    "finding", ("RG-07",)),
    "ownership_structure_unclear": ("missing_items", "This case has no ownership finding",
                                    ("UB-03", "UB-04", "UB-06")),
}
# A reason with no approved customer wording yet: refused, never guessed.
NO_WORDING = {"source_of_funds_clarification":
              "There is no approved customer wording for a source of funds clarification "
              "yet; Wallester has to approve one before it can be sent"}

# situation used for the customer message that follows each decision
_SITUATION = {
    "approve": "final_outcome_approved",
    "conditional_approve": "final_outcome_approved",
    "reject": "final_outcome_declined",
    "withdrawn": "case_closed",
    "request_more_information": "clarification",
    "insufficient_evidence": "clarification",
    "enhanced_due_diligence": "manual_review_underway",
    "escalate": "manual_review_underway",
}


class DecisionRefused(PermissionError):
    """The taxonomy, or an open hold, does not permit this decision."""


@dataclass
class DecisionResult:
    case_id: str
    decision_id: str
    decision: str
    status: str
    override_flag: bool
    override_direction: str | None = None
    customer_message: communication.CommunicationResult | None = None
    problems: list[str] = field(default_factory=list)


def recommended_action(conn, case_id: str) -> str:
    """What the system recommends: the risk assessment's action, or - before
    there is one - asking for the documents the customer still owes, and
    otherwise insufficient evidence."""
    a = conn.execute("SELECT recommended_action FROM risk_assessment WHERE case_id = ?",
                     (case_id,)).fetchone()
    if a:
        return a[0]
    return ("request_more_information" if communication.owed_items(conn, case_id)
            else "insufficient_evidence")


def reason_problem(conn, case_id: str, decision: str, reason_code: str | None) -> str | None:
    """Why this reason cannot be used on this case now, or None. A message must
    never state a reason the case does not have: asking for documents needs
    something owed, a clarification needs its finding, and a reason with no
    approved wording is refused."""
    if decision != "request_more_information" or not reason_code:
        return None
    if reason_code in NO_WORDING:
        return NO_WORDING[reason_code]
    if reason_code in DOCUMENT_REASONS and not communication.owed_items(conn, case_id):
        return "Nothing is outstanding from this customer, so there is nothing to ask for"
    if reason_code in FINDING_REASONS:
        _, refusal, rules = FINDING_REASONS[reason_code]
        marks = ",".join("?" * len(rules))
        found = conn.execute(f"SELECT 1 FROM finding WHERE case_id = ? AND rule_id IN ({marks})",
                             (case_id, *rules)).fetchone()
        if not found and reason_code == "registered_address_mismatch":
            # The register's own answer is the evidence where no finding row exists.
            found = conn.execute("SELECT 1 FROM registry_check WHERE case_id = ? AND"
                                 " address_match = 'mismatch'", (case_id,)).fetchone()
        if not found:
            return refusal
    return None


def check_permitted(conn, case_id: str, reviewer_role: str, decision: str,
                    kb: KnowledgeBase, reviewer: str = "this reviewer"):
    """Whether this role may record this decision on this case now: the
    taxonomy's band and role rules, and no approval over an open hold. Raises
    DecisionRefused with the reason; returns (case, band, recommended).

    The one place those rules are checked - record_decision() calls it, and so
    do the console's queue and "why is this case here" panel, so a screen can
    never offer what the backend would refuse.
    """
    rule = kb.decision_taxonomy.get(decision)
    if rule is None:
        raise DecisionRefused(
            f"'{decision}' is not a decision in the taxonomy; "
            f"choose from {sorted(kb.decision_taxonomy)}")
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    assessment = conn.execute("SELECT * FROM risk_assessment WHERE case_id = ?",
                              (case_id,)).fetchone()
    band = assessment["risk_band"] if assessment else "insufficient_evidence"
    recommended = recommended_action(conn, case_id)

    # 1. nothing is approved over an open hold
    open_now = holds.open_holds(conn, case_id)
    if decision in APPROVING and open_now:
        raise DecisionRefused(
            f"cannot {decision} {case_id}: {len(open_now)} hold(s) are still open "
            f"({'; '.join(h.code for h in open_now)}). Release them first, with a reason")

    # 2. the band must permit the decision
    allowed_bands = [b for b in rule["allowed_bands"].split("|") if b]
    if band not in allowed_bands:
        raise DecisionRefused(
            f"'{decision}' is not available at band {band}; the taxonomy allows "
            f"{allowed_bands}")

    # 3. role. Critical, or any sanctions or serious-media finding, needs compliance.
    sensitive = conn.execute(
        "SELECT COUNT(*) FROM screening_check WHERE case_id = ? AND ("
        " sanctions_result IN ('possible_match', 'clear_match')"
        " OR adverse_media_result = 'serious')", (case_id,)).fetchone()[0]
    needs_compliance = (rule["required_role"] == "compliance"
                        or band in COMPLIANCE_ONLY_BANDS or sensitive)
    if needs_compliance and reviewer_role != "compliance":
        why = ("the band is critical" if band in COMPLIANCE_ONLY_BANDS else
               "there is a sanctions or serious adverse-media finding" if sensitive else
               f"'{decision}' is reserved to compliance")
        raise DecisionRefused(
            f"{reviewer} is {reviewer_role}, but {why}, so this decision is compliance's "
            f"to take")

    return case, band, recommended


def permitted_decisions(conn, case_id: str, reviewer_role: str,
                        kb: KnowledgeBase | None = None) -> list[str]:
    """Every decision check_permitted() allows this role on this case now."""
    kb = kb or KnowledgeBase()
    out = []
    for decision in kb.decision_taxonomy:
        try:
            check_permitted(conn, case_id, reviewer_role, decision, kb)
        except DecisionRefused:
            continue
        out.append(decision)
    return out


def record_decision(conn, case_id: str, reviewer: str, reviewer_role: str, decision: str,
                    reason_code: str, rationale: str, evidence_relied_on,
                    override_reason: str | None = None, escalation_target: str | None = None,
                    kb: KnowledgeBase | None = None,
                    chooser: communication.TemplateChooser | None = None) -> DecisionResult:
    kb = kb or KnowledgeBase()
    rule = kb.decision_taxonomy.get(decision)
    if rule is None:
        raise DecisionRefused(
            f"'{decision}' is not a decision in the taxonomy; "
            f"choose from {sorted(kb.decision_taxonomy)}")
    if not (rationale or "").strip() and rule["requires_reason"].lower() == "true":
        raise DecisionRefused("a decision needs a rationale")
    if not (reviewer or "").strip():
        raise DecisionRefused("the reviewer must be identified")

    case, band, recommended = check_permitted(conn, case_id, reviewer_role, decision, kb,
                                              reviewer=reviewer)

    # The reason code has to be one the KB lists for this decision.
    allowed_codes = [r["code"] for r in kb.reason_codes_for(decision)]
    if reason_code not in allowed_codes:
        raise DecisionRefused(
            f"reason code '{reason_code}' is not one for {decision}; "
            f"choose from {allowed_codes}")
    problem = reason_problem(conn, case_id, decision, reason_code)
    if problem:
        raise DecisionRefused(problem)

    # 4. override is computed, never claimed
    override = decision != recommended
    direction = kb.override_direction(decision, recommended)
    if override and not (override_reason or "").strip():
        raise DecisionRefused(
            f"the recommendation was '{recommended}' and the decision is '{decision}'. "
            f"That is an override and needs a reason")
    if not override and (override_reason or "").strip():
        raise DecisionRefused("an override reason was given but the decision agrees with the "
                              "recommendation")

    # 5. escalation needs a target
    if decision == "escalate" and not (escalation_target or "").strip():
        raise DecisionRefused("escalate needs an escalation_target")

    # 6. the evidence has to exist
    refs = ([r for r in evidence_relied_on.split("|") if r]
            if isinstance(evidence_relied_on, str) else list(evidence_relied_on or []))
    known = evidence_pack.known_ids(conn, case_id)
    unknown = [r for r in refs if r not in known]
    if unknown:
        raise DecisionRefused(
            f"the decision cites {unknown}, which are not on {case_id}; a reference nobody "
            f"can follow is not evidence")

    decision_id = db.next_id(conn, "human_decision")
    conn.execute(
        "INSERT INTO human_decision VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, case_id, reviewer, reviewer_role, decision, reason_code, rationale,
         "|".join(refs), int(override), override_reason, direction, escalation_target,
         rule["customer_template_id"], db.now()))
    db.audit(conn, case_id, reviewer_role, reviewer, "human_decision_recorded",
             f"{decision_id} -> {decision} (reason {reason_code}); band {band}, recommendation "
             f"{recommended}; override={override}"
             + (f" ({direction})" if direction else "")
             + (f"; escalated to {escalation_target}" if escalation_target else ""),
             kb.version)

    status = rule["resulting_status"]
    owner = "compliance" if decision == "escalate" else "system"
    if decision == "request_more_information":
        # Back to the customer, whatever was asked. Asking for documents they
        # still owe leaves the case where it is, waiting on them.
        owner = "customer"
        if reason_code in DOCUMENT_REASONS:
            status = case["status"]
    db.update_case(conn, case_id, status=status, next_action_owner=owner)

    # A final decision settles the case: nobody still needs to act on a hold it
    # left open (a reject over an eligibility hold, say). Each is closed by the
    # deciding reviewer, "case decided", and audited like any other release.
    if status in FINAL_STATUSES:
        for hold in holds.open_holds(conn, case_id):
            holds.release(conn, hold.hold_id, reviewer,
                          f"case decided: {decision} recorded as {decision_id}", kb=kb)

    comm = None
    situation, template_id = message_plan(kb, decision, case, reason_code)
    if situation:
        # The reviewer approves the message their decision triggers - except on
        # a confirmed sanctions match, where there is no automatic message at
        # all (communication gate 2): a compliance task is raised, and what the
        # customer is told is approved separately, as its own audited act.
        approver = None if communication.confirmed_sanctions_match(conn, case_id) else reviewer
        comm = communication.send_required_message(
            conn, case_id, situation, kb, chooser, approver=approver, template_id=template_id)

    db.audit(conn, case_id, "system", ACTOR, "decision_completed",
             f"{decision_id}: case -> {status}"
             + (f"; customer message {comm.communication_id} ({comm.sent_status})"
                if comm and comm.communication_id else "; no automatic customer message"),
             kb.version)
    return DecisionResult(case_id, decision_id, decision, status, override,
                          direction, comm, [])


def message_plan(kb: KnowledgeBase, decision: str, case,
                 reason_code: str | None = None) -> tuple[str | None, str | None]:
    """(situation, pinned template or None) for the customer message this
    decision triggers - what record_decision sends and preview_customer_message
    shows, from one place. The reason can change it: asking for what is still
    owed sends the request that names those items, never a clarification about
    something the case has no finding for."""
    if decision == "request_more_information" and (
            reason_code in DOCUMENT_REASONS or reason_code in FINDING_REASONS):
        situation = ("resubmission" if reason_code in DOCUMENT_REASONS
                     else FINDING_REASONS[reason_code][0])
        template_id = next((r["template_id"] for r in kb.communication_rules
                            if r["situation"] == situation), None)
        return situation, (template_id if template_id
                           and _allowed(kb, situation, template_id, case) else None)
    situation = _SITUATION.get(decision)
    if situation is None:
        return None, None
    template_id = kb.decision_taxonomy[decision]["customer_template_id"]
    return situation, (template_id if _allowed(kb, situation, template_id, case) else None)


def preview_customer_message(conn, case_id: str, decision: str,
                             kb: KnowledgeBase | None = None,
                             reason_code: str | None = None) -> dict:
    """The customer message recording this decision would trigger, rendered
    now and stored nowhere. See communication.preview_message for the kinds."""
    kb = kb or KnowledgeBase()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    problem = reason_problem(conn, case_id, decision, reason_code)
    if problem:
        return {"kind": "refused", "situation": None, "template_id": None, "text": problem,
                "restricted": bool(case["restricted_finding"])}
    situation, template_id = message_plan(kb, decision, case, reason_code)
    if situation is None:
        return {"kind": "none", "situation": None, "restricted": bool(case["restricted_finding"]),
                "template_id": None, "text": None}
    return communication.preview_message(conn, case_id, situation, kb, template_id=template_id)


def _allowed(kb: KnowledgeBase, situation: str, template_id: str, case) -> bool:
    restricted = bool(case["restricted_finding"])
    return template_id in [r["template_id"] for r in kb.templates_for(situation, restricted)]

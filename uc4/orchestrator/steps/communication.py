"""
STEP 8a - Customer communications  (brief Sections 5.9, 11.3)

Everything a customer ever receives comes from the approved template library.
The model may choose among the templates the KB allows for a situation and fill
their declared placeholders; it may not write a sentence. That is deliberate:
free text is how a screening result reaches a customer by accident.

Three gates, in order:

  1. the situation decides which templates are allowed, and a case with a
     restricted finding gets only the generic ones - those that state no reason;
  2. a confirmed sanctions match produces no automatic message at all. A
     compliance task is raised instead, because what to tell the customer in
     that situation is a decision with legal consequences, not a template;
  3. the rendered text is scanned for restricted wording before it can be sent,
     and a hit blocks the send and is audited.

Nothing leaves the building. A send writes to the outbox table.

Following up (the console's Customer follow-up panel) is read from the same
records: the request and reminder rows here and in the outbox, and the audit
trail for which items each message asked for and when the customer viewed it.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import clock, db, holds
from ..kb import KnowledgeBase
from .document_quality import CLOSED_STATUSES, MAX_ATTEMPTS, OPEN_FOR_UPLOAD
from .requirement_pack import shown_to_customer

ACTOR = "step.communication"

# The situations that ask the customer for something. The no-response clock
# starts at the first of them (first_request_at) and never restarts.
REQUEST_SITUATIONS = ("resubmission", "clarification", "missing_items")
REMINDER = "reminder"
# Outcome messages. They go out only with what they announce - a recorded
# decision, or chase() closing a case for want of a reply - never by hand,
# where a closure notice could reach a customer whose case is still open.
OUTCOME_SITUATIONS = ("case_closed", "final_outcome_approved", "final_outcome_declined")
# The optional early nudge on an unviewed request (kb/communication_schedule.csv).
NUDGE = "unviewed_nudge"
# What a send is, as far as the POC can say: written to the outbox table. There
# is no mail gateway to report delivery, so nothing is ever called "delivered".
DELIVERY_LABEL = "Simulated - sent to demo outbox"
TIMEZONE = "UTC"
FOLLOW_UP_STATUSES = ("Awaiting customer", "Reminder sent", "Completed", "Closed")
# Appended to a request's or reminder's communication_drafted audit row, so the
# items it asked for stay on the record however the checklist changes later.
_ITEMS_REQUESTED = "; items requested: "
_COMM_ID = re.compile(r"COM-\d+")

# Words that must never appear in anything a customer is shown (Section 11.3).
RESTRICTED_WORDING = (
    "sanction", "sanctions", "screening", "screened", "aml", "anti-money",
    "money launder", "launder", "pep", "politically exposed", "adverse media",
    "watchlist", "watch list", "risk score", "risk band", "escalation",
    "escalated", "match", "blacklist", "terror",
)
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class RestrictedWordingError(ValueError):
    """Rendered text contained wording that must never reach a customer."""


class TemplateNotAllowed(ValueError):
    """A template was chosen that the KB does not allow for this situation."""


@dataclass
class CommunicationResult:
    case_id: str
    communication_id: str | None
    template_id: str | None
    audience: str
    approval_status: str
    sent_status: str
    compliance_task_id: str | None = None
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Clock - real or fake, so the resubmission loop can be tested without waiting
# ---------------------------------------------------------------------------

# The one clock lives in orchestrator/clock.py; these names are kept so the
# resubmission loop reads as it always has.
Clock = clock.Clock
FakeClock = clock.FakeClock


# ---------------------------------------------------------------------------
# Choosing a template - the only judgement in this step
# ---------------------------------------------------------------------------

@dataclass
class TemplateChoice:
    template_id: str
    placeholders: dict


class TemplateChooser:
    mode = "base"
    version: str | None = None

    def choose(self, situation: str, allowed: list[dict], facts: dict) -> TemplateChoice:
        raise NotImplementedError


class MockTemplateChooser(TemplateChooser):
    """Takes the first template the KB allows and fills it from the case facts."""

    mode = "mock"
    version = None

    def choose(self, situation: str, allowed: list[dict], facts: dict) -> TemplateChoice:
        return TemplateChoice(allowed[0]["template_id"], dict(facts))


class ClaudeTemplateChooser(TemplateChooser):
    """Ask Claude which allowed template fits, and for the placeholder values.

    TODO: not wired up. No API call is made yet - calling choose() raises.

    When implemented it must:
      - be given ONLY the templates the KB allows for this situation, and
        require exactly {"template_id": "...", "placeholders": {...}} with no
        prose around it, re-asking once if the reply does not parse;
      - be rejected if it names a template outside that list. The list is the
        control; a model that can pick any template has no control at all;
      - fill placeholders from the customer-safe facts it is given and nothing
        else. It writes no sentences: the wording is the template's;
      - never be shown a screening result, a risk band or a finding. It cannot
        leak what it was not told;
      - set `version` to the model id plus the prompt version for the audit row.

    The rendered text is scanned for restricted wording afterwards regardless,
    because a prompt is a request and a scanner is a guarantee.
    """

    mode = "claude"

    def __init__(self, model: str = "claude-opus-5", prompt_version: str = "comm-v1"):
        self.model = model
        self.prompt_version = prompt_version
        self.version = f"{model}/{prompt_version}"

    def choose(self, situation: str, allowed: list[dict], facts: dict) -> TemplateChoice:
        raise NotImplementedError(
            "ClaudeTemplateChooser is a stub: no API call is wired up yet. "
            "Run with the mock chooser (the default) until it is.")


# ---------------------------------------------------------------------------

def scan(text: str) -> list[str]:
    """Restricted words present in this text. Word-boundary matched, so
    'match' catches the noun but 'matches the register' is not smuggled past."""
    lowered = f" {text.lower()} "
    return sorted({w for w in RESTRICTED_WORDING
                   if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", lowered)})


def owed_items(conn, case_id: str) -> list:
    """The checklist items the customer still has to send: asked of them, not
    optional, and either never uploaded or sent back. An item that is accepted,
    being looked at by a person, waived, or no longer asked for is not owed -
    so it drops out of every reminder by itself."""
    rows = conn.execute(
        "SELECT i.*, ind.full_name FROM checklist_item i JOIN requirement_pack p"
        " USING (pack_id) LEFT JOIN individual ind"
        " ON ind.individual_id = i.subject_individual_id"
        " WHERE p.case_id = ? ORDER BY i.item_id", (case_id,)).fetchall()
    return [r for r in rows if r["level"] != "optional" and r["status"] in OPEN_FOR_UPLOAD
            and shown_to_customer(r)]


def item_label(item) -> str:
    """How an item is named in a message: its document type, and whose it is."""
    name = item["document_type"].replace("_", " ")
    return name + (" for " + item["full_name"] if item["full_name"] else "")


def customer_safe_facts(conn, case_id: str) -> dict:
    """Only what a customer may be told: names and the items they still owe."""
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (case["applicant_id"],)).fetchone()
    contact = conn.execute(
        "SELECT full_name FROM individual WHERE applicant_id = ? ORDER BY individual_id LIMIT 1",
        (case["applicant_id"],)).fetchone()
    # Every owed item, in one list: a request or a reminder names them all in
    # one message, never one message per document.
    outstanding = list(dict.fromkeys(item_label(i) for i in owed_items(conn, case_id)))
    return {
        "case_id": case_id,
        "applicant_name": applicant["legal_name"],
        "contact_name": contact["full_name"] if contact else applicant["legal_name"],
        "document_list": ", ".join(outstanding) or "the outstanding documents",
        "missing_items": ", ".join(outstanding) or "the outstanding documents",
        # TPL-0001 and TPL-0003 declare {{item_list}}. Without it render() leaves
        # "[item_list]" in a message that goes to the applicant, and the one thing
        # a resubmission request has to do is name what is missing.
        "item_list": ", ".join(outstanding) or "the outstanding documents",
        "reason_text": "we need a clearer copy",
        "escalation_target": "the compliance team",
    }


def render(template: dict, placeholders: dict) -> str:
    """Fill the template's declared placeholders. Anything it does not declare
    is ignored, and a placeholder with no value is left visibly unfilled rather
    than guessed at."""
    text = template["template_text"]
    for name in PLACEHOLDER.findall(text):
        text = text.replace("{{" + name + "}}", str(placeholders.get(name, f"[{name}]")))
    return text


def confirmed_sanctions_match(conn, case_id: str) -> bool:
    return bool(conn.execute(
        "SELECT COUNT(*) FROM screening_check WHERE case_id = ? AND sanctions_result = "
        "'clear_match'", (case_id,)).fetchone()[0])


def _choose(conn, case_id: str, situation: str, kb: KnowledgeBase, chooser: TemplateChooser,
            template_id: str | None, restricted: bool):
    """Gate 1, the choice and the rendering - shared by a send and a preview,
    so what an analyst is shown is what would go. Returns (rule, template id,
    text), or (None, None, None) when no template is allowed at all."""
    allowed = kb.templates_for(situation, restricted)
    if not allowed:
        return None, None, None
    facts = customer_safe_facts(conn, case_id)
    if template_id:
        if template_id not in [a["template_id"] for a in allowed]:
            raise TemplateNotAllowed(
                f"{template_id} is not allowed for '{situation}'"
                + (" on a restricted case" if restricted else "")
                + f"; allowed: {[a['template_id'] for a in allowed]}")
        choice = TemplateChoice(template_id, facts)
    else:
        choice = chooser.choose(situation, allowed, facts)
        if choice.template_id not in [a["template_id"] for a in allowed]:
            raise TemplateNotAllowed(
                f"{chooser.mode} chooser returned {choice.template_id}, which is not allowed "
                f"for '{situation}'; allowed: {[a['template_id'] for a in allowed]}")
    rule = next(a for a in allowed if a["template_id"] == choice.template_id)
    return rule, choice.template_id, render(kb.message_templates[choice.template_id],
                                            choice.placeholders)


def preview_message(conn, case_id: str, situation: str, kb: KnowledgeBase,
                    template_id: str | None = None,
                    chooser: TemplateChooser | None = None) -> dict:
    """What an automatic send of this situation would do now, writing nothing:
    the same three gates, the same choice and rendering as send_required_message.

    kind is "message" (template_id, text), "withheld" (a confirmed sanctions
    match: no automatic message), "none" (no template is allowed) or "blocked"
    (the rendered text failed the restricted-wording scan; it is not returned).
    """
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    restricted = bool(case["restricted_finding"])
    out = {"situation": situation, "restricted": restricted, "template_id": None, "text": None}
    if confirmed_sanctions_match(conn, case_id):
        return dict(out, kind="withheld")
    rule, chosen, text = _choose(conn, case_id, situation, kb, chooser or MockTemplateChooser(),
                                 template_id, restricted)
    if rule is None:
        return dict(out, kind="none")
    if rule["audience"] == "applicant" and scan(text):
        return dict(out, kind="blocked", template_id=chosen)
    return dict(out, kind="message", template_id=chosen, text=text)


def send_required_message(conn, case_id: str, situation: str, kb: KnowledgeBase,
                          chooser: TemplateChooser | None = None,
                          approver: str | None = None,
                          template_id: str | None = None) -> CommunicationResult:
    """Draft, check and (if approved) send one message.

    `template_id` pins the choice when a decision has already named one;
    otherwise the chooser picks among the templates the KB allows.
    """
    chooser = chooser or MockTemplateChooser()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    restricted = bool(case["restricted_finding"])

    # Gate 2: a confirmed sanctions match is not a template decision.
    if confirmed_sanctions_match(conn, case_id) and not approver:
        task_id = db.next_id(conn, "compliance_task")
        conn.execute("INSERT INTO compliance_task VALUES (?,?,?,?,?,?,?,?)",
                     (task_id, case_id, "decide customer communication",
                      "a confirmed sanctions match is on this case; what the customer is told "
                      "is a compliance decision, not an automated one",
                      "compliance", db.now(), None, None))
        db.audit(conn, case_id, "system", ACTOR, "customer_communication_withheld",
                 f"{task_id} raised: no automatic message is sent on a confirmed sanctions "
                 f"match; compliance decides what the customer is told", kb.version)
        return CommunicationResult(case_id, None, None, "applicant", "not_drafted", "not_sent",
                                   task_id, ["awaiting a compliance decision on communication"])

    # Gate 1: which templates may be used at all, then the choice.
    rule, chosen, text = _choose(conn, case_id, situation, kb, chooser, template_id, restricted)
    if rule is None:
        msg = (f"no template is allowed for '{situation}'"
               + (" on a case with a restricted finding" if restricted else ""))
        db.audit(conn, case_id, "system", ACTOR, "communication_blocked", msg, kb.version)
        return CommunicationResult(case_id, None, None, "applicant", "not_drafted", "not_sent",
                                   None, [msg])
    choice = TemplateChoice(chosen, {})
    template = kb.message_templates[chosen]

    comm_id = db.next_id(conn, "communication")
    approval = "approved" if approver else (
        "pending_approval" if rule["requires_approval"].lower() == "true" else "approved")
    conn.execute(
        "INSERT INTO communication (communication_id, case_id, template_id, audience,"
        " message_type, situation, approval_status, approved_by, sent_status, rendered_text,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (comm_id, case_id, choice.template_id, rule["audience"], template["message_type"],
         situation, approval, approver, "not_sent", text, db.now()))
    asked = ""
    if situation in REQUEST_SITUATIONS + (REMINDER, NUDGE):
        asked = _ITEMS_REQUESTED + (", ".join(i["item_id"] for i in owed_items(conn, case_id))
                                    or "none")
    db.audit(conn, case_id, "ai_agent", ACTOR, "communication_drafted",
             f"{comm_id} drafted from template {choice.template_id} for audience "
             f"{rule['audience']}; situation {situation}; restricted={restricted}; "
             f"chooser={chooser.mode}" + asked, chooser.version or kb.version)

    # Gate 3: the scanner runs on the rendered text, whatever produced it.
    if rule["audience"] == "applicant":
        hits = scan(text)
        if hits:
            conn.execute("UPDATE communication SET approval_status = 'rejected',"
                         " sent_status = 'not_sent' WHERE communication_id = ?", (comm_id,))
            msg = (f"{comm_id} blocked before sending: restricted wording {hits} in text "
                   f"intended for the applicant")
            db.audit(conn, case_id, "system", ACTOR, "communication_blocked", msg, kb.version)
            raise RestrictedWordingError(msg)

    sent = "not_sent"
    if approval == "approved":
        conn.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?)",
                     (db.next_id(conn, "outbox"), comm_id, case_id, rule["audience"],
                      text, db.now()))
        sent = "sent"
        conn.execute("UPDATE communication SET sent_status = 'sent' WHERE communication_id = ?",
                     (comm_id,))
        # A person approving something the customer will read is a distinct act
        # from the case decision that prompted it, and is recorded as one.
        action = ("customer_communication_approved"
                  if approver and rule["audience"] == "applicant"
                  else "communication_approved")
        db.audit(conn, case_id, "analyst" if approver else "system",
                 approver or ACTOR, action,
                 f"{comm_id} approved for release to {rule['audience']} under template "
                 f"{choice.template_id}", kb.version)
    else:
        db.audit(conn, case_id, "system", ACTOR, "communication_held",
                 f"{comm_id} drafted and held for approval under template "
                 f"{choice.template_id}", kb.version)

    return CommunicationResult(case_id, comm_id, choice.template_id, rule["audience"],
                               approval, sent, None, [])


# ---------------------------------------------------------------------------
# The resubmission loop
# ---------------------------------------------------------------------------

def first_request_at(conn, case_id: str) -> str | None:
    """When the customer was first asked for anything on this case: the first
    request message sent, or the first time the case was put on the customer,
    whichever came first. Both deadlines count from here.

    Read from rows that are never rewritten (an outbox row, a hold's placed_at),
    so nothing that happens later can move it: not a new upload, not a new
    attempt at an item, not Step 3 lifting and re-placing its customer hold.
    """
    marks = ",".join("?" * len(REQUEST_SITUATIONS))
    sent = conn.execute(
        "SELECT MIN(o.sent_at) FROM outbox o JOIN communication c USING (communication_id)"
        f" WHERE o.case_id = ? AND c.audience = 'applicant' AND c.situation IN ({marks})",
        (case_id, *REQUEST_SITUATIONS)).fetchone()[0]
    held = conn.execute("SELECT MIN(placed_at) FROM case_hold WHERE case_id = ?"
                        " AND owner = 'customer'", (case_id,)).fetchone()[0]
    return min((t for t in (sent, held) if t), default=None)


def customer_blocking(conn, case_id: str) -> tuple[list, bool]:
    """(the open customer holds, whether the customer is the only blocker left:
    at least one customer hold open and no hold owned by anyone else)."""
    open_now = holds.open_holds(conn, case_id)
    waiting_on = [h for h in open_now if h.owner == "customer"]
    return waiting_on, bool(waiting_on) and len(waiting_on) == len(open_now)


def chase(conn, case_id: str, kb: KnowledgeBase, clock: Clock,
          chooser: TemplateChooser | None = None,
          approver: str = "ops.queue") -> CommunicationResult | None:
    """Send the single reminder, or close the case, when the clock says so.

    Called each day the case sits unanswered. Returns the message it sent, or
    None if it is not yet time for one - or no longer: the case is closed or
    withdrawn, or nothing is owed any more (an acceptable replacement arrived,
    or the item was waived or taken over by an analyst).

    The reminder is one message listing every item still owed, never one per
    document, and it goes whenever the customer owes anything - even while
    another hold (an analyst on a different document, say) is open too. The
    automatic closure is narrower: only when the customer is the sole
    remaining blocker, so a case is never withdrawn while the team still has
    work on it. Both deadlines count from the FIRST request and a new attempt
    does not restart them. Whether the customer has viewed a message never
    touches the schedule: reminders and closure follow it regardless. Views
    are read only by the optional early nudge (unviewed_nudge), which is off
    unless the KB sets a day count.
    """
    status = conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                          (case_id,)).fetchone()["status"]
    owed = owed_items(conn, case_id)
    started = first_request_at(conn, case_id)
    if status in CLOSED_STATUSES or not owed or not started:
        return None
    waiting_on, sole_blocker = customer_blocking(conn, case_id)

    waited = (clock.today() - date.fromisoformat(started[:10])).days
    already = {r["situation"] for r in conn.execute(
        "SELECT situation FROM communication WHERE case_id = ?", (case_id,))}
    # An extra, never a substitute: the schedule below runs whatever it did.
    nudged = _nudge(conn, case_id, kb, clock, chooser, approver)

    if waited >= kb.communication_schedule["close_after_days"] and sole_blocker:
        if "case_closed" in already:
            return nudged
        result = send_required_message(conn, case_id, "case_closed", kb, chooser, approver)
        for hold in waiting_on:
            holds.release(conn, hold.hold_id, approver,
                          f"no response {waited} days after the request; closing as withdrawn")
        db.update_case(conn, case_id, status="closed_withdrawn", next_action_owner="system")
        db.audit(conn, case_id, "system", ACTOR, "case_closed_no_response",
                 f"no response {waited} days after the original request; closed as withdrawn "
                 f"with no assessment made and no further provider checks commissioned",
                 kb.version)
        return result

    if waited >= kb.communication_schedule["first_reminder_after_days"]:
        if REMINDER in already:
            return nudged
        result = send_required_message(conn, case_id, REMINDER, kb, chooser, approver)
        if result.communication_id is None:
            return result           # withheld or blocked; the step audited why
        db.audit(conn, case_id, "system", ACTOR, "applicant_chased",
                 f"no response after {waited} days; single reminder {result.communication_id} "
                 f"sent under the approved template, listing {len(owed)} outstanding item(s): "
                 + ", ".join(f"{i['item_id']} ({i['document_type']})" for i in owed),
                 kb.version)
        return result
    return nudged


def _latest_request(conn, case_id: str):
    """The most recent request sent to the customer (id, sent_at), or None."""
    marks = ",".join("?" * len(REQUEST_SITUATIONS))
    return conn.execute(
        "SELECT c.communication_id, o.sent_at FROM outbox o JOIN communication c"
        f" USING (communication_id) WHERE o.case_id = ? AND c.situation IN ({marks})"
        " ORDER BY o.outbox_id DESC LIMIT 1", (case_id, *REQUEST_SITUATIONS)).fetchone()


def _nudged_requests(conn, case_id: str) -> set:
    return {m for (p,) in conn.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = ?"
        " AND action = 'unviewed_nudge_sent'", (case_id,)) for m in _COMM_ID.findall(p)[:1]}


def nudge_due(conn, case_id: str, kb: KnowledgeBase) -> str | None:
    """The date the optional nudge would go for the latest request, or None:
    it is off (the KB's unviewed_nudge is 0), there is no request, the customer
    has viewed it, or it has already had its one nudge."""
    days = kb.communication_schedule.get(NUDGE, 0)
    request = _latest_request(conn, case_id) if days > 0 else None
    if (request is None or request["communication_id"] in _first_views(conn, case_id)
            or request["communication_id"] in _nudged_requests(conn, case_id)):
        return None
    return _on_day(request["sent_at"], days)


def _nudge(conn, case_id, kb, clock, chooser, approver) -> CommunicationResult | None:
    """One short nudge on the latest request if, after the KB's day count, the
    customer has not viewed it. At most one per request; off by default."""
    due = nudge_due(conn, case_id, kb)
    if due is None or clock.today().isoformat() < due:
        return None
    request = _latest_request(conn, case_id)
    result = send_required_message(conn, case_id, NUDGE, kb, chooser, approver)
    if result.communication_id is None:
        return result
    days = (clock.today() - date.fromisoformat(request["sent_at"][:10])).days
    db.audit(conn, case_id, "system", ACTOR, "unviewed_nudge_sent",
             f"{request['communication_id']} not viewed {days} days after it was sent; one "
             f"nudge {result.communication_id} sent under the approved template "
             f"(unviewed_nudge = {kb.communication_schedule[NUDGE]} days). The reminder "
             f"schedule is unchanged", kb.version)
    return result


# ---------------------------------------------------------------------------
# Follow-up visibility: view tracking and the console panel
# ---------------------------------------------------------------------------

def record_view(conn, case_id: str, communication_ids, kb: KnowledgeBase | None = None) -> None:
    """The customer opened their messages. Recorded for the console's
    follow-up panel only: chase() never reads it, so a viewed message is
    reminded about on the same schedule as one nobody opened."""
    ids = [i for i in communication_ids if i]
    if ids:
        db.audit(conn, case_id, "applicant", "customer", "message_viewed",
                 f"{', '.join(ids)} shown to the customer on the Messages page",
                 getattr(kb, "version", None))


def _first_views(conn, case_id: str) -> dict:
    """{communication_id: when the customer first viewed it}."""
    out = {}
    for r in conn.execute("SELECT payload_summary, timestamp FROM audit_event WHERE case_id = ?"
                          " AND action = 'message_viewed' ORDER BY event_id", (case_id,)):
        for comm_id in _COMM_ID.findall(r["payload_summary"]):
            out.setdefault(comm_id, r["timestamp"])
    return out


def _items_requested(conn, case_id: str) -> dict:
    """{communication_id: [item ids it asked for]}, from its draft audit row."""
    out = {}
    for (payload,) in conn.execute("SELECT payload_summary FROM audit_event WHERE case_id = ?"
                                   " AND action = 'communication_drafted'", (case_id,)):
        head, found, items = payload.partition(_ITEMS_REQUESTED)
        comm = _COMM_ID.match(head)
        if found and comm:
            out[comm.group(0)] = [i for i in items.split(", ") if i and i != "none"]
    return out


def _on_day(stamp: str, days: int) -> str:
    return (date.fromisoformat(stamp[:10]) + timedelta(days=days)).isoformat()


def waiting_timing(conn, case_id: str, kb: KnowledgeBase) -> dict | None:
    """For the dashboard, while the customer owes an item on an open case:
    since when (the first request, which never resets), the customer's latest
    view of any message, and the next message the schedule would send - the
    optional nudge if it is on and due first, otherwise the reminder. None when
    the customer owes nothing or the case is closed."""
    status = conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                          (case_id,)).fetchone()["status"]
    started = first_request_at(conn, case_id)
    if status in CLOSED_STATUSES or not started or not owed_items(conn, case_id):
        return None
    due = []
    if not conn.execute("SELECT 1 FROM communication WHERE case_id = ? AND situation = ?",
                        (case_id, REMINDER)).fetchone():
        due.append((_on_day(started, kb.communication_schedule["first_reminder_after_days"]),
                    "reminder"))
    nudge = nudge_due(conn, case_id, kb)
    if nudge:
        due.append((nudge, "nudge"))
    viewed = conn.execute("SELECT MAX(timestamp) FROM audit_event WHERE case_id = ? AND"
                          " action = 'message_viewed'", (case_id,)).fetchone()[0]
    nxt = min(due) if due else None
    return {"since": started,
            "days": (clock.current().today() - date.fromisoformat(started[:10])).days,
            "last_viewed": viewed,
            "next_due": nxt[0] if nxt else None, "next_kind": nxt[1] if nxt else None,
            "timezone": TIMEZONE}


def follow_up(conn, case_id: str, kb: KnowledgeBase) -> dict:
    """What the console's Customer follow-up panel shows: one entry per request
    sent to the customer, each with the items it asked for, when it was sent,
    whether it was viewed, the reminders since and where it stands.

    Built from records that already exist; nothing is stored for the panel.
    Times are the stored UTC stamps.
    """
    case = conn.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()
    closed = case["status"] in CLOSED_STATUSES
    schedule = kb.communication_schedule
    started = first_request_at(conn, case_id)
    _, sole_blocker = customer_blocking(conn, case_id)
    owed = {i["item_id"] for i in owed_items(conn, case_id)}
    items = {r["item_id"]: r for r in conn.execute(
        "SELECT i.*, ind.full_name FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
        " LEFT JOIN individual ind ON ind.individual_id = i.subject_individual_id"
        " WHERE p.case_id = ?", (case_id,))}
    views = _first_views(conn, case_id)
    asked = _items_requested(conn, case_id)
    sent = [dict(r) for r in conn.execute(
        "SELECT c.communication_id, c.situation, o.sent_at FROM outbox o"
        " JOIN communication c USING (communication_id) WHERE o.case_id = ?"
        " AND o.audience = 'applicant' ORDER BY o.outbox_id", (case_id,))]
    reminders = [dict(m, delivery=DELIVERY_LABEL, viewed_at=views.get(m["communication_id"]))
                 for m in sent if m["situation"] in (REMINDER, NUDGE)]

    requests = []
    for m in (m for m in sent if m["situation"] in REQUEST_SITUATIONS):
        # A request sent before items were recorded on it asked for what is owed.
        ids = asked.get(m["communication_id"]) or sorted(owed)
        documents = []
        for item_id in ids:
            item = items.get(item_id)
            state = ("no longer requested" if item is None or not shown_to_customer(item)
                     else "outstanding" if item_id in owed else "received")
            documents.append({"item_id": item_id, "state": state,
                              "label": item_label(item) if item else item_id})
        since = [r for r in reminders if r["sent_at"] >= m["sent_at"]]
        outstanding = any(d["state"] == "outstanding" for d in documents)
        if closed:
            status = "Closed"
        elif outstanding:
            status = "Reminder sent" if since else "Awaiting customer"
        elif any(d["state"] == "received" for d in documents):
            status = "Completed"
        else:
            status = "Closed"

        due = None
        if status == "Awaiting customer" and started:
            due = _on_day(started, schedule["first_reminder_after_days"])
        requests.append({
            "communication_id": m["communication_id"],
            "situation": m["situation"],
            "documents": documents,
            "sent_at": m["sent_at"],
            "delivery": DELIVERY_LABEL,
            "viewed_at": views.get(m["communication_id"]),
            "next_reminder_due": due,
            "reminders": since,
            "status": status,
        })

    open_now = any(r["status"] in ("Awaiting customer", "Reminder sent") for r in requests)
    return {
        "timezone": TIMEZONE,
        "requests": requests,
        "first_request_at": started,
        # The case-level limit, from the first request; a new attempt never moves it.
        # It applies only while the customer is the sole remaining blocker.
        "closes_on": (_on_day(started, schedule["close_after_days"])
                      if started and open_now and sole_blocker else None),
        "closure_paused": bool(started and open_now and not sole_blocker),
        "owed": [item_label(items[i]) for i in sorted(owed)],
        "attempt_limit": MAX_ATTEMPTS,
    }


def reupload(conn, case_id: str, application: dict, kb: KnowledgeBase, file_name: str,
             checker=None) -> dict:
    """A replacement document arrives. Only that checklist item is re-screened."""
    from . import document_quality
    doc = next((d for d in application.get("documents", []) if d["file_name"] == file_name), None)
    if doc is None:
        raise KeyError(f"{file_name} is not on this application")
    one = dict(application, documents=[doc])
    db.audit(conn, case_id, "applicant", "customer", "document_resubmitted",
             f"{file_name} re-uploaded; re-screening that checklist item only", kb.version)
    result = document_quality.run(conn, case_id, one, kb, checker=checker)
    return {"file_name": file_name, "status": result.status, "next_step": result.next_step}

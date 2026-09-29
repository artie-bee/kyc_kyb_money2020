"""
Deleting one demo case (WAL-DEMO-), in a demo build only.

Reset demo removes every WAL-DEMO- case by deleting the database file and
rebuilding it; the append-only audit triggers never come into it. Removing a
single case cannot do that, so this deletes that case's rows directly, in one
transaction, and lifts the audit trigger only inside it:

  - it refuses any id that is not a WAL-DEMO- case: a scripted WAL-ONB- case
    can never be deleted, and neither can anything else;
  - the audit_no_delete trigger is dropped and re-created inside the same
    transaction, so a failure part-way rolls the whole thing back, trigger
    included, and no other statement ever runs without it;
  - id_sequence is left alone, so the deleted case's ids are never issued again.

Files (uploads, the saved application) and the deletion log are the caller's:
app/data.delete_demo_case.
"""

from . import db

# Child tables first, so every foreign key is satisfied as rows go.
_BY_CASE = (
    ("risk_factor", "assessment_id IN (SELECT assessment_id FROM risk_assessment"
                    " WHERE case_id = ?)"),
    ("extracted_field", "document_id IN (SELECT document_id FROM document WHERE case_id = ?)"),
    ("checklist_item_document", "document_id IN (SELECT document_id FROM document"
                                " WHERE case_id = ?)"),
    ("checklist_item", "pack_id IN (SELECT pack_id FROM requirement_pack WHERE case_id = ?)"),
    ("outbox", "case_id = ?"),
)
_CASE_TABLES = ("requirement_pack", "document", "registry_check", "identity_check",
                "screening_check", "finding", "evidence_pack", "risk_assessment",
                "communication", "compliance_task", "human_decision", "case_hold",
                "portal_token", "audit_event")
_AUDIT_TRIGGER = ("CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_event "
                  "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END")


class NotADemoCase(PermissionError):
    """Only a WAL-DEMO- case may be deleted."""


def delete(conn, case_id: str) -> dict:
    """Remove one WAL-DEMO- case and everything that belongs to it. Returns
    {case_id, applicant, rows}. The case's ids are never reused."""
    if not db.is_demo_case(case_id):
        raise NotADemoCase(f"{case_id} is not a demo case; only WAL-DEMO- cases can be deleted")
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    if case is None:
        raise KeyError(f"no such case {case_id}")
    applicant = case["applicant_id"]
    name = conn.execute("SELECT legal_name FROM applicant WHERE applicant_id = ?",
                        (applicant,)).fetchone()
    conn.commit()
    rows = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TRIGGER audit_no_delete")
        for table, where in _BY_CASE:
            rows += conn.execute(f"DELETE FROM {table} WHERE {where}", (case_id,)).rowcount
        for table in _CASE_TABLES:
            rows += conn.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,)).rowcount
        rows += conn.execute("DELETE FROM onboarding_case WHERE case_id = ?",
                             (case_id,)).rowcount
        # The demo applicant and its people belong to this case alone.
        rows += conn.execute("DELETE FROM ubo WHERE applicant_id = ?", (applicant,)).rowcount
        rows += conn.execute("DELETE FROM individual WHERE applicant_id = ?",
                             (applicant,)).rowcount
        rows += conn.execute("DELETE FROM applicant WHERE applicant_id = ? AND NOT EXISTS"
                             " (SELECT 1 FROM onboarding_case WHERE applicant_id = ?)",
                             (applicant, applicant)).rowcount
        conn.execute(_AUDIT_TRIGGER)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"case_id": case_id, "applicant": name["legal_name"] if name else "", "rows": rows}

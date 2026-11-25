"""
Set up the follow-up reminder showcase: a new Kivirand Digital OÜ case that
owes one document and has been asked for it in writing.

    python tools/make_reminder_showcase.py

Everything goes through the same calls the portal and the console make:
  - the application answers in demo_packs/kivirand_digital/CASE_DETAILS.md go
    through the portal form's build_application() and intake;
  - the ten set-up files are uploaded against their checklist items (they are
    a recognised demo pack, so each is accepted and read in mock mode);
  - the written request (TPL-0001) is sent for what is still owed: the source
    of funds declaration.

The reminder is then left for the presenter: send it with the item's Send
reminder button on the Checklist tab, or move the demo clock on to the due
date and it goes out on its own. Uploading
demo_packs/kivirand_digital/upload_during_demo/10_source_of_funds_declaration.pdf
in the portal stops any further reminder.
"""

import argparse
import secrets
import sys
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402
from orchestrator.steps import communication                           # noqa: E402
from portal import apply                                               # noqa: E402

PACK = UC4 / "demo_packs" / "kivirand_digital"

ANSWERS = {
    "legal_name": "Kivirand Digital OÜ",
    "registration_number": "16924517",
    "entity_type": "private_limited_company",
    "country": "EE",
    "registered_address": "Tartu mnt 64, 10115 Tallinn, Estonia",
    "business_activity": "Web development and online shop set-up for small retailers",
    "monthly_spend": "4500",
    "currency": "EUR",
    "contact_name": "Mart Kivirand",
    "contact_email": "mart@kiviranddigital.example",
    "d1_name": "Mart Kivirand", "d1_dob": "1987-11-02",
    "d1_nationality": "EE", "d1_residence": "EE",
    "o1_name": "Mart Kivirand", "o1_dob": "1987-11-02",
    "o1_nationality": "EE", "o1_residence": "EE",
    "o1_held": "direct", "o1_pct": "100",
    "fact_company_owner": "no", "fact_trust": "no", "fact_vat": "yes",
    "fact_trade_register": "yes", "fact_partner": "no",
    "confirm_demo": "yes",
}

# Every set-up file, by the checklist item it answers. The source of funds
# declaration is left out on purpose: it is what the request asks for.
SET_UP = {
    "certificate_of_incorporation": "01_certificate_of_incorporation.pdf",
    "registry_extract": "02_company_registration_extract.pdf",
    "tax_registration_certificate": "03_tax_registration_certificate.pdf",
    "director_register": "04_register_of_directors.pdf",
    "authorised_signatory_list": "05_list_of_authorised_signatories.pdf",
    "ubo_declaration": "06_declaration_of_beneficial_owners.pdf",
    "id_document": "07_identity_document_mart_kivirand.jpg",
    "proof_of_address": "08_proof_of_address_mart_kivirand.pdf",
    "liveness_selfie": "09_selfie_mart_kivirand.jpg",
    "business_activity_description": "11_business_activity_description.pdf",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--approver", default="analyst.demo",
                    help="who approves the written request (default analyst.demo)")
    args = ap.parse_args()

    conn = data.connect()
    # The same checks the form runs, step by step, before it will send.
    errors = [e for step in range(apply.BUSINESS, apply.REVIEW + 1)
              for e in apply.validate(step, ANSWERS)]
    if errors:
        sys.exit("The showcase answers do not pass the form's checks: " + "; ".join(errors))
    case_id = data.submit_application(
        conn, apply.build_application(ANSWERS, "PORTAL-" + secrets.token_hex(4)))
    print(f"{case_id}: application submitted for {ANSWERS['legal_name']}")

    items = {r["document_type"]: r for r in data.checklist(conn, case_id)}
    for doc_type, name in SET_UP.items():
        item = items.get(doc_type)
        if item is None:
            sys.exit(f"{case_id}: no checklist item for {doc_type}; the showcase needs one")
        r = data.upload_document(conn, case_id, item["item_id"], name,
                                 (PACK / name).read_bytes())
        print(f"  {name} -> {r.document_id}: {r.quality_status}")

    owed = [communication.item_label(i) for i in communication.owed_items(conn, case_id)]
    if not owed:
        sys.exit(f"{case_id}: nothing is owed, so there is nothing to ask for")
    data.send_message(conn, case_id, "resubmission", args.approver)
    print(f"  written request sent for: {', '.join(owed)}")
    print(f"{case_id} is ready: the reminder is the next message (Send reminder on "
          f"the Checklist tab, or the demo clock).")
    conn.close()


if __name__ == "__main__":
    main()

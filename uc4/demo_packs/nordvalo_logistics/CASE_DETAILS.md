# New application — Nordvalo Logistics OÜ

All details are fictional. Every document is watermarked
"SPECIMEN — SYNTHETIC TEST DOCUMENT" and uses invented issuers.

What makes it dynamic, compared with Lumen Harbour OU (one director, 12 items):
two directors who both own shares, so each gets an ID, a proof of address and a
selfie; and card spend above 50,000 EUR a month, so a **business bank
statement** is added. The contact is a director, so no board resolution is asked
for (it shows as waived). 15 items in all, checked in a dry run on 2026-09-30.

## 1. Application form values (type exactly)

Portal: http://127.0.0.1:8701/demo → **Start a new demo application**.
Leave Director 3-4 and Owner 3-4 blank.

| Step | Field | Value |
|---|---|---|
| Your business | Legal name | Nordvalo Logistics OÜ |
| Your business | Trading name | *(blank)* |
| Your business | Legal form | Private limited company (Ltd, OÜ) |
| Your business | Registration number | 16987231 |
| Your business | Country | Estonia |
| Your business | Registered address | Peterburi tee 46, 11415 Tallinn, Estonia |
| Your business | What the business does | Same-day courier and last-mile delivery for online shops in Tallinn and Tartu |
| Your business | Expected card spend per month | 65000 |
| Your business | Currency | EUR |
| Contact | Contact person | Anneli Rebane |
| Contact | Email | anneli@nordvalo.example |
| People | Director 1 | Anneli Rebane · 1985-06-21 · Estonia · Estonia |
| People | Director 2 | Tanel Sepp · 1990-02-09 · Estonia · Estonia |
| Owners | Owner 1 | Anneli Rebane · Directly · 60 |
| Owners | Owner 2 | Tanel Sepp · Directly · 40 |
| A few facts | Company owns part? | No |
| A few facts | Trust or nominee? | No |
| A few facts | VAT registered? | Yes |
| A few facts | National trade register? | Yes |
| A few facts | Cards under your own brand? | No |
| Check and send | Made up for the demo | Yes |

## 2. The facts every document must agree on

| | Anneli Rebane | Tanel Sepp |
|---|---|---|
| Role | Director (management board member), authorised signatory, 60% owner | Director (management board member), authorised signatory, 40% owner |
| Date of birth | 1985-06-21 | 1990-02-09 |
| Personal code | 48506210000 (specimen) | 39002090000 (specimen) |
| Nationality / residence | Estonian / Estonia | Estonian / Estonia |
| Home address | Kadaka tee 12-5, 12915 Tallinn, Estonia | Riia 18-3, 51010 Tartu, Estonia |
| ID card number | AC0000123 (specimen) | AC0000456 (specimen) |
| ID card issued / expires | 2022-05-10 / 2027-05-10 | 2023-11-02 / 2028-11-02 |

Company: Nordvalo Logistics OÜ · registry code 16987231 · status **active** ·
registered 2023-03-14 · Peterburi tee 46, 11415 Tallinn, Estonia · share capital
25,000 EUR · VAT number EE102987231 (specimen) · VAT registered since 2023-06-01.

## 3. The documents (15 checklist items; file types matter)

| # | Checklist item on the portal | File | Must show |
|---|---|---|---|
| 01 | Certificate of incorporation | PDF | company name, registry code, incorporation date 2023-03-14, registered address |
| 02 | Company registration extract | PDF | name, registry code, status **Active**, registered address, both board members, both shareholders; **dated 2026-09-28** (limit 90 days) |
| 03 | Tax registration certificate | PDF | name, registry code, VAT number, VAT registration date |
| 04 | Register of directors | PDF | Anneli Rebane and Tanel Sepp, appointed 2023-03-14; **dated 2026-09-28** (limit 90 days) |
| 05 | List of authorised signatories | PDF | Anneli Rebane and Tanel Sepp, each may sign alone |
| 06 | Declaration of beneficial owners | PDF | Anneli Rebane 60% and Tanel Sepp 40%, direct shareholding; dated 2026-09-20 |
| 07 | Identity document — Anneli Rebane | **JPG or PNG** | full name, date of birth, document number, expiry 2027-05-10 |
| 08 | Identity document — Tanel Sepp | **JPG or PNG** | full name, date of birth, document number, expiry 2028-11-02 |
| 09 | Proof of address — Anneli Rebane | PDF | utility bill to her home address, **dated 2026-09-15** (limit 90 days) |
| 10 | Proof of address — Tanel Sepp | PDF | utility bill to his home address, **dated 2026-09-12** (limit 90 days) |
| 11 | Selfie — Anneli Rebane | **JPG or PNG** | a clear face picture |
| 12 | Selfie — Tanel Sepp | **JPG or PNG** | a clear face picture |
| 13 | Source of funds declaration | PDF | source: founders' share capital (25,000 EUR) and revenue from delivery contracts; expected monthly volume 65,000 EUR |
| 14 | Description of your business activity | PDF | courier and last-mile delivery, ~40 online-shop clients, 18 vans, card spend on fuel, van leasing and route software |
| 15 | Business bank statement | PDF | three months (July–September 2026), account in the company's name, card spend around 60,000–65,000 EUR a month; dated 2026-09-30 |

**Reminder story:** keep **15 (bank statement)** back. After the other 14 are
in, click **Request** on the bank statement's row (Checklist tab); the
reminder goes out 14 days later on the demo clock (**+7 days** twice), and
uploading 15 in the portal stops any further reminder.

## 4. How the files are read

The 15 files in this folder are a **recognised demo pack**: `manifest.json`
records each file's SHA-256, its verdict and its fields. Uploaded in the
portal, each is recognised by its bytes and its fields are filled in at once,
labelled *"mock: recognised demo sample file"* - no analyst types anything.
Checked on a copy of the database on 2026-09-30: files 01-14 uploaded, every
field filled in, registry name / number / address / directors all match, case
Ready for decision, bank statement still owed. The business bank statement is
in `upload_during_demo/`.

Only these exact files are recognised. A re-saved or edited copy has different
bytes and goes through the ordinary path (the typing form below, or the live
model with `WALLESTER_UC4_EXTRACTION_FOR_NEW_UPLOADS=live_if_available`).

### What an analyst would type in (only for files that are not recognised)

First accept each file's visual check ("visual check not run in mock mode"),
then type these. Dates are YYYY-MM-DD. * = required. Rehearsed on a copy of
the database on 2026-09-30: with these values every registry check matches and
the case reaches Ready for decision.

| Document | Field | Value |
|---|---|---|
| Certificate of incorporation | company_name * | Nordvalo Logistics OÜ |
| | registration_number * | 16987231 |
| | incorporation_date | 2023-03-14 |
| | registered_address | Peterburi tee 46, 11415 Tallinn, Estonia |
| Company registration extract | company_name * | Nordvalo Logistics OÜ |
| | registration_number * | 16987231 |
| | registered_address * | Peterburi tee 46, 11415 Tallinn, Estonia |
| | entity_status * | active |
| | legal_name | Nordvalo Logistics OÜ |
| | registered_shareholder | Anneli Rebane (60%); Tanel Sepp (40%) |
| Tax registration certificate | vat_number | EE102987231 |
| | tax_number | 16987231 |
| Register of directors | director_name * | Anneli Rebane |
| | director_name_2 | Tanel Sepp |
| | appointment_date | 2023-03-14 |
| List of authorised signatories | signatory_name * | Anneli Rebane |
| Declaration of beneficial owners | ubo_name * | Anneli Rebane |
| | ownership_percentage * | 60 |
| | control_basis | Direct shareholding |
| | indirect_ownership_path | *(blank)* |
| | ubo_name_2 | Tanel Sepp |
| ID — Anneli Rebane | full_name * | Anneli Rebane |
| | expiry_date | 2027-05-10 |
| | date_of_birth | 1985-06-21 |
| | document_number | AC0000123 |
| ID — Tanel Sepp | full_name * | Tanel Sepp |
| | expiry_date | 2028-11-02 |
| | date_of_birth | 1990-02-09 |
| | document_number | AC0000456 |
| Proof of address — Anneli Rebane | document_date * | 2026-09-15 |
| | address | Kadaka tee 12-5, 12915 Tallinn, Estonia |
| | full_name | Anneli Rebane |
| | issuer | Harju Demo Energy AS |
| Proof of address — Tanel Sepp | document_date * | 2026-09-12 |
| | address | Riia 18-3, 51010 Tartu, Estonia |
| | full_name | Tanel Sepp |
| | issuer | Harju Demo Energy AS |
| Selfies (both) | *(no fields — accepting the visual check is all)* | |
| Source of funds declaration | declared_source * | Founders' share capital (25,000 EUR) and revenue from delivery contracts |
| | expected_monthly_volume | 65000 EUR |
| Business activity description | declared_industry * | Courier and last-mile delivery |
| | expected_card_usage | Fuel, van leasing and route-planning software, about 65,000 EUR a month |
| Business bank statement (kept back) | account_holder * | Nordvalo Logistics OÜ |

The reminder counts 14 days from when the application was sent (the case is
put on the customer then), so after sending the request, **+7 days** twice.

## 5. Prompt to paste into Claude

> Create a set of synthetic KYB demo documents for a fictional Estonian company,
> for a software demo. Every page and image must carry a visible diagonal
> watermark "SPECIMEN — SYNTHETIC TEST DOCUMENT". Use only invented issuers
> (e.g. "Demo Business Register", "Demo Tax Authority", "Harju Demo Energy AS",
> "Baltic Demo Bank AS"), never a real authority, bank or utility, and no real
> logos, seals or signatures.
>
> [paste sections 2 and 3 of this file here]
>
> Make items 01–06, 09, 10 and 13–15 as one-page A4 PDFs (the bank statement
> may be 2–3 pages), with typed text that states each "Must show" value
> exactly as given. Make 07 and 08 as PNG ID-card images (1600×1000 px) with
> crisp printed text and a simple drawn portrait placeholder, and 11 and 12 as
> PNG selfies (1200×1200 px), a simple sharp illustrated head-and-shoulders
> portrait with the name in small text. Name the files
> 01_certificate_of_incorporation.pdf, 02_company_registration_extract.pdf,
> 03_tax_registration_certificate.pdf, 04_register_of_directors.pdf,
> 05_list_of_authorised_signatories.pdf, 06_declaration_of_beneficial_owners.pdf,
> 07_identity_document_anneli_rebane.png, 08_identity_document_tanel_sepp.png,
> 09_proof_of_address_anneli_rebane.pdf, 10_proof_of_address_tanel_sepp.pdf,
> 11_selfie_anneli_rebane.png, 12_selfie_tanel_sepp.png,
> 13_source_of_funds_declaration.pdf, 14_business_activity_description.pdf,
> 15_business_bank_statement.pdf, and give them to me as one zip.

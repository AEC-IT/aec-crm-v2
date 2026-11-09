# AEC Studies CRM v2

Python Flask, same stack as the old CRM, rebuilt with every old feature plus the improvements below.

## Run it on your computer
```bash
pip install -r requirements.txt
python run.py            # open http://localhost:5000
```
First login: `admin@aec-edu.com` / `ChangeMe@123` — change it straight away (top bar → Password),
or set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before the first run.

## Put it online (Render)
1. Push this folder to a GitHub repo.
2. In Render → New → Blueprint → pick the repo. `render.yaml` creates the web app, a PostgreSQL
   database (with daily backups) and a disk for uploaded documents.
3. Fill in `ADMIN_EMAIL`, `ADMIN_PASSWORD`, and the WhatsApp values (below).
The paid "starter" plan is used so the CRM never goes to sleep (the old one is slow because the free plan sleeps).

## Move the old data in
Either from CSV exports (old CRM → Reports → Export All Data):
```bash
python migrate.py csv leads old_leads.csv --dry-run     # check first
python migrate.py csv leads old_leads.csv
python migrate.py csv universities old_universities.csv
python migrate.py csv payments old_payments.csv
```
or straight from the old database: `python migrate.py db <old DATABASE_URL>`.
Duplicate phone numbers are merged into one lead; duplicate universities are skipped.

## WhatsApp (Meta Cloud API)
Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`.
In Meta → WhatsApp → Configuration, set the webhook to `https://<your-site>/webhooks/whatsapp`.
Every new chat now becomes a lead and is given an owner automatically — no more "Unknown User".

## Lead capture API (website, Meta Lead Ads via Zapier/Make, Tally, AI bots)
Admin → API Integrations → create a key, then `POST /api/v1/leads` with header `X-API-Key`.
Tally: webhook URL `/api/v1/leads?key=<key>`. Repeat enquiries never create duplicates.

## What's in it
| Area | Features |
|---|---|
| Dashboard | KPIs, overdue follow-ups, hottest leads, today's appointments, 16-stage pipeline, team performance |
| Leads | List with filters, search, sort, bulk assign/stage/archive, CSV export; Kanban drag-and-drop; closed/lost; archived |
| Lead page | Clickable stage bar, call/note/WhatsApp logging with outcomes, follow-ups, full timeline (audit log), universities & applications, duplicate merge, owner + auto-assign, portal & brochure links |
| **New** | Duplicate guard + merge, auto-assignment (branch, language, workload cap), lead score 0–100 (hot/warm/cold), **Today's Call List**, **student/parent portal** with progress + document upload, brochure open tracking, campaign & ad name tracking, visa & flight fields, IST times everywhere |
| Documents | 30-item checklist, upload, verify/reject, notes, expiry dates, student uploads |
| Universities | Fees, hostel, duration, recognition, intakes, seats, commission |
| Communication | WhatsApp inbox + threads, brochures, appointments |
| Finance | Charges with GST and due dates, payments, receipt numbers, printable receipts, dues list, period filters, CSV export |
| Reports | Overview, funnel, pipeline health (stuck leads), counsellor performance (calls, overdue, untouched), source & campaign, monthly trends, follow-ups, enrollment projection, full exports |
| Front desk | Visitors with check-out, couriers in/out, bills, inventory |
| Office | Requests/tasks (purchase, travel, onboarding, routed messages), expenses, records vault with renewal alerts |
| Admin | Users (10 roles, branch, languages, lead cap), branches, automation rules, API keys, notifications |

Roles control what each person sees: counsellors and telecallers only see their own leads; finance and admin
pages are limited to the right roles.

## Phase 2 (added)
- **WhatsApp templates** (Admin → WhatsApp Templates): ready-made messages with `{name}`, `{course}`, `{country}`,
  `{counsellor}`, `{stage}`, `{due}`, `{fee_type}`, `{due_date}`, `{pay_link}`, `{portal}`. Pick one on any lead page,
  preview it filled in, and send. Within 24 h of the student's last message it goes as normal text; after that,
  Meta only allows approved templates — put the approved template's name in "Meta template name" and use the
  variables in the same order as in Meta.
- **Payment reminders**: "Remind" button on every unpaid charge, and "Remind all overdue" on the Finance dashboard
  (skips anyone reminded in the last 3 days). Every reminder is logged on the lead's timeline.
- **UPI pay page**: each student gets a private link (`/finance/pay/<link>`) with a "Pay with GPay / PhonePe / Paytm"
  button and a QR code for the exact amount. Set `UPI_ID` (e.g. `aecstudies@okicici`) and `UPI_NAME`.
  Staff still record the payment after checking the UTR — this is a direct UPI link, not a payment gateway.
- **2-step login**: top bar → 2-step. Scan the QR with Google/Microsoft Authenticator. Admins can reset it for
  someone who lost their phone (Users → Edit). Recommended for every admin and finance user.

**If v2 is already online with data**, these columns must be added once (PostgreSQL):
```sql
ALTER TABLE "user" ADD COLUMN totp_secret VARCHAR(64);
ALTER TABLE charge ADD COLUMN last_reminded_at TIMESTAMP;
```
(The new WhatsApp templates table is created automatically.)

## Tests
`python -m pytest -q tests` — loads every page and checks the main flows, including phase 2.

## Next phase (suggested)
AI chat agent and AI training pages from the old CRM (need its prompts/API key), Razorpay gateway for automatic
payment confirmation, scheduled daily reminders, Meta ad-spend import for cost per admission.

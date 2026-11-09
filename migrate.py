"""
Move data from the old AEC CRM into the new one.

Two ways (use whichever you have):

  1) From CSV exports of the old CRM (Reports → Export All Data):
       python migrate.py csv leads old_leads.csv
       python migrate.py csv universities old_universities.csv
       python migrate.py csv payments old_payments.csv

  2) Straight from the old database (ask your developer / Render for its DATABASE_URL):
       python migrate.py db postgresql://user:pass@host/olddb
     It reads every table, maps columns with the same names, and reports anything it skipped.

Add --dry-run to see what would happen without saving.
Duplicate phone numbers are merged into one lead (notes from both are kept).
"""
import csv
import sys
from datetime import datetime, date
from app import create_app
from app.models import now_ist, db, Lead, University, User, Payment, Charge, Activity, STAGE_LABEL
from app.services import normalize_phone, new_token, next_receipt_no

# old stage text → new stage code (extend if your export shows others)
STAGE_MAP = {
    "new": "new_lead", "new lead": "new_lead", "new_lead": "new_lead",
    "attempted contact": "attempted_contact", "attempted_contact": "attempted_contact",
    "contacted": "connected", "connected": "connected", "qualified": "qualified",
    "counseling completed": "counseling_completed", "counselling completed": "counseling_completed",
    "university shortlisted": "university_shortlisted", "documents requested": "application_started",
    "application form": "application_started", "application started": "application_started",
    "application submitted": "application_submitted", "offer letter received": "offer_letter_received",
    "student confirmed": "student_confirmed", "visa process": "visa_process", "visa approved": "visa_approved",
    "flight booked": "flight_booked", "student departed": "student_departed", "enrolled": "enrolled",
    "alumni": "alumni", "dropped out": "dropped_out", "refund requested": "refund_requested",
    "refund processed": "refund_processed", "documents requested": "application_started",
    "application form": "application_started", "follow up": "connected", "no answer": "attempted_contact",
}
LOST_STAGES = {"not interested", "lost", "closed", "closed lost"}
ALIASES = {  # new field: possible old column names
    "name": ["name", "full_name", "student_name"], "phone": ["phone", "mobile", "phone_number"],
    "whatsapp": ["whatsapp", "whatsapp_number"], "email": ["email"], "course": ["course"],
    "country_preference": ["country_preference", "country"], "budget": ["budget"],
    "source": ["source", "lead_source"], "stage": ["stage", "status_stage", "pipeline_stage"],
    "temperature": ["temperature", "heat"], "location": ["location", "city"], "gender": ["gender"],
    "parent_occupation": ["parent_occupation"], "self_occupation": ["self_occupation"],
    "tenth_year": ["tenth_year"], "tenth_percent": ["tenth_percent"], "twelfth_year": ["twelfth_year"],
    "twelfth_percent": ["twelfth_percent"], "twelfth_group": ["twelfth_group"], "neet_score": ["neet_score"],
    "neet_year": ["neet_year"], "degree_name": ["degree_name"], "graduation_year": ["graduation_year"],
    "graduation_cgpa": ["graduation_cgpa"], "intended_intake": ["intended_intake", "intake"],
    "funding_source": ["funding_source"], "passport_status": ["passport_status"],
    "decision_maker": ["decision_maker"], "competitor": ["competitor"], "objection": ["objection"],
    "created_at": ["created_at", "created"], "notes": ["notes", "remarks", "comment"],
    "assigned_to": ["assigned_to", "assigned_to_email", "counselor", "assigned_to_id"],
    "next_followup_at": ["next_followup_at", "follow_up_date", "followup_date"],
}
NUM = {"budget", "tenth_percent", "twelfth_percent", "graduation_cgpa"}
INT = {"tenth_year", "twelfth_year", "neet_score", "neet_year", "graduation_year"}


def get(row, field):
    for k in ALIASES.get(field, [field]):
        for rk in (k, k.title(), k.upper(), k.replace("_", " ").title()):
            v = row.get(rk)
            if v not in (None, "", "None", "null", "—"):
                return v
    return None


def parse_dt(v):
    if isinstance(v, (datetime, date)):
        return v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time())
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %b %Y, %I:%M %p"):
        try:
            return datetime.strptime(str(v).strip(), fmt)
        except ValueError:
            pass
    return None


def import_leads(rows, dry):
    users = {u.email.lower(): u for u in User.query.all()}
    users_by_name = {u.name.lower(): u for u in User.query.all()}
    by_phone = {l.phone: l for l in Lead.query.all()}
    created = merged = skipped = 0
    for r in rows:
        name, phone = get(r, "name"), normalize_phone(get(r, "phone") or get(r, "whatsapp"))
        if not (name and phone):
            skipped += 1
            continue
        lead = by_phone.get(phone)
        is_new = lead is None
        if is_new:
            lead = Lead(name=name.strip(), phone=phone, portal_token=new_token())
        for f in ALIASES:
            if f in ("name", "phone", "notes", "assigned_to", "stage"):
                continue
            v = get(r, f)
            if v is None or (not is_new and getattr(lead, f, None) not in (None, "")):
                continue  # on merge, keep existing values
            try:
                if f in NUM: v = float(str(v).replace(",", "").replace("₹", ""))
                elif f in INT: v = int(float(v))
                elif f in ("created_at", "next_followup_at"): v = parse_dt(v)
                elif f == "whatsapp": v = normalize_phone(v)
            except ValueError:
                continue
            setattr(lead, f, v)
        st = (get(r, "stage") or "").strip().lower().replace("_", " ")
        if st in LOST_STAGES:
            lead.status = "lost"
            lead.lost_reason = lead.lost_reason or st.capitalize()
        if (r.get("status") or "") in ("lost", "archived"):
            lead.status = r["status"]
        if STAGE_MAP.get(st) or st.replace(" ", "_") in STAGE_LABEL:
            lead.stage = STAGE_MAP.get(st, st.replace(" ", "_"))
        if lead.gender:
            lead.gender = lead.gender.capitalize()
        if r.get("followup_note") and not lead.next_followup_note:
            lead.next_followup_note = r["followup_note"][:250]
        a = str(get(r, "assigned_to") or "").strip().lower()
        u = users.get(a) or users_by_name.get(a) or (db.session.get(User, int(a)) if a.isdigit() else None)
        if u and not lead.assigned_to_id:
            lead.assigned_to_id = u.id
        elif a and not u and is_new and a != "unassigned":
            db.session.flush()
            db.session.add(Activity(lead_id=lead.id, kind="system", text=f"Owner in old CRM: {get(r, 'assigned_to')}"))
        if not lead.whatsapp:
            lead.whatsapp = lead.phone
        lead.compute_score()
        if is_new:
            db.session.add(lead)
            by_phone[phone] = lead
            created += 1
        else:
            merged += 1
        db.session.flush()
        note = get(r, "notes")
        if note and note.strip() != "No notes yet.":
            db.session.add(Activity(lead_id=lead.id, kind="note", text=f"[Imported] {note}"))
        db.session.add(Activity(lead_id=lead.id, kind="system", text="Imported from old CRM" + ("" if is_new else " (duplicate merged)")))
    print(f"Leads: {created} created, {merged} duplicates merged, {skipped} skipped (no name/phone)")
    (db.session.rollback if dry else db.session.commit)()


def import_universities(rows, dry):
    seen = {(u.name.lower(), (u.country or "").lower()) for u in University.query.all()}
    n = 0
    for r in rows:
        name = get(r, "name")
        if not name:
            continue
        key = (name.strip().lower(), (r.get("country") or "").lower())
        if key in seen:
            continue  # removes the duplicates in the old list
        seen.add(key)
        fee = r.get("fee_usd_per_year") or r.get("fees") or r.get("fee")
        try:
            fee = float(str(fee).replace(",", "").replace("USD", "")) if fee else None
        except ValueError:
            fee = None
        db.session.add(University(name=name.strip(), country=r.get("country"), courses=r.get("courses"),
                                  fee_usd_per_year=fee, recognition=r.get("recognition"), notes=r.get("notes") or None))
        n += 1
    print(f"Universities: {n} added (duplicates skipped)")
    (db.session.rollback if dry else db.session.commit)()


def import_payments(rows, dry):
    n = 0
    for r in rows:
        phone = normalize_phone(r.get("phone") or r.get("student_phone"))
        lead = Lead.query.filter_by(phone=phone).first() if phone else \
            Lead.query.filter(Lead.name.ilike(r.get("student") or r.get("name") or "~")).first()
        if not lead:
            print("  no matching lead for payment row:", r)
            continue
        amt = float(str(r.get("amount", 0)).replace(",", "").replace("₹", ""))
        ch = Charge(lead_id=lead.id, fee_type=r.get("for") or r.get("fee_type") or "Service Charge", amount=amt)
        db.session.add(ch)
        db.session.flush()
        db.session.add(Payment(lead_id=lead.id, charge_id=ch.id, amount=amt, mode=r.get("mode"),
                               reference=r.get("reference"), paid_on=(parse_dt(r.get("date")) or now_ist()).date(),
                               receipt_no=next_receipt_no()))
        db.session.flush()
        n += 1
    print(f"Payments: {n} imported")
    (db.session.rollback if dry else db.session.commit)()


def from_db(url, dry):
    from sqlalchemy import create_engine, inspect, text
    eng = create_engine(url.replace("postgres://", "postgresql://", 1))
    tables = inspect(eng).get_table_names()
    print("Old database tables:", ", ".join(tables))
    with eng.connect() as c:
        def rows(t):
            return [dict(r._mapping) for r in c.execute(text(f'SELECT * FROM "{t}"'))]
        users_t = next((t for t in tables if t in ("users", "user")), None)
        if users_t:
            for r in rows(users_t):
                if r.get("email") and not User.query.filter_by(email=r["email"].lower()).first():
                    u = User(name=r.get("name") or r["email"], email=r["email"].lower(), role=r.get("role") or "counselor",
                             password_hash=r.get("password_hash"))  # same werkzeug hashing → old passwords keep working
                    db.session.add(u)
            db.session.commit()
            print("Users copied (old passwords still work if the old app used werkzeug hashing).")
        uni_t = next((t for t in tables if t in ("universities", "university")), None)
        if uni_t:
            import_universities(rows(uni_t), dry)
        lead_t = next((t for t in tables if t in ("leads", "lead")), None)
        if lead_t:
            import_leads(rows(lead_t), dry)
        others = [t for t in tables if t not in (users_t, uni_t, lead_t)]
        print("Not auto-mapped yet (send me their column lists to add):", ", ".join(others))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    dry = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        from app.seed import seed
        seed()
        if sys.argv[1] == "csv":
            kind, path = sys.argv[2], sys.argv[3]
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            {"leads": import_leads, "universities": import_universities, "payments": import_payments}[kind](rows, dry)
        elif sys.argv[1] == "db":
            from_db(sys.argv[2], dry)
        if dry:
            print("(dry run — nothing saved)")


if __name__ == "__main__":
    main()

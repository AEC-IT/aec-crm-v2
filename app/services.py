"""Business logic: phone normalising, duplicates, auto-assignment, automation, notifications."""
import re
import secrets
from datetime import datetime
from sqlalchemy import or_, func
from .models import now_ist
from .models import db, Lead, User, Activity, Notification, AutomationRule, STAGE_LABEL


def normalize_phone(raw):
    """Return +91XXXXXXXXXX style numbers so duplicates match."""
    if not raw:
        return raw
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    return "+" + digits if digits else None


def find_duplicates(phone=None, email=None, exclude_id=None):
    conds = []
    p = normalize_phone(phone)
    if p:
        conds += [Lead.phone == p, Lead.whatsapp == p]
    if email:
        conds.append(func.lower(Lead.email) == email.lower().strip())
    if not conds:
        return []
    q = Lead.query.filter(or_(*conds))
    if exclude_id:
        q = q.filter(Lead.id != exclude_id)
    return q.all()


def log(lead, text, kind="system", user=None, outcome=None):
    a = Activity(lead_id=lead.id, text=text, kind=kind, outcome=outcome,
                 user_id=user.id if user else None)
    db.session.add(a)
    return a


def notify(user_id, text, link=None):
    if user_id:
        db.session.add(Notification(user_id=user_id, text=text, link=link))


def auto_assign(lead):
    """Least-loaded active counsellor/telecaller in the lead's branch who speaks its language."""
    q = User.query.filter(User.active.is_(True), User.accepts_auto_assign.is_(True),
                          User.role.in_(["counselor", "telecaller"]))
    if lead.branch_id:
        q = q.filter(or_(User.branch_id == lead.branch_id, User.branch_id.is_(None)))
    candidates = q.all()
    if lead.preferred_language:
        lang = [u for u in candidates if lead.preferred_language.lower() in (u.languages or "").lower()]
        candidates = lang or candidates
    best, best_load = None, None
    for u in candidates:
        load = Lead.query.filter_by(assigned_to_id=u.id, status="open").count()
        if load >= (u.max_open_leads or 10**6):
            continue
        if best_load is None or load < best_load:
            best, best_load = u, load
    if best:
        lead.assigned_to_id = best.id
        notify(best.id, f"New lead assigned: {lead.name}", f"/leads/{lead.id}")
    return best


def merge_leads(keep, other, user):
    """Move everything from `other` into `keep`, fill blank fields, delete `other`."""
    for col in Lead.__table__.columns.keys():
        if col in ("id", "created_at"):
            continue
        if getattr(keep, col) in (None, "") and getattr(other, col) not in (None, ""):
            setattr(keep, col, getattr(other, col))
    for rel in ("activities", "documents", "applications", "charges"):
        for obj in getattr(other, rel).all():
            obj.lead_id = keep.id
    from .models import Payment, WhatsAppMessage
    Payment.query.filter_by(lead_id=other.id).update({"lead_id": keep.id})
    WhatsAppMessage.query.filter_by(lead_id=other.id).update({"lead_id": keep.id})
    log(keep, f"Merged duplicate lead #{other.id} ({other.name})", "system", user)
    db.session.delete(other)


def change_stage(lead, new_stage, user=None):
    if new_stage == lead.stage:
        return
    old = lead.stage
    lead.stage = new_stage
    lead.compute_score()
    log(lead, f"Stage: {STAGE_LABEL.get(old, old)} → {STAGE_LABEL.get(new_stage, new_stage)}", "stage", user)
    run_rules("stage_changed", lead)


def run_rules(trigger, lead):
    """Very small rule engine. condition is 'field=value' (or blank = always)."""
    for r in AutomationRule.query.filter_by(trigger=trigger, active=True).all():
        if r.condition and "=" in r.condition:
            f, v = [x.strip() for x in r.condition.split("=", 1)]
            if str(getattr(lead, f, "")) != v:
                continue
        if r.action == "notify_user":
            target = int(r.action_value) if (r.action_value or "").isdigit() else lead.assigned_to_id
            notify(target, f"[{r.name}] {lead.name} — {lead.stage_label}", f"/leads/{lead.id}")
        elif r.action == "assign" and (r.action_value or "").isdigit():
            lead.assigned_to_id = int(r.action_value)
        elif r.action == "set_followup_days" and (r.action_value or "").isdigit():
            from datetime import timedelta
            lead.next_followup_at = now_ist() + timedelta(days=int(r.action_value))
        elif r.action == "whatsapp_template":
            log(lead, f"Automation queued WhatsApp template '{r.action_value}'", "whatsapp")


def new_token():
    return secrets.token_urlsafe(24)


def next_receipt_no(on=None):
    """AEC/2026-27/00001 — numbered per Indian financial year (April–March), never reused."""
    from .models import Payment
    d = on or now_ist().date()
    fy = f"{d.year}-{(d.year + 1) % 100:02d}" if d.month >= 4 else f"{d.year - 1}-{d.year % 100:02d}"
    prefix = f"AEC/{fy}/"
    last = [int(r[0].rsplit("/", 1)[1]) for r in Payment.query.with_entities(Payment.receipt_no)
            .filter(Payment.receipt_no.like(prefix + "%")).all() if r[0].rsplit("/", 1)[1].isdigit()]
    return f"{prefix}{(max(last) + 1 if last else 1):05d}"


def upi_link(amount, note):
    """upi:// deep link — opens GPay / PhonePe / Paytm with amount filled in."""
    from urllib.parse import urlencode, quote
    from flask import current_app
    pa = current_app.config.get("UPI_ID")
    if not pa:
        return None
    q = urlencode({"pa": pa, "pn": current_app.config.get("UPI_NAME", "AEC Studies"),
                   "am": f"{amount:.2f}", "cu": "INR", "tn": note[:50]}, quote_via=quote)
    return "upi://pay?" + q


def fill_template(body, lead, charge=None, extra=None):
    """Replace {placeholders} with lead values; unknown ones are left blank."""
    from flask import url_for
    due = charge.amount - charge.paid if charge else lead.balance_due
    vals = {
        "name": lead.name.split()[0] if lead.name else "", "full_name": lead.name or "",
        "course": lead.course or "", "country": lead.country_preference or "",
        "counsellor": lead.assigned_to.name if lead.assigned_to else "AEC Studies",
        "stage": lead.stage_label, "due": f"₹{due:,.0f}",
        "fee_type": charge.fee_type if charge else "fees",
        "due_date": ("due by " + charge.due_date.strftime("%d %b %Y")) if charge and charge.due_date else "",
        "portal": url_for("leads.portal", token=lead.portal_token, _external=True) if lead.portal_token else "",
        "pay_link": url_for("finance.pay_page", token=lead.portal_token, _external=True) if lead.portal_token else "",
    }

    if extra:
        vals.update(extra)

    class Safe(dict):
        def __missing__(self, k):
            return ""
    return (body or "").format_map(Safe(vals)), vals


def save_upload(file_storage, fn):
    """Store an uploaded file in the database under name `fn`."""
    from .models import StoredFile
    sf = StoredFile.query.filter_by(name=fn).first() or StoredFile(name=fn)
    sf.data = file_storage.read()
    sf.mimetype = file_storage.mimetype or "application/octet-stream"
    db.session.add(sf)
    return fn


def serve_upload(fn):
    """Return a file from the database, falling back to the old upload folder."""
    import os
    from flask import current_app, send_from_directory, Response, abort
    from .models import StoredFile
    sf = StoredFile.query.filter_by(name=fn).first()
    if sf:
        return Response(sf.data, mimetype=sf.mimetype,
                        headers={"Content-Disposition": f'inline; filename="{fn}"'})
    if os.path.exists(os.path.join(current_app.config["UPLOAD_FOLDER"], fn)):
        return send_from_directory(current_app.config["UPLOAD_FOLDER"], fn)
    abort(404)


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _three(n):
    h, r = divmod(n, 100)
    return ((_ONES[h] + " Hundred" + (" " if r else "")) if h else "") + (_two(r) if r else "")


def amount_in_words(amount):
    """1,25,500.50 -> 'Rupees One Lakh Twenty Five Thousand Five Hundred and Fifty Paise Only'."""
    amount = round(float(amount or 0), 2)
    rupees, paise = int(amount), int(round((amount - int(amount)) * 100))
    parts = []
    for div, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        q, rupees = divmod(rupees, div)
        if q:
            parts.append((amount_in_words(q).replace("Rupees ", "").replace(" Only", "") if q >= 100 else _two(q)) + " " + name)
    if rupees:
        parts.append(_three(rupees))
    words = " ".join(parts) or "Zero"
    return "Rupees " + words + (f" and {_two(paise)} Paise" if paise else "") + " Only"

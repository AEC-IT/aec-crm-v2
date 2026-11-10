import csv
import io
from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response, current_app
from flask_login import current_user
from sqlalchemy import func
from .auth import requires
from .models import db, Charge, Payment, Lead, FEE_TYPES, PAY_MODES
from .services import log, next_receipt_no

bp = Blueprint("finance", __name__, url_prefix="/finance")


def period():
    a = request.args
    today = date.today()
    presets = {
        "today": (today, today), "yesterday": (today - timedelta(1), today - timedelta(1)),
        "7d": (today - timedelta(6), today), "30d": (today - timedelta(29), today),
        "month": (today.replace(day=1), today),
        "year": (today.replace(month=1, day=1), today),
    }
    if a.get("p") in presets:
        return presets[a["p"]]
    if a.get("from") or a.get("to"):
        return (date.fromisoformat(a["from"]) if a.get("from") else date(2000, 1, 1),
                date.fromisoformat(a["to"]) if a.get("to") else today)
    return date(2000, 1, 1), today


@bp.route("/dashboard")
@requires("finance")
def dashboard():
    d0, d1 = period()
    charges = Charge.query.filter(func.date(Charge.created_at) >= d0, func.date(Charge.created_at) <= d1).all()
    received = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.cancelled.isnot(True),
        Payment.paid_on >= d0, Payment.paid_on <= d1).scalar()
    by_type = {}
    for c in charges:
        t = by_type.setdefault(c.fee_type, [0, 0, 0])
        t[0] += 1; t[1] += c.amount; t[2] += c.paid
    due = {}
    for c in Charge.query.all():
        if c.amount > c.paid:
            due.setdefault(c.lead, [0, 0])
            due[c.lead][0] += c.amount; due[c.lead][1] += c.amount - c.paid
    overdue = [c for c in Charge.query.filter(Charge.due_date < date.today()).all() if c.status != "paid"]
    return render_template("finance_dashboard.html", charges=sorted(charges, key=lambda c: c.created_at, reverse=True)[:30],
                           billed=sum(c.amount for c in charges), paid=sum(c.paid for c in charges),
                           received=received, by_type=by_type, due=sorted(due.items(), key=lambda x: -x[1][1]),
                           overdue=overdue, d0=d0, d1=d1)


@bp.route("/payments")
@requires("finance")
def payments():
    d0, d1 = period()
    rows = Payment.query.filter(Payment.paid_on >= d0, Payment.paid_on <= d1).order_by(Payment.paid_on.desc()).all()
    if request.args.get("export") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Receipt", "Date", "Student", "Phone", "For", "Amount", "Mode", "Reference"])
        for p in rows:
            w.writerow([p.receipt_no, p.paid_on, p.lead.name if p.lead else "", p.lead.phone if p.lead else "",
                        p.charge.fee_type if p.charge else "Manual", p.amount, p.mode, p.reference])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=payments.csv"})
    return render_template("payments.html", rows=rows, total=sum(p.amount for p in rows if not p.cancelled), d0=d0, d1=d1)


@bp.route("/lead/<int:lid>", methods=["GET", "POST"])
@requires("finance")
def lead_charges(lid):
    lead = db.session.get(Lead, lid) or abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("action") == "charge":
            c = Charge(lead_id=lid, fee_type=f["fee_type"], description=f.get("description"),
                       amount=float(f["amount"]), gst_percent=float(f.get("gst_percent") or 0),
                       due_date=date.fromisoformat(f["due_date"]) if f.get("due_date") else None)
            db.session.add(c)
            log(lead, f"Charge raised: {c.fee_type} ₹{c.amount:,.0f}", "system", current_user)
        elif f.get("action") == "payment":
            amt = float(f["amount"])
            p = Payment(lead_id=lid, charge_id=int(f["charge_id"]) if f.get("charge_id") else None, amount=amt,
                        mode=f.get("mode"), reference=f.get("reference"),
                        paid_on=date.fromisoformat(f["paid_on"]) if f.get("paid_on") else date.today(),
                        receipt_no=None, recorded_by_id=current_user.id, description=f.get("description") or None)
            p.receipt_no = next_receipt_no(p.paid_on)
            db.session.add(p)
            log(lead, f"Payment received ₹{amt:,.0f} ({p.mode}) — receipt {p.receipt_no}", "system", current_user)
            db.session.commit()
            return redirect(url_for("finance.receipt", pid=p.id, send=1 if f.get("send_whatsapp") else None))
        db.session.commit()
        flash("Saved", "ok")
        return redirect(url_for("finance.lead_charges", lid=lid))
    return render_template("lead_charges.html", lead=lead, charges=lead.charges.all(),
                           payments=Payment.query.filter_by(lead_id=lid).order_by(Payment.paid_on.desc()).all(),
                           fee_types=FEE_TYPES, modes=PAY_MODES)


@bp.route("/receipt/<int:pid>")
@requires("finance")
def receipt(pid):
    from urllib.parse import quote
    p = db.session.get(Payment, pid) or abort(404)
    if request.args.get("send") and p.lead and not p.cancelled:
        ok, info = _send_receipt(p)
        flash(f"Receipt {p.receipt_no} created and sent on WhatsApp ✓" if ok else
              f"Receipt {p.receipt_no} created. {info} Use 'Open in WhatsApp' to send it from your phone.",
              "ok" if ok else "error")
        return redirect(url_for("finance.receipt", pid=pid))
    link = public_receipt_url(p)
    wa_text = (f"Dear {p.lead.name.split()[0]}, thank you for your payment of ₹{p.amount:,.0f} to AEC Studies. "
               f"Receipt {p.receipt_no}: {link}") if p.lead else ""
    wa_me = f"https://wa.me/{(p.lead.whatsapp or p.lead.phone).lstrip('+')}?text={quote(wa_text)}" if p.lead else None
    return render_template("receipt.html", public=False, link=link, wa_me=wa_me, **_receipt_ctx(p))


@bp.route("/charges")
@requires("finance")
def charges():
    status = request.args.get("status")
    rows = Charge.query.order_by(Charge.created_at.desc()).all()
    if status:
        rows = [c for c in rows if c.status == status]
    return render_template("charges.html", rows=rows, status=status)


# ------------------------------------------------------------------ UPI pay page + reminders
@bp.route("/pay/<token>")
def pay_page(token):
    """Public page for the student/parent: pending dues with UPI button and QR. No login."""
    from .services import upi_link
    lead = Lead.query.filter_by(portal_token=token).first_or_404()
    rows = [(c, c.amount - c.paid, upi_link(c.amount - c.paid, f"{lead.name} {c.fee_type}"))
            for c in lead.charges if c.amount > c.paid]
    return render_template("pay.html", lead=lead, rows=rows, total=sum(r[1] for r in rows))


def _payment_template():
    from .models import WhatsAppTemplate
    return (WhatsAppTemplate.query.filter_by(category="Payment", active=True).first()
            or WhatsAppTemplate.query.filter_by(active=True).first())


@bp.route("/remind/<int:cid>", methods=["POST"])
@requires("finance")
def remind(cid):
    from .comms import send_template_to_lead
    from .models import now_ist
    c = db.session.get(Charge, cid) or abort(404)
    tpl = _payment_template()
    if not tpl:
        flash("Create a WhatsApp template in the Payment category first", "error")
        return redirect(request.referrer or url_for("finance.dashboard"))
    ok, info = send_template_to_lead(tpl, c.lead, current_user, c)
    c.last_reminded_at = now_ist()
    db.session.commit()
    flash(f"Reminder sent to {c.lead.name}" if ok else info, "ok" if ok else "error")
    return redirect(request.referrer or url_for("finance.dashboard"))


@bp.route("/remind-overdue", methods=["POST"])
@requires("finance")
def remind_overdue():
    """Send the payment template to every overdue charge not reminded in the last 3 days."""
    from .comms import send_template_to_lead
    from .models import now_ist
    tpl = _payment_template()
    if not tpl:
        flash("Create a WhatsApp template in the Payment category first", "error")
        return redirect(url_for("finance.dashboard"))
    sent = failed = 0
    cutoff = now_ist() - timedelta(days=3)
    for c in Charge.query.filter(Charge.due_date < date.today()).all():
        if c.status == "paid" or (c.last_reminded_at and c.last_reminded_at > cutoff):
            continue
        ok, _ = send_template_to_lead(tpl, c.lead, current_user, c)
        c.last_reminded_at = now_ist()
        sent += ok
        failed += not ok
    db.session.commit()
    flash(f"Reminders: {sent} sent, {failed} not delivered (saved on the lead timeline)", "ok" if not failed else "error")
    return redirect(url_for("finance.dashboard"))


# ------------------------------------------------------------------ receipts
def _receipt_ctx(p):
    from .services import amount_in_words
    from .models import Branch
    branch = (p.lead.branch if p.lead and p.lead.branch else None) or Branch.query.first()
    balance = p.lead.balance_due if p.lead else 0
    return dict(p=p, words=amount_in_words(p.amount), branch=branch, balance=balance,
                company=current_app.config["COMPANY_NAME"], gstin=current_app.config["COMPANY_GSTIN"])


def public_receipt_url(p):
    return url_for("finance.receipt_public", token=p.lead.portal_token, pid=p.id, _external=True) \
        if p.lead and p.lead.portal_token else None


@bp.route("/receipts")
@requires("finance")
def receipts():
    d0, d1 = period()
    q = Payment.query.filter(Payment.paid_on >= d0, Payment.paid_on <= d1)
    s = (request.args.get("q") or "").strip()
    if s:
        q = q.join(Lead).filter((Lead.name.ilike(f"%{s}%")) | (Lead.phone.ilike(f"%{s}%")) |
                                (Payment.receipt_no.ilike(f"%{s}%")) | (Payment.reference.ilike(f"%{s}%")))
    rows = q.order_by(Payment.paid_on.desc(), Payment.id.desc()).all()
    if request.args.get("export") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Receipt no", "Date", "Student", "Phone", "For", "Amount", "Mode", "Reference", "Status"])
        for p in rows:
            w.writerow([p.receipt_no, p.paid_on, p.lead.name if p.lead else "", p.lead.phone if p.lead else "",
                        p.purpose, p.amount, p.mode, p.reference, "Cancelled" if p.cancelled else "Valid"])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=receipts.csv"})
    valid = [p for p in rows if not p.cancelled]
    by_mode = {}
    for p in valid:
        by_mode[p.mode or "Other"] = by_mode.get(p.mode or "Other", 0) + p.amount
    return render_template("receipts.html", rows=rows, total=sum(p.amount for p in valid), count=len(valid),
                           cancelled=len(rows) - len(valid), by_mode=by_mode, d0=d0, d1=d1)


@bp.route("/receipts/new", methods=["GET", "POST"])
@requires("finance")
def receipt_new():
    """Quick receipt: pick the student, amount and mode — the receipt opens ready to print or send."""
    lead = db.session.get(Lead, request.values.get("lead_id", type=int)) if request.values.get("lead_id") else None
    if request.method == "POST":
        f = request.form
        if not lead:
            flash("Choose the student", "error")
            return redirect(url_for("finance.receipt_new"))
        try:
            amt = float(f["amount"])
            assert amt > 0
        except (ValueError, AssertionError):
            flash("Enter an amount above 0", "error")
            return redirect(url_for("finance.receipt_new", lead_id=lead.id))
        paid_on = date.fromisoformat(f["paid_on"]) if f.get("paid_on") else date.today()
        p = Payment(lead_id=lead.id, charge_id=f.get("charge_id", type=int) or None, amount=amt, mode=f.get("mode"),
                    reference=f.get("reference") or None, paid_on=paid_on, description=f.get("description") or None,
                    recorded_by_id=current_user.id, receipt_no=next_receipt_no(paid_on))
        db.session.add(p)
        log(lead, f"Receipt {p.receipt_no}: ₹{amt:,.0f} received ({p.mode})", "system", current_user)
        db.session.commit()
        if f.get("send_whatsapp"):
            return redirect(url_for("finance.receipt", pid=p.id, send=1))  # page sends it straight away
        flash(f"Receipt {p.receipt_no} created", "ok")
        return redirect(url_for("finance.receipt", pid=p.id))
    leads = Lead.query.filter(Lead.status != "archived").order_by(Lead.name).with_entities(Lead.id, Lead.name, Lead.phone).all()
    charges = [c for c in lead.charges if c.status != "paid"] if lead else []
    return render_template("receipt_new.html", lead=lead, leads=leads, charges=charges, fee_types=FEE_TYPES,
                           modes=PAY_MODES, today=date.today())


@bp.route("/receipt/<int:pid>/cancel", methods=["POST"])
@requires("finance")
def receipt_cancel(pid):
    p = db.session.get(Payment, pid) or abort(404)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("Give a reason to cancel the receipt", "error")
        return redirect(url_for("finance.receipt", pid=pid))
    p.cancelled, p.cancel_reason = True, reason
    if p.lead:
        log(p.lead, f"Receipt {p.receipt_no} cancelled: {reason}", "system", current_user)
    db.session.commit()
    flash(f"Receipt {p.receipt_no} cancelled — it stays on record but no longer counts as paid", "ok")
    return redirect(url_for("finance.receipt", pid=pid))


def _send_receipt(p):
    """Send the receipt link to the student on WhatsApp (API) and save it on their chat. Returns (ok, info)."""
    from .comms import send_whatsapp
    from .models import WhatsAppMessage, now_ist
    lead = p.lead
    body = (f"Dear {lead.name.split()[0]}, thank you for your payment of ₹{p.amount:,.0f} to AEC Studies. "
            f"Receipt {p.receipt_no}: {public_receipt_url(p)}")
    phone = lead.whatsapp or lead.phone
    ok, info = send_whatsapp(phone, body)
    db.session.add(WhatsAppMessage(lead_id=lead.id, phone=phone, direction="out", body=body, read=True))
    log(lead, body + ("" if ok else f"\n⚠ {info}"), "whatsapp", current_user)
    lead.last_contacted_at = now_ist()
    db.session.commit()
    return ok, info


@bp.route("/receipt/<int:pid>/whatsapp", methods=["POST"])
@requires("finance")
def receipt_whatsapp(pid):
    p = db.session.get(Payment, pid) or abort(404)
    ok, info = _send_receipt(p)
    flash("Receipt sent on WhatsApp ✓" if ok else info + " Use 'Open in WhatsApp' to send it from your phone.",
          "ok" if ok else "error")
    return redirect(url_for("finance.receipt", pid=pid))


@bp.route("/r/<token>/<int:pid>")
def receipt_public(token, pid):
    """Receipt page the student or parent opens from the link — no login, only with their private token."""
    p = Payment.query.join(Lead).filter(Payment.id == pid, Lead.portal_token == token).first_or_404()
    return render_template("receipt.html", public=True, **_receipt_ctx(p))

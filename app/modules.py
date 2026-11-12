"""Generic CRUD for the simpler modules + documents, front desk, office, vault, admin."""
import os
import secrets
from datetime import datetime, date, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort, send_from_directory,
                   current_app)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func
from .auth import requires
from .models import now_ist
from .models import (db, University, Brochure, Visitor, Courier, Bill, InventoryItem, OfficeRecord, Appointment,
                     OfficeTask, Expense, Branch, AutomationRule, Integration, Notification, Lead, User, Document, WhatsAppTemplate,
                     DOC_CHECKLIST, COURSES, COUNTRIES, PAY_MODES)
from .services import save_upload, serve_upload, log, notify

bp = Blueprint("modules", __name__)


def users_opts():
    return [(u.id, f"{u.name} ({u.role_label})") for u in User.query.filter_by(active=True).order_by(User.name)]


def branch_opts():
    return [(b.id, b.name) for b in Branch.query.filter_by(active=True)]


# Each entry: model, module permission, title, fields [(name, label, type, options)], list columns
# types: text, textarea, number, date, datetime, select, fk, bool
CRUD = {
    "universities": dict(model=University, perm="universities", title="Universities", fields=[
        ("name", "Name", "text", None), ("country", "Country", "select", COUNTRIES), ("city", "City", "text", None),
        ("courses", "Courses (comma separated)", "text", None),
        ("fee_usd_per_year", "Tuition USD / year", "number", None),
        ("hostel_usd_per_year", "Hostel USD / year", "number", None),
        ("duration_years", "Duration (years)", "number", None),
        ("recognition", "Recognition (NMC, WHO, ECFMG…)", "text", None), ("intakes", "Intakes", "text", None),
        ("seats_available", "Seats available", "number", None), ("commission_percent", "Commission %", "number", None),
        ("website", "Website", "text", None), ("notes", "Notes", "textarea", None), ("active", "Active", "bool", None)],
        cols=["name", "country", "courses", "fee_usd_per_year", "recognition", "seats_available"],
        search=["name", "country", "courses"], filter="country"),
    "visitors": dict(model=Visitor, perm="frontdesk", title="Visitor Log", fields=[
        ("name", "Visitor name", "text", None), ("phone", "Phone", "text", None), ("purpose", "Purpose", "text", None),
        ("meeting_with_id", "Meeting with", "fk", users_opts), ("branch_id", "Branch", "fk", branch_opts),
        ("in_time", "In time", "datetime", None), ("out_time", "Out time", "datetime", None)],
        cols=["in_time", "name", "phone", "purpose", "out_time"], search=["name", "phone"], order="in_time"),
    "couriers": dict(model=Courier, perm="frontdesk", title="Courier Register", fields=[
        ("direction", "In / Out", "select", ["in", "out"]), ("party", "From / To", "text", None),
        ("item", "Item", "text", None), ("courier_company", "Courier company", "text", None),
        ("tracking_no", "Tracking no.", "text", None), ("handled_by", "Handled by", "text", None),
        ("branch_id", "Branch", "fk", branch_opts)],
        cols=["created_at", "direction", "party", "item", "courier_company", "tracking_no"],
        search=["party", "item", "tracking_no"], filter="direction", order="created_at"),
    "bills": dict(model=Bill, perm="frontdesk", title="Bills & Utilities", fields=[
        ("name", "Bill name", "text", None),
        ("bill_type", "Type", "select", ["Electricity", "Water", "Internet", "Phone", "Rent", "Maintenance", "Other"]),
        ("consumer_no", "Consumer no.", "text", None), ("provider", "Provider", "text", None),
        ("amount", "Amount ₹", "number", None), ("due_date", "Due date", "date", None),
        ("frequency", "Frequency", "select", ["Monthly", "Bi-monthly", "Quarterly", "Yearly", "One-time"]),
        ("status", "Status", "select", ["pending", "paid"]), ("paid_on", "Paid on", "date", None),
        ("paid_mode", "Paid via", "select", PAY_MODES), ("branch_id", "Branch", "fk", branch_opts)],
        cols=["name", "bill_type", "consumer_no", "amount", "due_date", "status"], search=["name", "consumer_no"],
        filter="status", order="due_date"),
    "inventory": dict(model=InventoryItem, perm="frontdesk", title="Office Inventory", fields=[
        ("name", "Item", "text", None), ("description", "Description", "text", None),
        ("category", "Category", "select", ["Document / File", "Equipment / Device", "Furniture", "Stationery",
                                             "Key / Access Card", "Other"]),
        ("branch_id", "Branch", "fk", branch_opts), ("qty", "Qty", "number", None), ("location", "Location", "text", None),
        ("responsible", "Responsible person", "text", None), ("serial", "Serial / Ref", "text", None),
        ("active", "Active", "bool", None)],
        cols=["name", "category", "qty", "location", "responsible", "serial"], search=["name", "serial"],
        filter="category"),
    "records": dict(model=OfficeRecord, perm="office", title="Office Records & Compliance", fields=[
        ("category", "Category", "select", ["Bank Account", "Utility", "Rent Paid", "Property Rented Out", "Vehicle",
                                             "Tax & Compliance", "Insurance", "License", "Other"]),
        ("title", "Title", "text", None), ("reference", "Account / Ref no.", "text", None),
        ("branch_id", "Branch", "fk", branch_opts), ("next_due", "Next due / renewal", "date", None),
        ("amount", "Amount ₹", "number", None), ("notes", "Notes", "textarea", None)],
        cols=["category", "title", "reference", "next_due", "amount"], search=["title", "reference"],
        filter="category", order="next_due"),
    "appointments": dict(model=Appointment, perm="leads", title="Appointments", fields=[
        ("title", "Title", "text", None), ("starts_at", "Date & time", "datetime", None),
        ("user_id", "With staff", "fk", users_opts), ("mode", "Mode", "select", ["Office", "Phone", "Video"]),
        ("lead_id", "Lead ID (optional)", "number", None),
        ("status", "Status", "select", ["scheduled", "done", "no-show", "cancelled"]), ("notes", "Notes", "text", None)],
        cols=["starts_at", "title", "mode", "status"], search=["title"], filter="status", order="starts_at"),
    "tasks": dict(model=OfficeTask, perm="office", title="Office Requests & Tasks", fields=[
        ("kind", "Type", "select", ["purchase", "maintenance", "travel", "onboarding", "message"]),
        ("title", "Title", "text", None), ("details", "Details", "textarea", None), ("amount", "Amount ₹", "number", None),
        ("assigned_to_id", "Assign to", "fk", users_opts), ("branch_id", "Branch", "fk", branch_opts),
        ("due_date", "Due date", "date", None), ("status", "Status", "select", ["open", "in progress", "done", "rejected"])],
        cols=["kind", "title", "amount", "due_date", "status"], search=["title"], filter="kind", order="created_at"),
    "expenses": dict(model=Expense, perm="office", title="Expenses & Petty Cash", fields=[
        ("spent_on", "Date", "date", None),
        ("category", "Category", "select", ["Petty cash", "Travel", "Stationery", "Marketing", "Food", "Repairs",
                                             "Salary advance", "Other"]),
        ("description", "Description", "text", None), ("amount", "Amount ₹", "number", None),
        ("mode", "Paid via", "select", PAY_MODES), ("branch_id", "Branch", "fk", branch_opts)],
        cols=["spent_on", "category", "description", "amount", "mode"], search=["description"], filter="category",
        order="spent_on"),
    "templates": dict(model=WhatsAppTemplate, perm="admin", title="WhatsApp Templates", fields=[
        ("title", "Title", "text", None),
        ("category", "Category", "select", ["Welcome", "Follow-up", "Documents", "Payment", "Visa & Travel", "General"]),
        ("body", "Message — use {name} {course} {country} {counsellor} {stage} {due} {fee_type} {due_date} {pay_link} {portal}", "textarea", None),
        ("meta_name", "Meta template name (needed outside the 24-hour window)", "text", None),
        ("language", "Language code (en, ml, hi…)", "text", None), ("active", "Active", "bool", None)],
        cols=["title", "category", "meta_name", "active"], search=["title", "body"], filter="category"),
    "branches": dict(model=Branch, perm="admin", title="Branches", fields=[
        ("name", "Name", "text", None), ("address", "Address", "text", None), ("phone", "Phone", "text", None),
        ("email", "Email", "text", None), ("active", "Active", "bool", None)],
        cols=["name", "address", "phone", "email", "active"], search=["name"]),
    "rules": dict(model=AutomationRule, perm="admin", title="Automation Rules", fields=[
        ("name", "Rule name", "text", None),
        ("trigger", "When", "select", ["lead_created", "stage_changed"]),
        ("condition", "Only if (field=value, e.g. stage=visa_approved / course=MBBS)", "text", None),
        ("action", "Then", "select", ["notify_user", "assign", "set_followup_days", "whatsapp_template"]),
        ("action_value", "Value (user ID / days / template name)", "text", None), ("active", "Active", "bool", None)],
        cols=["name", "trigger", "condition", "action", "action_value", "active"], search=["name"]),
}


def coerce(ftype, raw):
    if ftype == "bool":
        return bool(raw)
    if raw in (None, ""):
        return None
    if ftype == "number":
        return float(raw) if "." in raw else int(raw)
    if ftype == "fk":
        return int(raw)
    if ftype == "date":
        return date.fromisoformat(raw)
    if ftype == "datetime":
        return datetime.fromisoformat(raw)
    return raw.strip()


def spec(name):
    s = CRUD.get(name) or abort(404)
    if not current_user.can(s["perm"]):
        abort(403)
    return s


@bp.route("/m/<name>/")
@login_required
def crud_list(name):
    s = spec(name)
    M = s["model"]
    q = M.query
    a = request.args
    if a.get("q") and s.get("search"):
        from sqlalchemy import or_
        q = q.filter(or_(*[getattr(M, c).ilike(f"%{a['q']}%") for c in s["search"]]))
    if s.get("filter") and a.get(s["filter"]):
        q = q.filter(getattr(M, s["filter"]) == a[s["filter"]])
    order = getattr(M, s.get("order", "id"))
    rows = q.order_by(order.desc() if s.get("order") not in ("due_date", "next_due") else order.asc()).limit(500).all()
    filt_opts = next((f[3] for f in s["fields"] if f[0] == s.get("filter")), None)
    return render_template("crud_list.html", s=s, name=name, rows=rows, args=a, filt_opts=filt_opts,
                           labels={f[0]: f[1] for f in s["fields"]})


@bp.route("/m/<name>/new", methods=["GET", "POST"])
@bp.route("/m/<name>/<int:oid>", methods=["GET", "POST"])
@login_required
def crud_form(name, oid=None):
    s = spec(name)
    M = s["model"]
    obj = db.session.get(M, oid) if oid else M()
    if oid and not obj:
        abort(404)
    if request.method == "POST":
        if request.form.get("_delete") and oid:
            db.session.delete(obj)
            db.session.commit()
            flash("Deleted", "ok")
            return redirect(url_for("modules.crud_list", name=name))
        try:
            for fname, _, ftype, _ in s["fields"]:
                setattr(obj, fname, coerce(ftype, request.form.get(fname)))
        except ValueError as e:
            flash(f"Please check the values: {e}", "error")
            return render_template("crud_form.html", s=s, name=name, obj=obj, resolve=resolve)
        if hasattr(obj, "created_by_id") and not oid:
            obj.created_by_id = current_user.id
        if hasattr(obj, "recorded_by_id") and not oid:
            obj.recorded_by_id = current_user.id
        if not oid:
            db.session.add(obj)
        if name == "tasks" and obj.assigned_to_id:
            notify(obj.assigned_to_id, f"Office task: {obj.title}", url_for("modules.crud_list", name="tasks"))
        db.session.commit()
        flash("Saved", "ok")
        return redirect(url_for("modules.crud_list", name=name))
    for fname, _, ftype, _ in s["fields"]:  # sensible defaults
        if getattr(obj, fname, None) is None and request.args.get(fname):
            setattr(obj, fname, coerce(ftype, request.args[fname]))
    return render_template("crud_form.html", s=s, name=name, obj=obj, resolve=resolve)


def resolve(opts):
    return opts() if callable(opts) else [(o, o) for o in (opts or [])]


# ------------------------------------------------------------------ overviews
@bp.route("/frontdesk/")
@requires("frontdesk")
def frontdesk():
    t0 = datetime.combine(date.today(), datetime.min.time())
    return render_template("frontdesk.html",
                           visitors=Visitor.query.filter(Visitor.in_time >= t0).order_by(Visitor.in_time.desc()).all(),
                           couriers=Courier.query.filter(Courier.created_at >= t0).all(),
                           bills_pending=Bill.query.filter_by(status="pending").order_by(Bill.due_date).all(),
                           inventory=InventoryItem.query.filter_by(active=True).count())


@bp.route("/frontdesk/visitor/<int:vid>/out", methods=["POST"])
@requires("frontdesk")
def visitor_out(vid):
    v = db.session.get(Visitor, vid) or abort(404)
    v.out_time = now_ist()
    db.session.commit()
    return redirect(url_for("modules.frontdesk"))


@bp.route("/office/")
@requires("office")
def office():
    t0 = datetime.combine(date.today(), datetime.min.time())
    m0 = date.today().replace(day=1)
    return render_template("office.html",
                           appts=Appointment.query.filter(Appointment.starts_at >= t0,
                                                          Appointment.starts_at < t0 + timedelta(days=1)).all(),
                           open_tasks=OfficeTask.query.filter(OfficeTask.status.in_(["open", "in progress"]))
                           .order_by(OfficeTask.due_date).all(),
                           month_exp=db.session.query(func.coalesce(func.sum(Expense.amount), 0))
                           .filter(Expense.spent_on >= m0).scalar(),
                           due_records=OfficeRecord.query.filter(OfficeRecord.next_due <= date.today() + timedelta(days=30))
                           .order_by(OfficeRecord.next_due).all())


# ------------------------------------------------------------------ documents
@bp.route("/documents/")
@requires("documents")
def documents():
    q = Lead.query.filter(Lead.status == "open", Lead.stage.notin_(["new_lead", "attempted_contact"]))
    if request.args.get("q"):
        q = q.filter(Lead.name.ilike(f"%{request.args['q']}%") | Lead.phone.ilike(f"%{request.args['q']}%"))
    leads = q.order_by(Lead.updated_at.desc()).limit(200).all()
    stats = {l.id: (l.documents.filter(Document.status.in_(["uploaded", "verified"])).count(),
                    l.documents.filter_by(status="verified").count()) for l in leads}
    return render_template("documents.html", leads=leads, stats=stats, total=len(DOC_CHECKLIST))


@bp.route("/documents/<int:lid>", methods=["GET", "POST"])
@requires("documents")
def lead_documents(lid):
    lead = db.session.get(Lead, lid) or abort(404)
    if request.method == "POST":
        f = request.form
        name = f.get("doc_name")
        doc = lead.documents.filter_by(name=name).first() or Document(lead_id=lid, name=name)
        up = request.files.get("file")
        if up and up.filename:
            fn = secure_filename(f"{lid}_{now_ist():%Y%m%d%H%M%S}_{up.filename}")
            save_upload(up, fn)
            doc.filename, doc.status = fn, "uploaded"
        if f.get("status"):
            doc.status = f["status"]
        if f.get("note") is not None:
            doc.note = f.get("note")
        if f.get("expiry"):
            doc.expiry = date.fromisoformat(f["expiry"])
        db.session.add(doc)
        log(lead, f"Document '{name}' → {doc.status}", "system", current_user)
        db.session.commit()
        return redirect(url_for("modules.lead_documents", lid=lid))
    docs = {d.name: d for d in lead.documents}
    extra = [d for n, d in docs.items() if n not in DOC_CHECKLIST]
    return render_template("lead_documents.html", lead=lead, docs=docs, checklist=DOC_CHECKLIST, extra=extra)


@bp.route("/files/<path:fn>")
@login_required
def file(fn):
    return serve_upload(fn)


# ------------------------------------------------------------------ brochures
@bp.route("/brochures", methods=["GET", "POST"])
@requires("universities")
def brochures():
    if request.method == "POST":
        if request.form.get("delete"):
            b = db.session.get(Brochure, int(request.form["delete"]))
            if b:
                db.session.delete(b)
        else:
            up = request.files.get("file")
            if not up or not up.filename:
                flash("Choose a file", "error")
                return redirect(url_for("modules.brochures"))
            fn = secure_filename(f"brochure_{now_ist():%Y%m%d%H%M%S}_{up.filename}")
            save_upload(up, fn)
            db.session.add(Brochure(course=request.form.get("course") or None, title=request.form["title"], filename=fn,
                                    country=request.form.get("country") or None,
                                    university_id=request.form.get("university_id", type=int) or None))
        db.session.commit()
        return redirect(url_for("modules.brochures"))
    return render_template("brochures.html", brochures=Brochure.query.order_by(Brochure.course, Brochure.country).all(),
                           courses=COURSES, countries=COUNTRIES,
                           universities=University.query.filter_by(active=True).order_by(University.country, University.name).all())


@bp.route("/b/<int:bid>")
def brochure_public(bid):
    """Trackable public link sent to leads."""
    b = db.session.get(Brochure, bid) or abort(404)
    b.views += 1
    lid = request.args.get("l", type=int)
    if lid and (lead := db.session.get(Lead, lid)):
        log(lead, f"Opened brochure '{b.title}'", "system")
        notify(lead.assigned_to_id, f"{lead.name} opened brochure {b.title}", f"/leads/{lid}")
    db.session.commit()
    return serve_upload(b.filename)


# ------------------------------------------------------------------ admin
@bp.route("/admin/")
@requires("admin")
def admin():
    return render_template("admin.html", users=User.query.count(), branches=Branch.query.count(),
                           rules=AutomationRule.query.count(), integrations=Integration.query.all(),
                           unassigned=Lead.query.filter_by(assigned_to_id=None, status="open").count())


@bp.route("/admin/integrations", methods=["GET", "POST"])
@requires("admin")
def integrations():
    if request.method == "POST":
        f = request.form
        if f.get("revoke"):
            i = db.session.get(Integration, int(f["revoke"]))
            i.active = not i.active
        elif f.get("regen"):
            i = db.session.get(Integration, int(f["regen"]))
            i.api_key = "aec_live_" + secrets.token_urlsafe(24)
        elif f.get("delete"):
            db.session.delete(db.session.get(Integration, int(f["delete"])))
        else:
            db.session.add(Integration(name=f["name"], kind=f.get("kind"), api_key="aec_live_" + secrets.token_urlsafe(24),
                                       branch_id=int(f["branch_id"]) if f.get("branch_id") else None))
        db.session.commit()
        return redirect(url_for("modules.integrations"))
    return render_template("integrations.html", items=Integration.query.all(), branches=Branch.query.all())


@bp.route("/notifications/", methods=["GET", "POST"])
@login_required
def notifications():
    q = Notification.query.filter_by(user_id=current_user.id)
    if request.method == "POST":
        q.update({"read": True})
        db.session.commit()
        return redirect(url_for("modules.notifications"))
    return render_template("notifications.html", items=q.order_by(Notification.created_at.desc()).limit(200).all())


@bp.route("/admin/import", methods=["GET", "POST"])
@requires("admin")
def import_data():
    """Import CSV exported from the old CRM (leads / universities / payments). Duplicates are merged."""
    import csv
    import io
    import sys
    import contextlib
    sys.path.insert(0, os.path.dirname(current_app.root_path))
    import migrate
    out = None
    if request.method == "POST":
        kind = request.form.get("kind", "leads")
        up = request.files.get("file")
        text = up.read().decode("utf-8-sig") if up and up.filename else request.form.get("csv", "")
        rows = list(csv.DictReader(io.StringIO(text)))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            {"leads": migrate.import_leads, "universities": migrate.import_universities,
             "payments": migrate.import_payments, "whatsapp": migrate.import_whatsapp}[kind](rows, bool(request.form.get("dry")))
        out = buf.getvalue() or "Done"
        if request.form.get("api"):
            return out
    return render_template("import.html", out=out)


@bp.route("/privacy")
def privacy():
    """Public privacy policy (needed by Meta to publish the WhatsApp app)."""
    return render_template("privacy.html")

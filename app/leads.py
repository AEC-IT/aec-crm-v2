import csv
import io
from datetime import datetime, timedelta, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, Response
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from .auth import requires
from .models import now_ist
from .models import (db, Lead, User, Branch, University, Application, Activity, Brochure, Appointment,
                     Payment, Charge, WhatsAppTemplate, DOC_CHECKLIST, Document, PHASES, STAGES, STAGE_LABEL, COURSES, COUNTRIES,
                     SOURCES, CONVERTED_STAGES)
from .models import now_ist
from .services import (save_upload, serve_upload, normalize_phone, find_duplicates, log, auto_assign, merge_leads, change_stage,
                       run_rules, new_token)

bp = Blueprint("leads", __name__)

INT_FIELDS = {"tenth_year", "twelfth_year", "neet_score", "neet_year", "graduation_year", "referred_by_id"}
FLOAT_FIELDS = {"budget", "tenth_percent", "twelfth_percent", "pcb_percent", "graduation_cgpa"}
DATE_FIELDS = {"dob", "passport_expiry", "visa_applied_on", "visa_expiry", "flight_date"}
BOOL_FIELDS = {"budget_confirmed", "documents_ready"}
TEXT_FIELDS = ["name", "email", "course", "country_preference", "source", "campaign", "ad_name", "priority",
               "preferred_language", "gender", "location", "district", "state", "parent_name", "parent_occupation",
               "self_occupation", "twelfth_group", "degree_name", "english_test", "intended_intake",
               "funding_source", "passport_status", "passport_number", "decision_maker", "competitor",
               "objection", "visa_status", "flight_details"]


def visible_leads():
    q = Lead.query
    if not current_user.sees_all_leads:
        q = q.filter(Lead.assigned_to_id == current_user.id)
    elif current_user.role == "branch_manager" and current_user.branch_id:
        q = q.filter(Lead.branch_id == current_user.branch_id)
    return q


def get_lead(lid):
    lead = visible_leads().filter(Lead.id == lid).first()
    if not lead:
        abort(404)
    return lead


def fill_lead(lead, f):
    for k in TEXT_FIELDS:
        if k in f:
            setattr(lead, k, f.get(k).strip() or None)
    for k in INT_FIELDS:
        if k in f:
            setattr(lead, k, int(f[k]) if f[k].strip() else None)
    for k in FLOAT_FIELDS:
        if k in f:
            setattr(lead, k, float(f[k]) if f[k].strip() else None)
    for k in DATE_FIELDS:
        if k in f:
            setattr(lead, k, date.fromisoformat(f[k]) if f[k] else None)
    for k in BOOL_FIELDS:
        setattr(lead, k, bool(f.get(k)))
    for k in ("phone", "whatsapp", "parent_phone"):
        if k in f:
            setattr(lead, k, normalize_phone(f[k]) or None)
    if not lead.whatsapp:
        lead.whatsapp = lead.phone
    if "branch_id" in f:
        lead.branch_id = int(f["branch_id"]) if f["branch_id"] else None
    if "assigned_to_id" in f and current_user.sees_all_leads:
        lead.assigned_to_id = int(f["assigned_to_id"]) if f["assigned_to_id"] else None
    lead.compute_score()


def form_ctx():
    return dict(courses=COURSES, countries=COUNTRIES, sources=SOURCES, stages=STAGES,
                users=User.query.filter_by(active=True).order_by(User.name).all(),
                branches=Branch.query.filter_by(active=True).all())


# ------------------------------------------------------------------ dashboard
@bp.route("/")
@bp.route("/leads/dashboard")
@login_required
def dashboard():
    q = visible_leads()
    total = q.count()
    open_q = q.filter(Lead.status == "open")
    active = open_q.filter(~Lead.stage.in_(CONVERTED_STAGES)).count()
    converted = q.filter(Lead.stage.in_(CONVERTED_STAGES)).count()
    revenue = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.cancelled.isnot(True)).scalar()
    today0 = datetime.combine(date.today(), datetime.min.time())
    overdue = open_q.filter(Lead.next_followup_at < now_ist()).order_by(Lead.next_followup_at).limit(10).all()
    due_today = open_q.filter(Lead.next_followup_at >= today0,
                              Lead.next_followup_at < today0 + timedelta(days=1)).count()
    appts = Appointment.query.filter(Appointment.starts_at >= today0,
                                     Appointment.starts_at < today0 + timedelta(days=1)).order_by(Appointment.starts_at).all()
    counts = dict(db.session.query(Lead.stage, func.count()).filter(Lead.status == "open")
                  .filter(Lead.id.in_(q.with_entities(Lead.id))).group_by(Lead.stage).all())
    performers = (db.session.query(User, func.count(Lead.id),
                                   func.sum(db.case((Lead.stage.in_(CONVERTED_STAGES), 1), else_=0)))
                  .join(Lead, Lead.assigned_to_id == User.id).group_by(User.id)
                  .order_by(func.count(Lead.id).desc()).limit(6).all())
    week_new = q.filter(Lead.created_at >= now_ist() - timedelta(days=7)).count()
    unassigned = q.filter(Lead.assigned_to_id.is_(None), Lead.status == "open").count()
    hot = open_q.filter(Lead.temperature == "hot").order_by(Lead.score.desc()).limit(5).all()
    return render_template("dashboard.html", total=total, active=active, converted=converted, revenue=revenue,
                           overdue=overdue, due_today=due_today, appts=appts, counts=counts, phases=PHASES,
                           performers=performers, week_new=week_new, unassigned=unassigned, hot=hot)


# ------------------------------------------------------------------ list
@bp.route("/leads/")
@bp.route("/leads/closed", endpoint="closed", defaults={"status": "lost"})
@bp.route("/leads/archived", endpoint="archived", defaults={"status": "archived"})
@login_required
def index(status="open"):
    if not current_user.can("leads"):
        abort(403)
    a = request.args
    q = visible_leads().filter(Lead.status == status)
    if a.get("q"):
        s = f"%{a['q'].strip()}%"
        q = q.filter(or_(Lead.name.ilike(s), Lead.phone.ilike(s), Lead.email.ilike(s), Lead.whatsapp.ilike(s),
                         Lead.parent_phone.ilike(s), Lead.location.ilike(s)))
    for f in ("stage", "course", "country_preference", "source", "temperature", "branch_id"):
        if a.get(f):
            q = q.filter(getattr(Lead, f) == a[f])
    if a.get("assigned_to_id") == "none":
        q = q.filter(Lead.assigned_to_id.is_(None))
    elif a.get("assigned_to_id"):
        q = q.filter(Lead.assigned_to_id == int(a["assigned_to_id"]))
    if a.get("followup") == "overdue":
        q = q.filter(Lead.next_followup_at < now_ist())
    elif a.get("followup") == "today":
        t0 = datetime.combine(date.today(), datetime.min.time())
        q = q.filter(Lead.next_followup_at >= t0, Lead.next_followup_at < t0 + timedelta(days=1))
    sort = {"newest": Lead.created_at.desc(), "oldest": Lead.created_at.asc(), "score": Lead.score.desc(),
            "followup": Lead.next_followup_at.asc(), "name": Lead.name.asc()}.get(a.get("sort"), Lead.created_at.desc())
    if a.get("export") == "csv":
        return export_csv(q.order_by(sort).all())
    page = q.order_by(sort).paginate(page=int(a.get("page", 1)), per_page=50, error_out=False)
    return render_template("leads_list.html", page=page, status=status, args=a, **form_ctx())


def export_csv(leads):
    cols = ["id", "name", "phone", "whatsapp", "email", "course", "country_preference", "stage", "status", "source",
            "campaign", "temperature", "score", "neet_score", "budget", "location", "created_at"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols + ["assigned_to"])
    for l in leads:
        w.writerow([getattr(l, c) for c in cols] + [l.assigned_to.name if l.assigned_to else ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


@bp.route("/leads/bulk", methods=["POST"])
@requires("leads")
def bulk():
    ids = [int(i) for i in request.form.getlist("ids")]
    action = request.form.get("action")
    leads = visible_leads().filter(Lead.id.in_(ids)).all()
    for l in leads:
        if action == "assign" and request.form.get("assigned_to_id"):
            l.assigned_to_id = int(request.form["assigned_to_id"])
            log(l, f"Bulk assigned to {l.assigned_to_id}", "assign", current_user)
        elif action == "stage" and request.form.get("stage"):
            change_stage(l, request.form["stage"], current_user)
        elif action == "archive":
            l.status = "archived"
        elif action == "auto_assign":
            auto_assign(l)
    db.session.commit()
    flash(f"Updated {len(leads)} leads", "ok")
    return redirect(request.referrer or url_for("leads.index"))


# ------------------------------------------------------------------ create / edit
@bp.route("/leads/new", methods=["GET", "POST"])
@bp.route("/leads/<int:lid>/edit", methods=["GET", "POST"])
@requires("leads")
def form(lid=None):
    lead = get_lead(lid) if lid else Lead(branch_id=current_user.branch_id)
    dups = []
    if request.method == "POST":
        f = request.form
        dups = find_duplicates(f.get("phone"), f.get("email"), exclude_id=lid)
        if dups and not f.get("confirm_duplicate"):
            flash("A lead with this phone or email already exists. Open it, merge, or tick "
                  "'Create anyway'.", "error")
            fill_lead(lead, f)  # keep typed values on the form; nothing is committed
            return render_template("lead_form.html", lead=lead, dups=dups, **form_ctx())
        fill_lead(lead, f)
        if not lid:
            lead.portal_token = new_token()
            if current_user.role in ("counselor", "telecaller") and not lead.assigned_to_id:
                lead.assigned_to_id = current_user.id
            db.session.add(lead)
            db.session.flush()
            if not lead.assigned_to_id:
                auto_assign(lead)
            log(lead, f"Lead created ({lead.source})", "system", current_user)
            run_rules("lead_created", lead)
        else:
            log(lead, "Lead details updated", "system", current_user)
        db.session.commit()
        flash("Lead saved", "ok")
        return redirect(url_for("leads.detail", lid=lead.id))
    return render_template("lead_form.html", lead=lead, dups=dups, **form_ctx())


@bp.route("/leads/check-duplicate")
@login_required
def check_duplicate():
    d = find_duplicates(request.args.get("phone"), request.args.get("email"),
                        exclude_id=request.args.get("exclude", type=int))
    return jsonify([{"id": l.id, "name": l.name, "phone": l.phone, "stage": l.stage_label} for l in d])


# ------------------------------------------------------------------ detail
@bp.route("/leads/<int:lid>")
@requires("leads")
def detail(lid):
    lead = get_lead(lid)
    scored = sorted(((b.match_score(lead), b) for b in Brochure.query.all()), key=lambda x: (-x[0], x[1].title or ""))
    recommended = [b for sc, b in scored if sc > 0]
    other_brochures = [b for sc, b in scored if sc <= 0]
    charges = lead.charges.all()
    billed = sum(c.amount for c in charges)
    collected = sum(c.paid for c in charges)
    final_app = next((a for a in lead.applications if a.status == "Final"), None)
    from .services import fill_template
    stage_msgs = {t.stage: fill_template(t.body, lead, extra={"stage": STAGE_LABEL.get(t.stage, t.stage)})[0]
                  for t in WhatsAppTemplate.query.filter(WhatsAppTemplate.stage.isnot(None), WhatsAppTemplate.active.is_(True)).all()}
    return render_template("lead_detail.html", lead=lead, stages=STAGES, phases=PHASES,
                           universities=University.query.filter_by(active=True).order_by(University.country, University.name).all(),
                           brochures=recommended + other_brochures, recommended=recommended, other_brochures=other_brochures,
                           billed=billed, collected=collected, unpaid=sum(1 for c in charges if c.status != "paid"),
                           final_app=final_app, stage_msgs=stage_msgs, notes=lead.activities.filter_by(kind="note").all(),
                           dups=find_duplicates(lead.phone, lead.email, lead.id),
                           users=User.query.filter_by(active=True).all(),
                           activities=lead.activities.limit(100).all(), lost_reasons=LOST_REASONS,
                           wa_templates=WhatsAppTemplate.query.filter_by(active=True).order_by(WhatsAppTemplate.category).all())


@bp.route("/leads/<int:lid>/stage", methods=["POST"])
@requires("leads")
def set_stage(lid):
    lead = get_lead(lid)
    st = request.form.get("stage") or (request.json or {}).get("stage")
    if st == "lost":
        lead.status = "lost"
        lead.lost_reason = request.form.get("lost_reason")
        log(lead, f"Marked lost: {lead.lost_reason or '-'}", "stage", current_user)
    elif st in STAGE_LABEL:
        lead.status = "open"
        change_stage(lead, st, current_user)
    db.session.commit()
    if request.is_json:
        return jsonify(ok=True)
    body = (request.form.get("wa_body") or "").strip()
    if request.form.get("send_whatsapp") and body:
        from .comms import send_whatsapp, in_service_window, _post
        from .models import WhatsAppMessage
        import re
        phone = normalize_phone(lead.whatsapp or lead.phone)
        tpl = WhatsAppTemplate.query.filter_by(stage=st, active=True).first()
        if tpl and tpl.meta_name and not in_service_window(phone):
            # outside 24 h: Meta only accepts the approved template, variables in body order
            from .services import fill_template
            _, vals = fill_template(tpl.body, lead)
            params = [{"type": "text", "text": vals.get(k, "") or "-"} for k in re.findall(r"{(\w+)}", tpl.body)]
            ok, info = _post({"to": phone.lstrip("+"), "type": "template", "template": {"name": tpl.meta_name,
                              "language": {"code": tpl.language or "en"}, "components": [{"type": "body", "parameters": params}] if params else []}})
        else:
            ok, info = send_whatsapp(phone, body)
        db.session.add(WhatsAppMessage(lead_id=lead.id, phone=phone, direction="out", body=body, read=True))
        log(lead, body + ("" if ok else f"\n⚠ {info}"), "whatsapp", current_user)
        lead.last_contacted_at = now_ist()
        db.session.commit()
        flash(f"Stage updated to {lead.stage_label} and WhatsApp sent ✓" if ok else
              f"Stage updated to {lead.stage_label}. WhatsApp not sent: {info} Use 'Open in WhatsApp' to send it from your phone.",
              "ok" if ok else "error")
    else:
        flash(f"Stage updated to {lead.stage_label}", "ok")
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/leads/<int:lid>/activity", methods=["POST"])
@requires("leads")
def add_activity(lid):
    lead = get_lead(lid)
    f = request.form
    kind = f.get("kind", "note")
    text = f.get("text", "").strip()
    if text or f.get("outcome"):
        log(lead, text, kind, current_user, f.get("outcome") or None)
        if kind in ("call", "whatsapp", "meeting"):
            lead.last_contacted_at = now_ist()
            if lead.stage == "new_lead":
                change_stage(lead, "connected" if f.get("outcome") == "Connected" else "attempted_contact", current_user)
    if f.get("next_followup_at"):
        lead.next_followup_at = datetime.fromisoformat(f["next_followup_at"])
        lead.next_followup_note = f.get("next_followup_note") or text[:250]
    elif f.get("clear_followup"):
        lead.next_followup_at = None
    db.session.commit()
    return redirect(url_for("leads.detail", lid=lid) + "#timeline")


@bp.route("/leads/<int:lid>/assign", methods=["POST"])
@requires("leads")
def assign(lid):
    lead = get_lead(lid)
    if request.form.get("auto"):
        u = auto_assign(lead)
        log(lead, f"Auto-assigned to {u.name if u else 'nobody (no capacity)'}", "assign", current_user)
    else:
        lead.assigned_to_id = int(request.form["assigned_to_id"]) if request.form.get("assigned_to_id") else None
        log(lead, f"Assigned to {lead.assigned_to.name if lead.assigned_to else 'nobody'}", "assign", current_user)
    db.session.commit()
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/leads/<int:lid>/merge/<int:other_id>", methods=["POST"])
@requires("leads")
def merge(lid, other_id):
    keep, other = get_lead(lid), get_lead(other_id)
    merge_leads(keep, other, current_user)
    db.session.commit()
    flash(f"Merged #{other_id} into this lead", "ok")
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/leads/<int:lid>/application", methods=["POST"])
@requires("leads")
def add_application(lid):
    lead = get_lead(lid)
    f = request.form
    if f.get("app_id"):
        app_ = Application.query.filter_by(id=int(f["app_id"]), lead_id=lid).first_or_404()
        if f.get("delete"):
            db.session.delete(app_)
        else:
            app_.status = f.get("status", app_.status)
            if app_.status == "Final":
                for other in lead.applications:
                    if other.id != app_.id and other.status == "Final":
                        other.status = "Accepted"
            log(lead, f"Application {app_.university.name}: {app_.status}", "system", current_user)
    elif f.get("university_id"):
        app_ = Application(lead_id=lid, university_id=int(f["university_id"]), course=f.get("course") or lead.course,
                           intake=f.get("intake") or lead.intended_intake, status=f.get("status") or "Shortlisted")
        db.session.add(app_)
        db.session.flush()
        log(lead, f"University shortlisted: {app_.university.name}", "system", current_user)
        if [c for c, _ in STAGES].index(lead.stage) < [c for c, _ in STAGES].index("university_shortlisted"):
            change_stage(lead, "university_shortlisted", current_user)
    db.session.commit()
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/leads/<int:lid>/status/<status>", methods=["POST"])
@requires("leads")
def set_status(lid, status):
    lead = get_lead(lid)
    if status in ("open", "archived", "lost"):
        lead.status = status
        log(lead, f"Status set to {status}", "system", current_user)
        db.session.commit()
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/leads/<int:lid>/delete", methods=["POST"])
@requires("admin")
def delete(lid):
    lead = get_lead(lid)
    db.session.delete(lead)
    db.session.commit()
    flash("Lead deleted", "ok")
    return redirect(url_for("leads.index"))


# ------------------------------------------------------------------ kanban / call list
@bp.route("/leads/kanban")
@requires("leads")
def kanban():
    phase = request.args.get("phase", "Inquiry")
    stages = dict(PHASES).get(phase, PHASES[0][1])
    cols = {code: visible_leads().filter(Lead.status == "open", Lead.stage == code)
            .order_by(Lead.score.desc()).limit(60).all() for code, _ in stages}
    return render_template("kanban.html", cols=cols, stages=stages, phases=PHASES, phase=phase)


@bp.route("/leads/call-list")
@requires("leads")
def call_list():
    """Today's work queue: overdue + due today + never-contacted new leads."""
    t1 = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    q = visible_leads().filter(Lead.status == "open")
    if current_user.sees_all_leads and request.args.get("mine"):
        q = q.filter(Lead.assigned_to_id == current_user.id)
    due = q.filter(Lead.next_followup_at < t1).order_by(Lead.next_followup_at).all()
    fresh = q.filter(Lead.stage == "new_lead", Lead.last_contacted_at.is_(None),
                     Lead.next_followup_at.is_(None)).order_by(Lead.created_at).limit(50).all()
    return render_template("call_list.html", due=due, fresh=fresh)


# ------------------------------------------------------------------ student / parent portal (no login)
@bp.route("/portal/<token>", methods=["GET", "POST"])
def portal(token):
    import os
    from werkzeug.utils import secure_filename
    from flask import current_app
    lead = Lead.query.filter_by(portal_token=token).first_or_404()
    if request.method == "POST" and request.files.get("file"):
        f = request.files["file"]
        name = request.form.get("doc_name") or "Other"
        fn = secure_filename(f"{lead.id}_{now_ist():%Y%m%d%H%M%S}_{f.filename}")
        save_upload(f, fn)
        doc = lead.documents.filter_by(name=name).first() or Document(lead_id=lead.id, name=name)
        doc.filename, doc.status, doc.uploaded_by_portal = fn, "uploaded", True
        db.session.add(doc)
        log(lead, f"Student uploaded '{name}' via portal", "system")
        from .services import notify
        notify(lead.assigned_to_id, f"{lead.name} uploaded {name}", f"/documents/{lead.id}")
        db.session.commit()
        flash("Thank you — document received.", "ok")
        return redirect(request.url)
    docs = {d.name: d for d in lead.documents}
    return render_template("portal.html", lead=lead, phases=PHASES, checklist=DOC_CHECKLIST, docs=docs,
                           stage_codes=[c for c, _ in STAGES])


LOST_REASONS = ["Not interested", "Joined another consultancy", "Studying in India", "Budget too low", "Not eligible",
                "Wrong number / fake enquiry", "No response after many attempts", "Postponed to next year", "Other"]


@bp.route("/leads/<int:lid>/close", methods=["POST"])
@requires("leads")
def close(lid):
    """Close a lead with a reason (moves it to Closed & Lost; can be reopened)."""
    lead = get_lead(lid)
    reason = request.form.get("reason") or "Other"
    note = (request.form.get("note") or "").strip()
    lead.status, lead.lost_reason = "lost", reason
    lead.next_followup_at = None
    log(lead, f"Lead closed: {reason}" + (f" — {note}" if note else ""), "stage", current_user)
    db.session.commit()
    flash(f"{lead.name} moved to Closed & Lost ({reason})", "ok")
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/leads/<int:lid>/notify", methods=["POST"])
@requires("leads")
def notify_user(lid):
    """Send an in-CRM notification about this lead to a colleague."""
    from .services import notify
    lead = get_lead(lid)
    uid = request.form.get("user_id", type=int)
    msg = (request.form.get("message") or "").strip()
    target = db.session.get(User, uid) if uid else None
    if not target:
        flash("Choose who to notify", "error")
        return redirect(url_for("leads.detail", lid=lid))
    notify(uid, f"{current_user.name} about {lead.name}: {msg or 'please check this lead'}", f"/leads/{lid}")
    log(lead, f"Notified {target.name}: {msg}", "note", current_user)
    db.session.commit()
    flash(f"{target.name} has been notified", "ok")
    return redirect(url_for("leads.detail", lid=lid))

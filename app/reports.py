import csv
import io
from datetime import datetime, timedelta, date
from flask import Blueprint, render_template, request, Response
from sqlalchemy import func
from .auth import requires
from .models import now_ist
from .models import db, Lead, User, Payment, Activity, PHASES, STAGES, STAGE_LABEL, CONVERTED_STAGES

bp = Blueprint("reports", __name__, url_prefix="/reports")


def date_range():
    days = request.args.get("days", type=int) or 90
    return now_ist() - timedelta(days=days), days


@bp.route("/")
@requires("reports")
def overview():
    since, days = date_range()
    q = Lead.query.filter(Lead.created_at >= since)
    total = q.count()
    conv = q.filter(Lead.stage.in_(CONVERTED_STAGES)).count()
    lost = q.filter(Lead.status == "lost").count()
    by_source = q.with_entities(Lead.source, func.count()).group_by(Lead.source).order_by(func.count().desc()).all()
    by_course = q.with_entities(Lead.course, func.count()).group_by(Lead.course).order_by(func.count().desc()).all()
    by_country = q.with_entities(Lead.country_preference, func.count()).group_by(Lead.country_preference)\
        .order_by(func.count().desc()).all()
    return render_template("reports_overview.html", total=total, conv=conv, lost=lost, days=days,
                           by_source=by_source, by_course=by_course, by_country=by_country)


@bp.route("/conversion-funnel")
@requires("reports")
def funnel():
    since, days = date_range()
    codes = [c for c, _ in STAGES]
    rows = Lead.query.filter(Lead.created_at >= since, Lead.status != "archived").all()
    reached = {c: 0 for c in codes}
    for l in rows:  # a lead at stage N has passed every earlier stage
        idx = codes.index(l.stage) if l.stage in codes else 0
        for c in codes[:min(idx + 1, codes.index("alumni") + 1)]:
            reached[c] += 1
    funnel = [(c, STAGE_LABEL[c], reached[c]) for c in codes if c not in ("dropped_out", "refund_requested", "refund_processed")]
    return render_template("report_funnel.html", funnel=funnel, total=len(rows), days=days)


@bp.route("/counselor-performance")
@requires("reports")
def counselors():
    since, days = date_range()
    data = []
    for u in User.query.filter(User.role.in_(["counselor", "telecaller", "branch_manager", "super_admin"])).all():
        leads = Lead.query.filter(Lead.assigned_to_id == u.id, Lead.created_at >= since)
        n = leads.count()
        calls = Activity.query.filter(Activity.user_id == u.id, Activity.kind == "call", Activity.created_at >= since).count()
        conv = leads.filter(Lead.stage.in_(CONVERTED_STAGES)).count()
        overdue = Lead.query.filter(Lead.assigned_to_id == u.id, Lead.status == "open",
                                    Lead.next_followup_at < now_ist()).count()
        untouched = leads.filter(Lead.last_contacted_at.is_(None), Lead.status == "open").count()
        data.append((u, n, calls, conv, overdue, untouched))
    data.sort(key=lambda r: -r[1])
    return render_template("report_counselors.html", data=data, days=days)


@bp.route("/lead-source-analysis")
@requires("reports")
def sources():
    since, days = date_range()
    rows = db.session.query(Lead.source, Lead.campaign, func.count(),
                            func.sum(db.case((Lead.stage.in_(CONVERTED_STAGES), 1), else_=0)),
                            func.sum(db.case((Lead.temperature == "hot", 1), else_=0)))\
        .filter(Lead.created_at >= since).group_by(Lead.source, Lead.campaign).order_by(func.count().desc()).all()
    return render_template("report_sources.html", rows=rows, days=days)


@bp.route("/monthly-trends")
@requires("reports")
def monthly():
    leads = Lead.query.with_entities(Lead.created_at, Lead.stage).all()
    pays = Payment.query.with_entities(Payment.paid_on, Payment.amount).all()
    months = {}
    for created, stage in leads:
        k = created.strftime("%Y-%m")
        m = months.setdefault(k, [0, 0, 0])
        m[0] += 1
        if stage in CONVERTED_STAGES:
            m[1] += 1
    for d, amt in pays:
        if d:
            months.setdefault(d.strftime("%Y-%m"), [0, 0, 0])[2] += amt or 0
    return render_template("report_monthly.html", months=sorted(months.items())[-18:])


@bp.route("/pipeline-health")
@requires("reports")
def pipeline_health():
    """Leads stuck: no activity for 14+ days in an open stage."""
    cutoff = now_ist() - timedelta(days=14)
    stuck = Lead.query.filter(Lead.status == "open", ~Lead.stage.in_(CONVERTED_STAGES),
                              Lead.updated_at < cutoff).order_by(Lead.updated_at).limit(300).all()
    no_followup = Lead.query.filter(Lead.status == "open", Lead.next_followup_at.is_(None),
                                    ~Lead.stage.in_(CONVERTED_STAGES)).count()
    return render_template("report_pipeline.html", stuck=stuck, no_followup=no_followup)


@bp.route("/followup-report")
@requires("reports")
def followups():
    now = now_ist()
    rows = db.session.query(User, func.count(Lead.id)).join(Lead, Lead.assigned_to_id == User.id)\
        .filter(Lead.status == "open", Lead.next_followup_at < now).group_by(User.id).all()
    overdue = Lead.query.filter(Lead.status == "open", Lead.next_followup_at < now).order_by(Lead.next_followup_at).limit(300).all()
    return render_template("report_followups.html", rows=rows, overdue=overdue)


@bp.route("/enrollment-projection")
@requires("reports")
def projection():
    """Weighted pipeline: probability by phase × open leads."""
    weights = {"Inquiry": 0.03, "Counseling": 0.15, "Application": 0.35, "Pre-Departure": 0.75}
    out = []
    for phase, group in PHASES:
        if phase not in weights:
            continue
        n = Lead.query.filter(Lead.status == "open", Lead.stage.in_([c for c, _ in group])).count()
        out.append((phase, n, weights[phase], round(n * weights[phase], 1)))
    return render_template("report_projection.html", rows=out, total=sum(r[3] for r in out))


@bp.route("/export/<kind>")
@requires("reports")
def export(kind):
    q = Lead.query if kind == "all" else Lead.query.filter(Lead.status == "open")
    cols = [c.name for c in Lead.__table__.columns if c.name != "portal_token"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for l in q.all():
        w.writerow([getattr(l, c) for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=aec_{kind}_{date.today()}.csv"})

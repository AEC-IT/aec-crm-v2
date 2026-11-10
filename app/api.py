"""Public lead-capture API for landing pages, AI bots, Meta Lead Ads (via Zapier/Make) and Tally."""
from datetime import datetime
from flask import Blueprint, request, jsonify
from .models import now_ist
from .models import db, Lead, Integration
from .services import normalize_phone, find_duplicates, log, auto_assign, run_rules, new_token

bp = Blueprint("api", __name__, url_prefix="/api/v1")

FIELD_ALIASES = {
    "name": ["name", "full_name", "student_name", "Name", "Full name"],
    "phone": ["phone", "phone_number", "mobile", "Phone", "Phone number", "whatsapp"],
    "email": ["email", "Email"],
    "course": ["course", "Course"],
    "country_preference": ["country", "country_preference", "Country"],
    "location": ["location", "city", "City", "District"],
    "neet_score": ["neet_score", "NEET Score", "neet"],
    "campaign": ["campaign", "campaign_name", "utm_campaign"],
    "ad_name": ["ad_name", "utm_content"],
    "source": ["source", "utm_source"],
}


def pick(data, key):
    for k in FIELD_ALIASES[key]:
        if data.get(k) not in (None, ""):
            return data[k]
    return None


def flatten_tally(data):
    """Tally webhook: {data: {fields: [{label, value}]}}"""
    out = {}
    for f in (data.get("data") or {}).get("fields", []):
        out[f.get("label")] = f.get("value")
    return out


@bp.route("/leads", methods=["POST"])
def create_lead():
    key = request.headers.get("X-API-Key") or request.args.get("key")
    integ = Integration.query.filter_by(api_key=key, active=True).first() if key else None
    if not integ:
        return jsonify(error="invalid or missing API key"), 401
    data = request.get_json(silent=True) or request.form.to_dict()
    if "data" in data and isinstance(data["data"], dict):
        data = {**data, **flatten_tally(data)}
    name, phone = pick(data, "name"), normalize_phone(pick(data, "phone"))
    if not (name and phone):
        return jsonify(error="name and phone are required"), 400
    integ.leads_received += 1
    integ.last_used = now_ist()
    dups = find_duplicates(phone, pick(data, "email"))
    if dups:  # never create a duplicate; log the re-enquiry instead
        lead = dups[0]
        log(lead, f"Re-enquiry via {integ.name}: {data.get('message') or ''}", "system")
        if lead.status != "open":
            lead.status = "open"
        db.session.commit()
        return jsonify(ok=True, lead_id=lead.id, duplicate=True)
    lead = Lead(name=name, phone=phone, whatsapp=phone, email=pick(data, "email"), course=pick(data, "course"),
                country_preference=pick(data, "country_preference"), location=pick(data, "location"),
                campaign=pick(data, "campaign"), ad_name=pick(data, "ad_name"),
                source=pick(data, "source") or integ.kind or "API", branch_id=integ.branch_id, portal_token=new_token())
    try:
        lead.neet_score = int(pick(data, "neet_score")) if pick(data, "neet_score") else None
    except ValueError:
        pass
    lead.compute_score()
    db.session.add(lead)
    db.session.flush()
    auto_assign(lead)
    log(lead, f"Lead received from {integ.name}", "system")
    run_rules("lead_created", lead)
    db.session.commit()
    return jsonify(ok=True, lead_id=lead.id, duplicate=False), 201


@bp.route("/import", methods=["POST", "OPTIONS"])
def bulk_import():
    """Bulk CSV import (leads / universities / payments) authorised by an API key.
    Accepts a simple form POST so a browser page on another site can send it."""
    import csv, io, contextlib
    from flask import make_response
    if request.method == "OPTIONS":
        resp = make_response("")
    else:
        key = request.args.get("key") or request.form.get("key")
        integ = Integration.query.filter_by(api_key=key, active=True).first() if key else None
        if not integ:
            resp = make_response("invalid or missing API key", 401)
        else:
            import migrate
            rows = list(csv.DictReader(io.StringIO(request.form.get("csv", ""))))
            kind = request.form.get("kind", "leads")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                {"leads": migrate.import_leads, "universities": migrate.import_universities,
                 "payments": migrate.import_payments, "whatsapp": migrate.import_whatsapp}[kind](rows, bool(request.form.get("dry")))
            integ.last_used = now_ist()
            db.session.commit()
            resp = make_response(buf.getvalue() or "done")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

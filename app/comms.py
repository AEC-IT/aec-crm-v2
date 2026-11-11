"""WhatsApp inbox via Meta Cloud API (webhook in, send out)."""
import json
import urllib.request
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from .auth import requires
from .models import now_ist
from .models import db, WhatsAppMessage, Lead, WhatsAppTemplate, Charge
from .services import normalize_phone, log, auto_assign, notify, new_token, fill_template

bp = Blueprint("comms", __name__)


def _post(payload):
    token, pid = current_app.config.get("WHATSAPP_TOKEN"), current_app.config.get("WHATSAPP_PHONE_ID")
    if not (token and pid):
        return False, "WhatsApp API not configured — message saved in the CRM only."
    req = urllib.request.Request(f"https://graph.facebook.com/v20.0/{pid}/messages",
                                 data=json.dumps({"messaging_product": "whatsapp", **payload}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True, "sent"
    except Exception as e:  # noqa
        return False, f"WhatsApp error: {e}"


def in_service_window(phone):
    """Meta allows free-text replies only within 24 h of the customer's last message."""
    from datetime import timedelta
    last = WhatsAppMessage.query.filter_by(phone=phone, direction="in").order_by(WhatsAppMessage.created_at.desc()).first()
    return bool(last and last.created_at > now_ist() - timedelta(hours=24))


def send_template_to_lead(tpl, lead, user=None, charge=None):
    """Fill the template for this lead and send it: free text inside the 24-hour window,
    otherwise the Meta-approved template (variables passed in the order they appear in the body)."""
    import re
    text, vals = fill_template(tpl.body, lead, charge)
    phone = lead.whatsapp or lead.phone
    if in_service_window(phone) or not tpl.meta_name:
        ok, info = _post({"to": phone.lstrip("+"), "type": "text", "text": {"body": text}})
        if not ok and not in_service_window(phone) and "configured" not in info:
            info += " (outside 24 h — add the Meta template name to this template)"
    else:
        params = [{"type": "text", "text": vals.get(k, "") or "-"} for k in re.findall(r"{(\w+)}", tpl.body)]
        comp = [{"type": "body", "parameters": params}] if params else []
        ok, info = _post({"to": phone.lstrip("+"), "type": "template",
                          "template": {"name": tpl.meta_name, "language": {"code": tpl.language or "en"}, "components": comp}})
    db.session.add(WhatsAppMessage(lead_id=lead.id, phone=phone, direction="out", body=text, read=True))
    log(lead, f"[Template: {tpl.title}] {text}" + ("" if ok else f"\n⚠ {info}"), "whatsapp", user)
    lead.last_contacted_at = now_ist()
    return ok, info


@bp.route("/leads/<int:lid>/send-template", methods=["POST"])
@requires("leads")
def send_template(lid):
    from .leads import get_lead
    lead = get_lead(lid)
    tpl = db.session.get(WhatsAppTemplate, request.form.get("template_id", type=int)) or abort(404)
    charge = db.session.get(Charge, request.form.get("charge_id", type=int)) if request.form.get("charge_id") else None
    ok, info = send_template_to_lead(tpl, lead, current_user, charge)
    db.session.commit()
    flash("WhatsApp sent" if ok else info, "ok" if ok else "error")
    return redirect(request.referrer or url_for("leads.detail", lid=lid))


@bp.route("/whatsapp/preview/<int:lid>/<int:tid>")
@requires("leads")
def preview(lid, tid):
    from .leads import get_lead
    tpl = db.session.get(WhatsAppTemplate, tid) or abort(404)
    return fill_template(tpl.body, get_lead(lid))[0]


def send_whatsapp(to, body):
    """Send a text message. Returns (ok, info). Without credentials, it only logs."""
    token, pid = current_app.config.get("WHATSAPP_TOKEN"), current_app.config.get("WHATSAPP_PHONE_ID")
    if not (token and pid):
        return False, "WhatsApp API not configured — message saved only."
    req = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{pid}/messages",
        data=json.dumps({"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text",
                         "text": {"body": body}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True, "sent"
    except Exception as e:  # noqa
        return False, str(e)


@bp.route("/whatsapp/")
@requires("leads")
def inbox():
    sub = db.session.query(WhatsAppMessage.phone, func.max(WhatsAppMessage.id).label("mid"))\
        .group_by(WhatsAppMessage.phone).subquery()
    last = WhatsAppMessage.query.join(sub, WhatsAppMessage.id == sub.c.mid).order_by(WhatsAppMessage.id.desc())
    convs = last.all()
    if not current_user.sees_all_leads:
        convs = [m for m in convs if m.lead and m.lead.assigned_to_id == current_user.id]
    if request.args.get("filter") == "unread":
        unread_phones = {p for (p,) in db.session.query(WhatsAppMessage.phone).filter_by(direction="in", read=False)}
        convs = [m for m in convs if m.phone in unread_phones]
    unread = dict(db.session.query(WhatsAppMessage.phone, func.count()).filter_by(direction="in", read=False)
                  .group_by(WhatsAppMessage.phone).all())
    return render_template("whatsapp.html", convs=convs, unread=unread)


@bp.route("/whatsapp/<path:phone>", methods=["GET", "POST"])
@requires("leads")
def thread(phone):
    phone = normalize_phone(phone)
    lead = Lead.query.filter((Lead.whatsapp == phone) | (Lead.phone == phone)).first()
    if request.method == "POST" and request.form.get("body"):
        body = request.form["body"].strip()
        ok, info = send_whatsapp(phone, body)
        db.session.add(WhatsAppMessage(lead_id=lead.id if lead else None, phone=phone, direction="out", body=body, read=True))
        if lead:
            log(lead, body, "whatsapp", current_user)
            lead.last_contacted_at = now_ist()
        db.session.commit()
        if not ok:
            flash(info, "error")
        return redirect(url_for("comms.thread", phone=phone))
    WhatsAppMessage.query.filter_by(phone=phone, direction="in", read=False).update({"read": True})
    db.session.commit()
    msgs = WhatsAppMessage.query.filter_by(phone=phone).order_by(WhatsAppMessage.created_at).all()
    return render_template("whatsapp_thread.html", msgs=msgs, phone=phone, lead=lead)


@bp.route("/webhooks/whatsapp", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":  # Meta verification
        if request.args.get("hub.verify_token") == current_app.config["WHATSAPP_VERIFY_TOKEN"]:
            return request.args.get("hub.challenge", "")
        abort(403)
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for ch in entry.get("changes", []):
            val = ch.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name") for c in val.get("contacts", [])}
            for m in val.get("messages", []):
                phone = normalize_phone(m.get("from"))
                body = m.get("text", {}).get("body") or f"[{m.get('type')}]"
                lead = Lead.query.filter((Lead.whatsapp == phone) | (Lead.phone == phone)).first()
                if not lead:  # every new chat becomes a lead and gets an owner
                    lead = Lead(name=names.get(m.get("from")) or f"WhatsApp {phone[-4:]}", phone=phone,
                                whatsapp=phone, source="WhatsApp", portal_token=new_token())
                    db.session.add(lead)
                    db.session.flush()
                    auto_assign(lead)
                    log(lead, "Lead created from incoming WhatsApp", "system")
                db.session.add(WhatsAppMessage(lead_id=lead.id, phone=phone, direction="in", body=body))
                log(lead, body, "whatsapp")
                notify(lead.assigned_to_id, f"WhatsApp from {lead.name}", f"/whatsapp/{phone}")
    db.session.commit()
    return "ok"


# ------------------------------------------------------------------ chat panel on the lead page
def lead_messages(lead):
    """All WhatsApp messages for a lead: linked by lead id, or sent from/to any of its numbers."""
    phones = {p for p in (lead.phone, lead.whatsapp, normalize_phone(lead.phone), normalize_phone(lead.whatsapp)) if p}
    return (WhatsAppMessage.query
            .filter((WhatsAppMessage.lead_id == lead.id) | (WhatsAppMessage.phone.in_(phones)))
            .order_by(WhatsAppMessage.created_at, WhatsAppMessage.id).all())


@bp.route("/leads/<int:lid>/chat", methods=["GET", "POST"])
@requires("leads")
def lead_chat(lid):
    """GET: the chat messages as an HTML fragment (the panel refreshes it). POST: send a reply."""
    from .leads import get_lead
    lead = get_lead(lid)
    phone = normalize_phone(lead.whatsapp or lead.phone)
    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if body:
            ok, info = send_whatsapp(phone, body)
            db.session.add(WhatsAppMessage(lead_id=lead.id, phone=phone, direction="out", body=body, read=True))
            log(lead, body + ("" if ok else f"\n⚠ {info}"), "whatsapp", current_user)
            lead.last_contacted_at = now_ist()
            db.session.commit()
            if not ok:
                return {"ok": False, "info": info}
        return {"ok": True}
    msgs = lead_messages(lead)
    for m in msgs:
        if m.direction == "in" and not m.read:
            m.read = True
    db.session.commit()
    return render_template("_chat_messages.html", msgs=msgs, lead=lead,
                           in_window=in_service_window(phone))


# ------------------------------------------------------------------ connection check (admin)
def _graph(method, path, data=None):
    """Call the Meta Graph API with the token from the server settings. Returns (status, json)."""
    token = current_app.config.get("WHATSAPP_TOKEN")
    if not token:
        return 0, {"error": {"message": "WHATSAPP_TOKEN is not set in Render"}}
    body = json.dumps(data).encode() if data is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(f"https://graph.facebook.com/v20.0/{path}", data=body, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": {"message": str(e)}}
    except Exception as e:  # noqa
        return 0, {"error": {"message": str(e)}}


@bp.route("/admin/whatsapp", methods=["GET", "POST"])
@requires("admin")
def whatsapp_setup():
    pid = current_app.config.get("WHATSAPP_PHONE_ID")
    if request.method == "POST":
        action = request.form.get("action")
        if action == "subscribe":
            waba = request.form.get("waba_id", "").strip()
            st, js = _graph("POST", f"{waba}/subscribed_apps")
            flash("CRM app is now subscribed — incoming messages will reach the CRM" if st == 200 and js.get("success")
                  else "Meta said: " + js.get("error", {}).get("message", str(js)), "ok" if st == 200 else "error")
        elif action == "test":
            to = normalize_phone(request.form.get("to"))
            st, js = _graph("POST", f"{pid}/messages", {"messaging_product": "whatsapp", "to": (to or "").lstrip("+"),
                            "type": "template", "template": {"name": "hello_world", "language": {"code": "en_US"}}})
            flash(f"Test message sent to {to} ✓" if st == 200 else "Meta said: " + js.get("error", {}).get("message", str(js)),
                  "ok" if st == 200 else "error")
        return redirect(url_for("comms.whatsapp_setup"))
    checks = {"token": bool(current_app.config.get("WHATSAPP_TOKEN")), "phone_id": pid,
              "verify": bool(current_app.config.get("WHATSAPP_VERIFY_TOKEN"))}
    phone = token_info = apps = None
    if checks["token"]:
        st, token_info = _graph("GET", "me?fields=id,name")
        if pid:
            st, phone = _graph("GET", f"{pid}?fields=display_phone_number,verified_name,quality_rating,code_verification_status,platform_type,throughput,name_status")
    waba_id = request.args.get("waba_id") or current_app.config.get("WHATSAPP_WABA_ID") or ""
    if checks["token"] and waba_id:
        st, apps = _graph("GET", f"{waba_id}/subscribed_apps")
    return render_template("whatsapp_setup.html", checks=checks, phone=phone, token_info=token_info, apps=apps,
                           waba_id=waba_id, webhook=url_for("comms.webhook", _external=True))

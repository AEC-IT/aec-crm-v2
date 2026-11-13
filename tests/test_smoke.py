"""Smoke test: every page loads, key flows work. Run: python -m pytest -q  (or python tests/test_smoke.py)"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.models import now_ist, db, User, Lead, Integration, University, WhatsAppMessage
from app.seed import seed


def make():
    d = tempfile.mkdtemp()
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{d}/t.db", "UPLOAD_FOLDER": d, "TESTING": True})
    with app.app_context():
        seed()
        c = User(name="Asha", email="asha@aec.com", role="counselor", branch_id=1, languages="Malayalam")
        c.set_password("password1"); db.session.add(c)
        db.session.add(University(name="Tbilisi State Medical University", country="Georgia", courses="MBBS", fee_usd_per_year=7000))
        db.session.add(Integration(name="Website", kind="Website", api_key="k1", branch_id=1))
        db.session.commit()
    return app


def login(cl, email="admin@aec-edu.com", pw="ChangeMe@123"):
    r = cl.post("/auth/login", data={"email": email, "password": pw})
    assert r.status_code == 302, r.data[:300]


def test_everything():
    app = make()
    cl = app.test_client()
    assert cl.get("/leads/").status_code == 302  # needs login
    login(cl)
    # create lead → auto-assigned to the counsellor
    r = cl.post("/leads/new", data={"name": "Rashid", "phone": "9147712737", "course": "MBBS", "neet_score": "420",
                                    "country_preference": "Georgia", "budget_confirmed": "on", "source": "Walk-in"})
    assert r.status_code == 302
    with app.app_context():
        l = Lead.query.first()
        assert l.phone == "+919147712737" and l.assigned_to.name == "Asha" and l.score > 0
        lid = l.id
    # duplicate is blocked
    r = cl.post("/leads/new", data={"name": "Rashid again", "phone": "+91 91477 12737"})
    assert b"Possible duplicate" in r.data
    # activity + follow-up + stage + university + charge + payment
    fu = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert cl.post(f"/leads/{lid}/activity", data={"kind": "call", "outcome": "Connected", "text": "Interested",
                                                   "next_followup_at": fu}).status_code == 302
    assert cl.post(f"/leads/{lid}/stage", json={"stage": "qualified"}).status_code == 200
    assert cl.post(f"/leads/{lid}/application", data={"university_id": "1"}).status_code == 302
    assert cl.post(f"/finance/lead/{lid}", data={"action": "charge", "fee_type": "Service Charge", "amount": "50000"}).status_code == 302
    assert cl.post(f"/finance/lead/{lid}", data={"action": "payment", "charge_id": "1", "amount": "20000", "mode": "UPI"}).status_code == 302
    with app.app_context():
        l = db.session.get(Lead, lid)
        assert l.stage == "university_shortlisted" and l.balance_due == 30000 and l.followup_overdue
    # API lead capture, with duplicate protection
    r = cl.post("/api/v1/leads", json={"name": "Anu", "phone": "9876543210", "campaign": "Nov MBBS"}, headers={"X-API-Key": "k1"})
    assert r.status_code == 201
    r = cl.post("/api/v1/leads", json={"name": "Anu", "phone": "09876543210"}, headers={"X-API-Key": "k1"})
    assert r.json["duplicate"] is True
    assert cl.post("/api/v1/leads", json={"name": "x", "phone": "1"}).status_code == 401
    # Tally format
    r = cl.post("/api/v1/leads?key=k1", json={"data": {"fields": [{"label": "Name", "value": "Tally Kid"},
                                                                 {"label": "Phone number", "value": "9000000001"}]}})
    assert r.status_code == 201
    # WhatsApp webhook creates a lead from a new number
    cl.post("/webhooks/whatsapp", json={"entry": [{"changes": [{"value": {"contacts": [{"wa_id": "919446548313", "profile": {"name": "Parent"}}],
                                                                         "messages": [{"from": "919446548313", "type": "text", "text": {"body": "I am a parent"}}]}}]}]})
    with app.app_context():
        assert WhatsAppMessage.query.count() == 1 and Lead.query.filter_by(phone="+919446548313").first().assigned_to_id
    # every GET page renders
    pages = ["/", "/leads/", "/leads/closed", "/leads/archived", "/leads/kanban", "/leads/kanban?phase=Application",
             "/leads/call-list", f"/leads/{lid}", f"/leads/{lid}/edit", "/leads/new", "/leads/?export=csv",
             "/leads/?q=Rash&followup=overdue&sort=score", "/documents/", f"/documents/{lid}", "/brochures",
             "/whatsapp/", "/whatsapp/+919446548313", "/finance/dashboard", "/finance/dashboard?p=month",
             "/finance/payments", "/finance/payments?export=csv", "/finance/charges", f"/finance/lead/{lid}", "/finance/receipt/1",
             "/reports/", "/reports/conversion-funnel", "/reports/counselor-performance", "/reports/lead-source-analysis",
             "/reports/monthly-trends", "/reports/pipeline-health", "/reports/followup-report", "/reports/enrollment-projection",
             "/reports/export/all", "/frontdesk/", "/office/", "/admin/", "/admin/integrations", "/auth/users", "/auth/users/new",
             "/auth/users/1", "/auth/password", "/notifications/"]
    from app.modules import CRUD
    for n in CRUD:
        pages += [f"/m/{n}/", f"/m/{n}/new"]
    for p in pages:
        r = cl.get(p)
        assert r.status_code == 200, (p, r.status_code, r.data[:500])
    # generic CRUD create for each simple module
    assert cl.post("/m/visitors/new", data={"name": "Guest", "phone": "999", "in_time": "2026-11-05T10:00"}).status_code == 302
    assert cl.post("/m/bills/new", data={"name": "KSEB", "amount": "1200", "due_date": "2026-11-20", "status": "pending"}).status_code == 302
    assert cl.post("/m/records/new", data={"category": "Utility", "title": "KSEB", "next_due": "2026-11-15"}).status_code == 302
    assert cl.post("/m/appointments/new", data={"title": "Parent meeting", "starts_at": now_ist().strftime("%Y-%m-%dT%H:%M")}).status_code == 302
    for p in ["/", "/office/", "/frontdesk/", "/m/visitors/1"]:
        assert cl.get(p).status_code == 200, p
    # portal (public)
    with app.app_context():
        tok = db.session.get(Lead, lid).portal_token
    pub = app.test_client()
    assert pub.get(f"/portal/{tok}").status_code == 200
    # permissions: counsellor sees only own leads and no finance/admin
    c2 = app.test_client()
    login(c2, "asha@aec.com", "password1")
    assert c2.get("/finance/dashboard").status_code == 403
    assert c2.get("/admin/").status_code == 403
    assert c2.get(f"/leads/{lid}").status_code == 200
    print("ALL OK —", len(pages), "pages checked")


if __name__ == "__main__":
    test_everything()


def test_phase2():
    import pyotp
    app = make()
    app.config.update(UPI_ID="aecstudies@okicici", SERVER_NAME="crm.test")
    cl = app.test_client()
    login(cl)
    cl.post("/leads/new", data={"name": "Jithu Kumar", "phone": "9074403325", "course": "BBA", "country_preference": "China"})
    with app.app_context():
        from app.models import WhatsAppTemplate
        lid = Lead.query.first().id
        tok = Lead.query.first().portal_token
        pay_tpl = WhatsAppTemplate.query.filter_by(category="Payment").first().id
    # template preview fills placeholders
    txt = cl.get(f"/whatsapp/preview/{lid}/1").data.decode()
    assert "Hi Jithu" in txt and "BBA" in txt and "{" not in txt, txt
    # sending without API creds saves to timeline and says not configured
    r = cl.post(f"/leads/{lid}/send-template", data={"template_id": "1"}, follow_redirects=True)
    assert b"not configured" in r.data
    # overdue charge → reminder + pay page with UPI link
    cl.post(f"/finance/lead/{lid}", data={"action": "charge", "fee_type": "Application Fee", "amount": "100000", "due_date": "2026-10-15"})
    r = cl.post("/finance/remind-overdue", follow_redirects=True)
    assert b"Reminders: 0 sent, 1 not delivered" in r.data
    r = cl.post("/finance/remind-overdue", follow_redirects=True)
    assert b"Reminders: 0 sent, 0 not" in r.data  # skipped: reminded <3 days ago
    with app.app_context():
        from app.models import Activity
        msg = Activity.query.filter(Activity.text.like("%Payment reminder%")).first().text
        assert "₹1,00,000" in msg or "₹100,000" in msg and "/finance/pay/" in msg, msg
    r = app.test_client().get(f"/finance/pay/{tok}")
    assert r.status_code == 200 and b"upi://pay?pa=aecstudies%40okicici" in r.data and b"am=100000.00" in r.data
    for p in ["/m/templates/", "/m/templates/new", f"/leads/{lid}", "/finance/dashboard", f"/finance/lead/{lid}", "/auth/2fa/setup"]:
        assert cl.get(p).status_code == 200, p
    # 2-step login: set up, then sign in needs the code
    with cl.session_transaction() as s:
        secret = s["2fa_new_secret"]
    assert cl.post("/auth/2fa/setup", data={"code": "000000"}, follow_redirects=True).data.count(b"didn") == 1
    cl.post("/auth/2fa/setup", data={"code": pyotp.TOTP(secret).now()})
    with app.app_context():
        assert User.query.filter_by(role="super_admin").first().totp_secret == secret
    c2 = app.test_client()
    r = c2.post("/auth/login", data={"email": "admin@aec-edu.com", "password": "ChangeMe@123"})
    assert r.headers["Location"].endswith("/auth/2fa")
    assert c2.get("/leads/").status_code == 302  # not logged in yet
    c2.post("/auth/2fa", data={"code": "123456"})
    assert c2.get("/leads/").status_code == 302
    r = c2.post("/auth/2fa", data={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 302 and c2.get("/leads/").status_code == 200
    # external redirect is refused
    c3 = app.test_client()
    with app.app_context():
        u = User.query.filter_by(email="asha@aec.com").first()
    r = c3.post("/auth/login?next=https://evil.com", data={"email": "asha@aec.com", "password": "password1"})
    assert "evil" not in r.headers["Location"]
    print("PHASE 2 OK")


if __name__ == "__main__":
    test_phase2()


def test_receipts():
    app = make()
    app.config.update(SERVER_NAME="crm.test")
    cl = app.test_client()
    login(cl)
    cl.post("/leads/new", data={"name": "Ayisha Nesrin", "phone": "7012032235", "course": "BBA", "country_preference": "Spain"})
    with app.app_context():
        lead = Lead.query.first(); lid, tok = lead.id, lead.portal_token
    cl.post(f"/finance/lead/{lid}", data={"action": "charge", "fee_type": "Application Fee", "amount": "100000"})
    # quick receipt against the charge, with WhatsApp send ticked
    r = cl.post("/finance/receipts/new", data={"lead_id": lid, "charge_id": "1", "amount": "25500.50", "mode": "UPI",
                                               "reference": "UTR123", "paid_on": "2026-11-10", "send_whatsapp": "on"})
    assert r.status_code == 302 and "send=1" in r.headers["Location"]
    r = cl.get(r.headers["Location"], follow_redirects=True)
    assert b"AEC/2026-27/00001" in r.data and b"Twenty Five Thousand Five Hundred and Fifty Paise" in r.data
    assert b"not configured" in r.data and b"Open in WhatsApp" in r.data and b"wa.me/917012032235" in r.data
    with app.app_context():
        from app.models import WhatsAppMessage, Payment
        assert "Receipt AEC/2026-27/00001" in WhatsAppMessage.query.first().body
        assert db.session.get(Lead, lid).balance_due == 100000 - 25500.50
    # second receipt numbers on; public link works without login, wrong token 404
    cl.post(f"/finance/lead/{lid}", data={"action": "payment", "amount": "1000", "mode": "Cash", "paid_on": "2026-11-10"})
    assert b"AEC/2026-27/00002" in cl.get("/finance/receipts").data
    pub = app.test_client()
    assert pub.get(f"/finance/r/{tok}/1").status_code == 200
    assert pub.get("/finance/r/wrongtoken/1").status_code == 404
    assert b"Send on WhatsApp" not in pub.get(f"/finance/r/{tok}/1").data
    # cancel: stays listed, stops counting
    assert cl.post("/finance/receipt/1/cancel", data={"reason": ""}).status_code == 302
    cl.post("/finance/receipt/1/cancel", data={"reason": "Wrong amount"})
    with app.app_context():
        assert db.session.get(Lead, lid).balance_due == 100000
    assert b"CANCELLED" in cl.get("/finance/receipt/1").data
    for p in ["/finance/receipts", "/finance/receipts?export=csv", "/finance/receipts/new", f"/finance/receipts/new?lead_id={lid}",
              "/finance/dashboard", "/finance/payments", f"/finance/lead/{lid}", f"/leads/{lid}", "/"]:
        assert cl.get(p).status_code == 200, p
    # FY numbering: before April belongs to previous year
    with app.app_context():
        from app.services import next_receipt_no
        from datetime import date
        assert next_receipt_no(date(2027, 3, 31)).startswith("AEC/2026-27/") and next_receipt_no(date(2027, 4, 1)) == "AEC/2027-28/00001"
    print("RECEIPTS OK")


def test_lead_page_v2():
    import io
    app = make()
    app.config.update(SERVER_NAME="crm.test")
    cl = app.test_client()
    login(cl)
    cl.post("/leads/new", data={"name": "Ayisha Nesrin", "phone": "7012032235", "course": "MBBS", "country_preference": "Georgia",
                                "tenth_percent": "90", "location": "Alappuzha"})
    with app.app_context():
        lid = Lead.query.first().id
    # brochures: one matching, one other country, one any-country MBBS
    for title, course, country in [("MBBS Georgia 2027", "MBBS", "Georgia"), ("MBBS Russia", "MBBS", "Russia"), ("MBBS general", "MBBS", ""), ("BBA Spain", "BBA", "Spain")]:
        r = cl.post("/brochures", data={"course": course, "country": country, "title": title,
                                         "file": (io.BytesIO(b"%PDF-1.4 test"), "b.pdf")}, content_type="multipart/form-data")
        assert r.status_code == 302
    page = cl.get(f"/leads/{lid}").data.decode()
    rec = page[page.index('id="brochures"'):page.index('id="timeline"')]
    assert rec.index("MBBS Georgia 2027") < rec.index("Other brochures")
    assert "MBBS general" in rec[:rec.index("Other brochures")] and "MBBS Russia" in rec[rec.index("Other brochures"):]
    for s in ["Lead information", "Student profile", "Readiness", "Alappuzha", "Service charges", "Notes", "Assign lead",
              "Change stage", "Log a call", "Schedule follow-up", "Close lead", "Notify a user"]:
        assert s in page, s
    # send brochure (no API) -> saved in chat with link
    r = cl.post(f"/leads/{lid}/brochure/1/send", data={"ajax": "1"})
    assert r.json["ok"] is False and "/b/1?l=" in r.json["link"]
    with app.app_context():
        from app.models import WhatsAppMessage
        assert "MBBS Georgia 2027" in WhatsAppMessage.query.first().body
    assert app.test_client().get("/b/1").status_code == 200  # public brochure link
    # final university is unique
    with app.app_context():
        db.session.add(University(name="David Tvildiani Medical University", country="Georgia")); db.session.commit()
    cl.post(f"/leads/{lid}/application", data={"university_id": "1", "intake": "Sep 2027"})
    cl.post(f"/leads/{lid}/application", data={"university_id": "2"})
    cl.post(f"/leads/{lid}/application", data={"app_id": "1", "status": "Final"})
    cl.post(f"/leads/{lid}/application", data={"app_id": "2", "status": "Final"})
    with app.app_context():
        from app.models import Application
        assert [a.status for a in Application.query.order_by(Application.id)] == ["Accepted", "Final"]
    assert "Final: David Tvildiani" in cl.get(f"/leads/{lid}").data.decode()
    # note, call, follow-up, notify, close
    cl.post(f"/leads/{lid}/activity", data={"kind": "note", "text": "Parent wants Georgia only"})
    cl.post(f"/leads/{lid}/activity", data={"kind": "call", "outcome": "Connected", "next_followup_at": "2026-11-20T10:00"})
    cl.post(f"/leads/{lid}/activity", data={"next_followup_at": "2026-11-21T11:00", "next_followup_note": "Send fee details"})
    r = cl.post(f"/leads/{lid}/notify", data={"user_id": "2", "message": "call parent"}, follow_redirects=True)
    assert b"has been notified" in r.data
    with app.app_context():
        from app.models import Notification
        assert Notification.query.filter_by(user_id=2).filter(Notification.text.like("%call parent%")).count() == 1
        l = db.session.get(Lead, lid); assert l.next_followup_note == "Send fee details" and l.stage == "university_shortlisted"
    assert "Parent wants Georgia only" in cl.get(f"/leads/{lid}").data.decode()
    cl.post(f"/leads/{lid}/close", data={"reason": "Joined another consultancy", "note": "XYZ Edu"})
    with app.app_context():
        l = db.session.get(Lead, lid); assert l.status == "lost" and l.lost_reason == "Joined another consultancy"
    assert cl.get(f"/leads/{lid}").status_code == 200 and cl.get("/brochures").status_code == 200
    print("LEAD PAGE V2 OK")


def test_stage_whatsapp():
    app = make()
    cl = app.test_client()
    login(cl)
    cl.post("/leads/new", data={"name": "Rashid Khan", "phone": "9147712737", "course": "MBBS", "country_preference": "Georgia"})
    with app.app_context():
        from app.models import WhatsAppTemplate, WhatsAppMessage
        assert WhatsAppTemplate.query.filter(WhatsAppTemplate.stage.isnot(None)).count() == 19
    page = cl.get("/leads/1").data.decode()
    assert "Congratulations Rashid!" in page and "Update &amp; send on WhatsApp" in page and "wa.me" in page
    r = cl.post("/leads/1/stage", data={"stage": "offer_letter_received", "wa_body": "Congratulations Rashid! 🎉 Offer received.",
                                         "send_whatsapp": "1"}, follow_redirects=True)
    assert b"Stage updated to Offer Letter Received" in r.data and b"not sent" in r.data
    with app.app_context():
        assert WhatsAppMessage.query.first().body.startswith("Congratulations Rashid") and db.session.get(Lead, 1).stage == "offer_letter_received"
    r = cl.post("/leads/1/stage", data={"stage": "student_confirmed", "wa_body": "x"}, follow_redirects=True)
    assert b"Stage updated to Student Confirmed" in r.data
    with app.app_context():
        assert WhatsAppMessage.query.count() == 1  # stage only: nothing sent
    assert cl.get("/m/templates/").status_code == 200 and cl.get("/m/templates/new").status_code == 200
    print("STAGE WA OK")

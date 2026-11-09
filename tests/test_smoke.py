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

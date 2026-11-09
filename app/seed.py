import os
from .models import db, User, Branch, AutomationRule, WhatsAppTemplate


def seed():
    if not Branch.query.first():
        db.session.add(Branch(name="Kochi", address="2nd Floor, Kollanur Plaza, Nettipadam Road, Ernakulam - 682 016",
                              phone="+91 95269 90444", email="info@aec-edu.com"))
        db.session.commit()
    if not User.query.filter_by(role="super_admin").first():
        u = User(name="Admin User", email=os.environ.get("ADMIN_EMAIL", "admin@aec-edu.com"), role="super_admin",
                 branch_id=Branch.query.first().id)
        u.set_password(os.environ.get("ADMIN_PASSWORD", "ChangeMe@123"))
        db.session.add(u)
    if not AutomationRule.query.first():
        db.session.add_all([
            AutomationRule(name="Visa approved → book flight follow-up", trigger="stage_changed",
                           condition="stage=visa_approved", action="set_followup_days", action_value="3"),
            AutomationRule(name="New lead → call within 1 day", trigger="lead_created", condition="",
                           action="set_followup_days", action_value="1"),
            AutomationRule(name="Offer letter → notify counsellor", trigger="stage_changed",
                           condition="stage=offer_letter_received", action="notify_user", action_value=""),
        ])
    db.session.commit()
    if not WhatsAppTemplate.query.first():
        db.session.add_all([
            WhatsAppTemplate(title="Welcome", category="Welcome", body=(
                "Hi {name}, thank you for contacting AEC Studies! I'm {counsellor}, your counsellor for {course} abroad. "
                "When is a good time to call you and your parents?")),
            WhatsAppTemplate(title="No answer follow-up", category="Follow-up", body=(
                "Hi {name}, I tried calling you about {course} in {country}. Please reply with a convenient time — {counsellor}, AEC Studies.")),
            WhatsAppTemplate(title="Document request", category="Documents", body=(
                "Hi {name}, to start your application please upload your documents here: {portal}")),
            WhatsAppTemplate(title="Payment reminder", category="Payment", body=(
                "Hi {name}, a gentle reminder that {due} is pending for {fee_type} {due_date}. "
                "You can pay instantly by UPI here: {pay_link} — AEC Studies")),
            WhatsAppTemplate(title="Stage update", category="General", body=(
                "Hi {name}, good news — your application status is now: {stage}. Track your progress: {portal}")),
        ])
    db.session.commit()

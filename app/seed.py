import os
from .models import db, User, Branch, AutomationRule, WhatsAppTemplate, STAGE_LABEL


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
    seed_stage_templates()


STAGE_MESSAGES = {
    "new_lead": "Hi {name}, thank you for contacting AEC Studies! 😊 I'm {counsellor}. We'll call you shortly to understand your plans for {course} abroad.",
    "attempted_contact": "Hi {name}, this is {counsellor} from AEC Studies. I tried calling you about {course} abroad but couldn't reach you. Please reply with a convenient time to talk.",
    "connected": "Hi {name}, it was nice speaking with you! As discussed, I'll share the details for {course} in {country}. Feel free to message me any questions — {counsellor}, AEC Studies.",
    "qualified": "Hi {name}, good news — based on your profile you are eligible for {course} in {country}. Let's book a detailed counselling session with you and your parents. When suits you?",
    "counseling_completed": "Hi {name}, thank you for attending the counselling session with AEC Studies. We'll now shortlist the best universities for you. You can track your progress here: {portal}",
    "university_shortlisted": "Hi {name}, we have shortlisted universities for your {course} in {country}. Please review them with your parents and let us know your choice. — {counsellor}",
    "application_started": "Hi {name}, we have started your university application. Please upload your documents here so we can complete it quickly: {portal}",
    "application_submitted": "Hi {name}, your application has been submitted successfully ✅. We'll update you as soon as we hear from the university. — AEC Studies",
    "offer_letter_received": "Congratulations {name}! 🎉 You have received your offer letter. Let's discuss the next steps for confirming your seat — {counsellor}, AEC Studies.",
    "student_confirmed": "Welcome to the AEC family, {name}! 🎓 Your admission is confirmed. We'll now guide you through the visa process step by step.",
    "visa_process": "Hi {name}, your visa process has started. Please keep your passport and documents ready — we'll tell you exactly what's needed. Track progress here: {portal}",
    "visa_approved": "Great news, {name}! 🎉 Your visa has been approved. Next we'll plan your flight and pre-departure briefing.",
    "flight_booked": "Hi {name}, your flight is booked ✈️. We'll send your pre-departure checklist soon. Please make sure all documents are packed in your hand luggage.",
    "student_departed": "Have a safe journey, {name}! ✈️ All the best for your new chapter in {country}. Message us anytime if you need help — AEC Studies.",
    "enrolled": "Hi {name}, congratulations on joining your university in {country}! 🎓 We're proud of you. Do stay in touch with AEC Studies.",
    "alumni": "Hi {name}, hope you're doing well in {country}! If any friends or family are planning to study abroad, we'd love to help them too. — AEC Studies",
    "dropped_out": "Hi {name}, we understand your plans have changed. If you'd like to restart your study-abroad plans later, AEC Studies is always here to help.",
    "refund_requested": "Hi {name}, we have received your refund request. Our accounts team will review it and update you within a few working days. — AEC Studies",
    "refund_processed": "Hi {name}, your refund has been processed. Please allow a few working days for it to reflect in your account. Thank you — AEC Studies",
}


def seed_stage_templates():
    """Create one WhatsApp message per pipeline stage (only those missing; edits are never overwritten)."""
    have = {t.stage for t in WhatsAppTemplate.query.filter(WhatsAppTemplate.stage.isnot(None)).all()}
    for code, body in STAGE_MESSAGES.items():
        if code not in have:
            db.session.add(WhatsAppTemplate(title=f"Stage: {STAGE_LABEL.get(code, code)}", category="Stage update",
                                            body=body, stage=code))
    db.session.commit()

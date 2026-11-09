"""Database models for AEC Studies CRM v2."""
from datetime import datetime, date
from zoneinfo import ZoneInfo


def now_ist():
    """Naive datetime in India time — the whole app works in IST."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ---------------------------------------------------------------- constants
ROLES = [
    ("super_admin", "Super Admin"), ("director", "Director"),
    ("branch_manager", "Branch Manager"), ("counselor", "Counselor"),
    ("telecaller", "Telecaller"), ("visa_officer", "Visa Officer"),
    ("finance_officer", "Finance Officer"), ("front_desk", "Front Desk"),
    ("marketing", "Marketing Executive"), ("office_admin", "Office Admin"),
]
ROLE_LABEL = dict(ROLES)

# which roles may open which module
PERMISSIONS = {
    "leads": {"super_admin", "director", "branch_manager", "counselor", "telecaller", "visa_officer", "marketing"},
    "documents": {"super_admin", "director", "branch_manager", "counselor", "visa_officer"},
    "universities": {"super_admin", "director", "branch_manager", "counselor", "visa_officer", "marketing"},
    "finance": {"super_admin", "director", "branch_manager", "finance_officer"},
    "reports": {"super_admin", "director", "branch_manager", "marketing"},
    "frontdesk": {"super_admin", "director", "branch_manager", "front_desk", "office_admin"},
    "office": {"super_admin", "director", "branch_manager", "office_admin"},
    "admin": {"super_admin", "director"},
}

PHASES = [
    ("Inquiry", [("new_lead", "New Lead"), ("attempted_contact", "Attempted Contact"), ("connected", "Connected")]),
    ("Counseling", [("qualified", "Qualified"), ("counseling_completed", "Counseling Completed")]),
    ("Application", [("university_shortlisted", "University Shortlisted"), ("application_started", "Application Started"),
                     ("application_submitted", "Application Submitted"), ("offer_letter_received", "Offer Letter Received")]),
    ("Pre-Departure", [("student_confirmed", "Student Confirmed"), ("visa_process", "Visa Process"),
                       ("visa_approved", "Visa Approved"), ("flight_booked", "Flight Booked")]),
    ("Enrolled", [("student_departed", "Student Departed"), ("enrolled", "Enrolled"), ("alumni", "Alumni")]),
    ("Exceptions", [("dropped_out", "Dropped Out"), ("refund_requested", "Refund Requested"),
                    ("refund_processed", "Refund Processed")]),
]
STAGES = [s for _, group in PHASES for s in group]
STAGE_LABEL = dict(STAGES)
STAGE_PHASE = {code: phase for phase, group in PHASES for code, _ in group}
CLOSED_STAGES = {"lost"}
CONVERTED_STAGES = {"student_departed", "enrolled", "alumni"}

COURSES = ["MBBS", "BBA", "MBA", "Masters", "Engineering", "Nursing", "Law", "Arts", "Science",
           "Commerce", "Architecture", "Other"]
COUNTRIES = ["Australia", "Bangladesh", "Canada", "China", "Georgia", "Germany", "Ireland", "Italy",
             "Kazakhstan", "Kyrgyzstan", "Malaysia", "Nepal", "New Zealand", "Philippines", "Russia",
             "Singapore", "Spain", "Sri Lanka", "UAE", "United Kingdom", "United States", "Uzbekistan", "Other"]
SOURCES = ["Manual Entry", "Meta Lead Ad", "Instagram", "Website", "WhatsApp", "Tally Form", "Walk-in",
           "Referral", "Alumni Referral", "AI Chat", "Seminar / Expo", "Google Ads", "API"]
DOC_CHECKLIST = [
    "Passport (front & back)", "Passport-size photo", "10th marksheet", "10th certificate", "12th marksheet",
    "12th certificate", "NEET scorecard", "NEET admit card", "Birth certificate", "Aadhaar card",
    "Transfer certificate", "Conduct certificate", "Migration certificate", "Medical fitness certificate",
    "HIV test report", "Police clearance", "Bank statement", "Sponsor letter", "Parent ID proof",
    "Degree certificate", "Degree marksheets", "Resume / CV", "Statement of purpose", "Recommendation letter",
    "IELTS / English proof", "Offer letter", "Invitation letter", "Visa copy", "Flight ticket",
    "Apostille / attestation",
]
FEE_TYPES = ["Application Fee", "Service Charge", "Processing Fee", "Visa Fee", "Tuition (pass-through)",
             "Hostel (pass-through)", "Flight Ticket", "Insurance", "Other"]
PAY_MODES = ["Cash", "UPI", "Bank Transfer / NEFT", "Cheque / DD", "Card", "Online Link"]


class TimeStamped:
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)


class Branch(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(255))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    active = db.Column(db.Boolean, default=True)


class User(UserMixin, db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(30), default="counselor")
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    languages = db.Column(db.String(120), default="English,Malayalam")
    max_open_leads = db.Column(db.Integer, default=150)
    accepts_auto_assign = db.Column(db.Boolean, default=True)
    active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    totp_secret = db.Column(db.String(64))  # set = 2-step login on
    branch = db.relationship("Branch")

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, pw)

    @property
    def role_label(self):
        return ROLE_LABEL.get(self.role, self.role)

    def can(self, module):
        return self.role in PERMISSIONS.get(module, set())

    @property
    def sees_all_leads(self):
        return self.role in {"super_admin", "director", "branch_manager", "marketing", "visa_officer"}


class University(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    country = db.Column(db.String(60))
    city = db.Column(db.String(80))
    courses = db.Column(db.String(255))
    fee_usd_per_year = db.Column(db.Float)
    hostel_usd_per_year = db.Column(db.Float)
    duration_years = db.Column(db.Float)
    recognition = db.Column(db.String(160))  # NMC, WHO, ECFMG ...
    intakes = db.Column(db.String(80))
    seats_available = db.Column(db.Integer)
    commission_percent = db.Column(db.Float)
    website = db.Column(db.String(200))
    notes = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)


class Lead(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), index=True, nullable=False)
    whatsapp = db.Column(db.String(30), index=True)
    email = db.Column(db.String(120), index=True)
    course = db.Column(db.String(60))
    country_preference = db.Column(db.String(60))
    budget = db.Column(db.Float)
    stage = db.Column(db.String(40), default="new_lead", index=True)
    status = db.Column(db.String(20), default="open")  # open / lost / archived
    lost_reason = db.Column(db.String(160))
    temperature = db.Column(db.String(10), default="warm")  # hot / warm / cold
    score = db.Column(db.Integer, default=0)
    priority = db.Column(db.String(10), default="medium")
    source = db.Column(db.String(60), default="Manual Entry")
    campaign = db.Column(db.String(160))
    ad_name = db.Column(db.String(160))
    referred_by_id = db.Column(db.Integer, db.ForeignKey("lead.id"))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    preferred_language = db.Column(db.String(30))
    # personal
    gender = db.Column(db.String(10))
    dob = db.Column(db.Date)
    location = db.Column(db.String(120))
    district = db.Column(db.String(60))
    state = db.Column(db.String(60), default="Kerala")
    parent_name = db.Column(db.String(120))
    parent_phone = db.Column(db.String(30))
    parent_occupation = db.Column(db.String(120))
    self_occupation = db.Column(db.String(120))
    # academic
    tenth_year = db.Column(db.Integer)
    tenth_percent = db.Column(db.Float)
    twelfth_year = db.Column(db.Integer)
    twelfth_percent = db.Column(db.Float)
    twelfth_group = db.Column(db.String(60))
    pcb_percent = db.Column(db.Float)
    neet_score = db.Column(db.Integer)
    neet_year = db.Column(db.Integer)
    degree_name = db.Column(db.String(120))
    graduation_year = db.Column(db.Integer)
    graduation_cgpa = db.Column(db.Float)
    english_test = db.Column(db.String(60))
    # qualification
    intended_intake = db.Column(db.String(40))
    funding_source = db.Column(db.String(40))
    passport_status = db.Column(db.String(20))
    passport_number = db.Column(db.String(30))
    passport_expiry = db.Column(db.Date)
    decision_maker = db.Column(db.String(20))
    budget_confirmed = db.Column(db.Boolean, default=False)
    documents_ready = db.Column(db.Boolean, default=False)
    competitor = db.Column(db.String(120))
    objection = db.Column(db.String(255))
    # pre-departure
    visa_applied_on = db.Column(db.Date)
    visa_status = db.Column(db.String(30))
    visa_expiry = db.Column(db.Date)
    flight_date = db.Column(db.Date)
    flight_details = db.Column(db.String(200))
    # follow-up
    next_followup_at = db.Column(db.DateTime, index=True)
    next_followup_note = db.Column(db.String(255))
    last_contacted_at = db.Column(db.DateTime)
    portal_token = db.Column(db.String(64), unique=True)

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    branch = db.relationship("Branch")
    referred_by = db.relationship("Lead", remote_side=[id])
    activities = db.relationship("Activity", backref="lead", lazy="dynamic", cascade="all, delete-orphan",
                                 order_by="desc(Activity.created_at)")
    documents = db.relationship("Document", backref="lead", lazy="dynamic", cascade="all, delete-orphan")
    applications = db.relationship("Application", backref="lead", lazy="dynamic", cascade="all, delete-orphan")
    charges = db.relationship("Charge", backref="lead", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def stage_label(self):
        return "Lost" if self.status == "lost" else STAGE_LABEL.get(self.stage, self.stage)

    @property
    def phase(self):
        return STAGE_PHASE.get(self.stage, "")

    @property
    def followup_overdue(self):
        return self.next_followup_at and self.next_followup_at < now_ist()

    def compute_score(self):
        """Simple, explainable lead score 0-100."""
        s = 0
        if self.neet_score:
            s += 20 if self.neet_score >= 137 else 8
        if self.budget_confirmed: s += 15
        if self.passport_status == "Ready": s += 10
        elif self.passport_status == "Applied": s += 5
        if self.documents_ready: s += 10
        if self.decision_maker == "Both": s += 10
        if self.intended_intake: s += 5
        if self.email: s += 5
        idx = [c for c, _ in STAGES].index(self.stage) if self.stage in STAGE_LABEL else 0
        s += min(idx * 3, 25)
        self.score = min(s, 100)
        self.temperature = "hot" if self.score >= 60 else "warm" if self.score >= 30 else "cold"
        return self.score

    @property
    def balance_due(self):
        return sum(c.amount - c.paid for c in self.charges)


class Activity(db.Model):
    """Timeline + audit log for a lead."""
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(20))  # note, call, whatsapp, email, meeting, stage, system, assign
    outcome = db.Column(db.String(40))  # for calls: connected / no answer / busy / switched off / wrong number
    text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now_ist, index=True)
    user = db.relationship("User")


class Application(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"))
    university_id = db.Column(db.Integer, db.ForeignKey("university.id"))
    course = db.Column(db.String(80))
    intake = db.Column(db.String(40))
    status = db.Column(db.String(30), default="Shortlisted")  # Shortlisted/Applied/Offer/Rejected/Accepted
    university = db.relationship("University")


class Document(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), index=True)
    name = db.Column(db.String(120))
    filename = db.Column(db.String(255))
    status = db.Column(db.String(20), default="pending")  # pending / uploaded / verified / rejected
    note = db.Column(db.String(255))
    expiry = db.Column(db.Date)
    uploaded_by_portal = db.Column(db.Boolean, default=False)


class Charge(db.Model, TimeStamped):
    """Service charge / proforma raised against a student."""
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), index=True)
    fee_type = db.Column(db.String(60))
    description = db.Column(db.String(255))
    amount = db.Column(db.Float, default=0)
    gst_percent = db.Column(db.Float, default=18)
    due_date = db.Column(db.Date)
    last_reminded_at = db.Column(db.DateTime)
    payments = db.relationship("Payment", backref="charge", lazy="dynamic")

    @property
    def paid(self):
        return sum(p.amount for p in self.payments)

    @property
    def status(self):
        return "paid" if self.paid >= self.amount else "partial" if self.paid else "pending"


class Payment(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), index=True)
    charge_id = db.Column(db.Integer, db.ForeignKey("charge.id"))
    amount = db.Column(db.Float)
    mode = db.Column(db.String(40))
    reference = db.Column(db.String(80))
    paid_on = db.Column(db.Date, default=date.today)
    receipt_no = db.Column(db.String(30), unique=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    lead = db.relationship("Lead")


class Brochure(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    course = db.Column(db.String(60))
    title = db.Column(db.String(160))
    filename = db.Column(db.String(255))
    views = db.Column(db.Integer, default=0)


class WhatsAppMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), index=True)
    phone = db.Column(db.String(30), index=True)
    direction = db.Column(db.String(3))  # in / out
    body = db.Column(db.Text)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now_ist)
    lead = db.relationship("Lead")


class Appointment(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160))
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    starts_at = db.Column(db.DateTime, index=True)
    mode = db.Column(db.String(20), default="Office")  # Office / Phone / Video
    status = db.Column(db.String(20), default="scheduled")
    notes = db.Column(db.String(255))
    lead = db.relationship("Lead")
    user = db.relationship("User")


class OfficeTask(db.Model, TimeStamped):
    """Purchase / maintenance requests, travel, onboarding, routed messages."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20))  # purchase, travel, onboarding, message
    title = db.Column(db.String(160))
    details = db.Column(db.Text)
    amount = db.Column(db.Float)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    status = db.Column(db.String(20), default="open")
    due_date = db.Column(db.Date)
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])


class Expense(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    category = db.Column(db.String(60))
    description = db.Column(db.String(255))
    amount = db.Column(db.Float)
    spent_on = db.Column(db.Date, default=date.today)
    mode = db.Column(db.String(40))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))


class Visitor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    purpose = db.Column(db.String(160))
    meeting_with_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    in_time = db.Column(db.DateTime, default=now_ist)
    out_time = db.Column(db.DateTime)
    meeting_with = db.relationship("User")


class Courier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(3))  # in / out
    party = db.Column(db.String(160))
    item = db.Column(db.String(160))
    courier_company = db.Column(db.String(80))
    tracking_no = db.Column(db.String(80))
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    handled_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=now_ist)


class Bill(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    bill_type = db.Column(db.String(40))
    consumer_no = db.Column(db.String(60))
    provider = db.Column(db.String(80))
    amount = db.Column(db.Float)
    due_date = db.Column(db.Date)
    frequency = db.Column(db.String(20), default="Monthly")
    status = db.Column(db.String(20), default="pending")
    paid_on = db.Column(db.Date)
    paid_mode = db.Column(db.String(40))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))


class InventoryItem(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    description = db.Column(db.String(255))
    category = db.Column(db.String(40))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    qty = db.Column(db.Integer, default=1)
    location = db.Column(db.String(80))
    responsible = db.Column(db.String(80))
    serial = db.Column(db.String(80))
    active = db.Column(db.Boolean, default=True)
    branch = db.relationship("Branch")


class OfficeRecord(db.Model, TimeStamped):
    """Compliance vault: bank, utility, rent, property, vehicle, tax."""
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(30))
    title = db.Column(db.String(160))
    reference = db.Column(db.String(80))
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    next_due = db.Column(db.Date)
    amount = db.Column(db.Float)
    notes = db.Column(db.Text)
    branch = db.relationship("Branch")


class AutomationRule(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    trigger = db.Column(db.String(40))  # lead_created, stage_changed, followup_overdue, payment_due
    condition = db.Column(db.String(160))  # e.g. stage=visa_approved
    action = db.Column(db.String(40))  # notify_user, whatsapp_template, create_task, assign
    action_value = db.Column(db.String(160))
    active = db.Column(db.Boolean, default=True)


class Integration(db.Model, TimeStamped):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    kind = db.Column(db.String(40))
    api_key = db.Column(db.String(64), unique=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branch.id"))
    active = db.Column(db.Boolean, default=True)
    leads_received = db.Column(db.Integer, default=0)
    last_used = db.Column(db.DateTime)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    text = db.Column(db.String(255))
    link = db.Column(db.String(200))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now_ist)


class WhatsAppTemplate(db.Model, TimeStamped):
    """Reusable message. Body placeholders: {name} {course} {country} {counsellor} {due} {pay_link} {portal}.
    meta_name: name of the Meta-approved template (needed to message someone outside the 24-hour window)."""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120))
    category = db.Column(db.String(30), default="General")
    body = db.Column(db.Text)
    meta_name = db.Column(db.String(120))
    language = db.Column(db.String(10), default="en")
    active = db.Column(db.Boolean, default=True)


class StoredFile(db.Model):
    """Uploaded files kept in the database, so free hosting (no persistent disk) never loses them."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, index=True)
    mimetype = db.Column(db.String(120))
    data = db.Column(db.LargeBinary)
    created_at = db.Column(db.DateTime, default=now_ist)

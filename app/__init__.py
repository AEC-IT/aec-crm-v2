import os
from datetime import datetime
from flask import Flask
from flask_login import LoginManager
from .models import now_ist
from .models import db, User, Notification, WhatsAppMessage, ROLE_LABEL, STAGE_LABEL

login_manager = LoginManager()
login_manager.login_view = "auth.login"


def create_app(config=None):
    app = Flask(__name__)
    db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "crm.db"))
    for prefix in ("postgres://", "postgresql://"):  # Render gives postgres://; pin the psycopg2 driver
        if db_url.startswith(prefix):
            db_url = "postgresql+psycopg2://" + db_url[len(prefix):]
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "change-me-in-production"),
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # test each pooled connection before use and refresh it every 4 min — free Postgres drops idle SSL connections
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 240},
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads")),
        MAX_CONTENT_LENGTH=15 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        WHATSAPP_TOKEN=os.environ.get("WHATSAPP_TOKEN"),
        WHATSAPP_PHONE_ID=os.environ.get("WHATSAPP_PHONE_ID"),
        UPI_ID=os.environ.get("UPI_ID"),
        COMPANY_NAME=os.environ.get("COMPANY_NAME", "AEC Studies Pvt. Ltd."),
        COMPANY_GSTIN=os.environ.get("COMPANY_GSTIN", ""),
        UPI_NAME=os.environ.get("UPI_NAME", "AEC Studies Pvt Ltd"),
        WHATSAPP_VERIFY_TOKEN=os.environ.get("WHATSAPP_VERIFY_TOKEN", "aec-verify"),
    )
    if config:
        app.config.update(config)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from .auth import bp as auth_bp
    from .leads import bp as leads_bp
    from .modules import bp as modules_bp
    from .finance import bp as finance_bp
    from .reports import bp as reports_bp
    from .comms import bp as comms_bp
    from .api import bp as api_bp
    for b in (auth_bp, leads_bp, modules_bp, finance_bp, reports_bp, comms_bp, api_bp):
        app.register_blueprint(b)

    @app.context_processor
    def globals_():
        from flask_login import current_user
        unread = unread_wa = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(user_id=current_user.id, read=False).count()
            unread_wa = WhatsAppMessage.query.filter_by(direction="in", read=False).count()
        return dict(ROLE_LABEL=ROLE_LABEL, STAGE_LABEL=STAGE_LABEL, now=now_ist(),
                    unread_notifications=unread, unread_whatsapp=unread_wa)

    @app.template_filter("inr")
    def inr(v):
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            return v
        s = f"{abs(v):.0f}"
        if len(s) > 3:  # Indian grouping 1,00,000
            head, tail = s[:-3], s[-3:]
            parts = []
            while len(head) > 2:
                parts.insert(0, head[-2:]); head = head[:-2]
            if head: parts.insert(0, head)
            s = ",".join(parts) + "," + tail
        return ("-" if v < 0 else "") + "₹" + s

    @app.template_filter("dict_without_page")
    def dict_without_page(d):
        return {k: v for k, v in d.items() if k != "page"}

    @app.template_filter("dt")
    def dt(v, fmt="%d %b %Y"):
        return v.strftime(fmt) if v else "—"

    from flask import render_template

    @app.errorhandler(403)
    def e403(e):
        return render_template("error.html", code=403, msg="You don't have access to this page."), 403

    @app.errorhandler(404)
    def e404(e):
        return render_template("error.html", code=404, msg="Page not found."), 404

    @app.cli.command("init-db")
    def init_db():
        """Create tables and a default admin."""
        from .seed import seed
        db.create_all()
        seed()
        print("Database ready.")

    with app.app_context():
        db.create_all()
        add_missing_columns()
    return app


def add_missing_columns():
    """Add any model column the live table lacks (new features on an existing database). Never drops anything."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have:
                ddl = col.type.compile(dialect=db.engine.dialect)
                try:
                    db.session.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                    db.session.commit()
                except Exception:  # another worker added it a moment earlier
                    db.session.rollback()


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

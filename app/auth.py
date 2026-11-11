from functools import wraps
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
from flask_login import login_user, logout_user, login_required, current_user
from .models import now_ist
from .models import db, User, Branch, ROLES

bp = Blueprint("auth", __name__, url_prefix="/auth")


def requires(module):
    """Route decorator: user must be logged in and have access to `module`."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if not current_user.can(module):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("leads.dashboard"))
    if request.method == "POST":
        u = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if u and u.active and u.check_password(request.form.get("password", "")):
            if u.totp_secret:  # password OK → now ask for the 6-digit code
                session["2fa_uid"], session["2fa_remember"] = u.id, bool(request.form.get("remember"))
                session["2fa_next"] = request.args.get("next")
                return redirect(url_for("auth.two_factor"))
            return _finish_login(u, bool(request.form.get("remember")), request.args.get("next"))
        flash("Invalid email or password", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/users")
@requires("admin")
def users():
    return render_template("users.html", users=User.query.order_by(User.name).all())


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:uid>", methods=["GET", "POST"])
@requires("admin")
def user_form(uid=None):
    u = db.session.get(User, uid) if uid else User()
    if uid and not u:
        abort(404)
    if request.method == "POST":
        f = request.form
        u.name, u.email, u.phone = f["name"], f["email"].strip().lower(), f.get("phone")
        u.role = f["role"]
        u.branch_id = int(f["branch_id"]) if f.get("branch_id") else None
        u.languages = f.get("languages")
        u.max_open_leads = int(f.get("max_open_leads") or 150)
        u.accepts_auto_assign = bool(f.get("accepts_auto_assign"))
        u.active = bool(f.get("active"))
        if f.get("password"):
            u.set_password(f["password"])
        if not uid:
            db.session.add(u)
        db.session.commit()
        flash("User saved", "ok")
        return redirect(url_for("auth.users"))
    return render_template("user_form.html", u=u, roles=ROLES, branches=Branch.query.all())


@bp.route("/password", methods=["GET", "POST"])
@login_required
def password():
    if request.method == "POST":
        if not current_user.check_password(request.form["current"]):
            flash("Current password is wrong", "error")
        elif len(request.form["new"]) < 8:
            flash("Use at least 8 characters", "error")
        else:
            current_user.set_password(request.form["new"])
            db.session.commit()
            flash("Password changed", "ok")
            return redirect(url_for("leads.dashboard"))
    return render_template("password.html")


def _finish_login(u, remember, nxt):
    login_user(u, remember=remember)
    u.last_login = now_ist()
    db.session.commit()
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):  # only local redirects
        return redirect(nxt)
    return redirect(url_for("leads.dashboard"))


@bp.route("/2fa", methods=["GET", "POST"])
def two_factor():
    import pyotp
    uid = session.get("2fa_uid")
    u = db.session.get(User, uid) if uid else None
    if not u:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        fails = session.get("2fa_fails", 0)
        if fails >= 5:
            session.clear()
            flash("Too many wrong codes — sign in again", "error")
            return redirect(url_for("auth.login"))
        if pyotp.TOTP(u.totp_secret).verify(request.form.get("code", "").replace(" ", ""), valid_window=1):
            remember, nxt = session.pop("2fa_remember", False), session.pop("2fa_next", None)
            session.pop("2fa_uid", None); session.pop("2fa_fails", None)
            return _finish_login(u, remember, nxt)
        session["2fa_fails"] = fails + 1
        flash("Wrong code — try the newest one in your app", "error")
    return render_template("two_factor.html")


@bp.route("/2fa/setup", methods=["GET", "POST"])
@login_required
def two_factor_setup():
    import pyotp
    if request.method == "POST":
        if request.form.get("disable"):
            if current_user.check_password(request.form.get("password", "")):
                current_user.totp_secret = None
                db.session.commit()
                flash("2-step login turned off", "ok")
            else:
                flash("Password is wrong", "error")
            return redirect(url_for("auth.two_factor_setup"))
        secret = session.get("2fa_new_secret")
        if secret and pyotp.TOTP(secret).verify(request.form.get("code", "").replace(" ", ""), valid_window=1):
            current_user.totp_secret = secret
            session.pop("2fa_new_secret", None)
            db.session.commit()
            flash("2-step login is on. You'll need the code from your app every time you sign in.", "ok")
            return redirect(url_for("auth.two_factor_setup"))
        flash("That code didn't match — scan again and use the newest code", "error")
    uri = None
    if not current_user.totp_secret:
        session["2fa_new_secret"] = session.get("2fa_new_secret") or pyotp.random_base32()
        uri = pyotp.TOTP(session["2fa_new_secret"]).provisioning_uri(name=current_user.email, issuer_name="AEC CRM")
    return render_template("two_factor_setup.html", uri=uri, secret=session.get("2fa_new_secret"))


@bp.route("/users/<int:uid>/reset-2fa", methods=["POST"])
@requires("admin")
def reset_2fa(uid):
    u = db.session.get(User, uid) or abort(404)
    u.totp_secret = None
    db.session.commit()
    flash(f"2-step login reset for {u.name} (lost phone)", "ok")
    return redirect(url_for("auth.user_form", uid=uid))

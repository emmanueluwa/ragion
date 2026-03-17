from flask import (
    Blueprint,
    current_app,
    url_for,
    redirect,
    render_template,
    request,
    jsonify,
)
from flask_login import current_user, login_user, login_required, logout_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_mail import Message
from models import User, db
from datetime import datetime, timezone
from extensions import mail

auth = Blueprint("auth", __name__)


def get_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def send_magic_link(email, token):
    link = url_for("auth.verify_token", token=token, _external=True)

    msg = Message(
        subject="Your login link for Swiftciv",
        recipients=[email],
        body=f"""
Hello,

Click the link below to log in to Swiftciv. This link expires in 15 minutes.

{link}

If you did not request this, please ignore this email.
""",
    )
    return mail.send(msg)


@auth.route("/login", methods=["GET"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    return render_template("login.html")


@auth.route("/login", methods=["POST"])
def request_magic_link():
    email = request.form.get("email") or (request.get_json() or {}).get("email")

    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.is_approved:
        return (
            jsonify({"error": "No account found. Please sign up to get access."}),
            403,
        )

    s = get_serializer()
    token = s.dumps(email, salt="magic-link")
    send_magic_link(email, token)

    return jsonify({"message": "Login link sent. Check your email."}), 200


@auth.route("/verify/<token>")
def verify_token(token):
    s = get_serializer()

    try:
        email = s.loads(token, salt="magic-link", max_age=900)
    except SignatureExpired:
        return render_template(
            "login.html", error="Link expired. Please request a new one."
        )
    except BadSignature:
        return render_template("login.html", error="Invalid link.")

    user = User.query.filter_by(email=email).first()
    if not user:
        return render_template("login.html", error="User not found.")

    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    login_user(user, remember=True)

    return redirect(url_for("index"))


@auth.route("/logout")
@login_required
def logout():
    logout_user()

    return redirect(url_for("auth.login"))

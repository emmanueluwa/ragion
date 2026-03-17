import os
import stripe

from flask import Blueprint, redirect, render_template, request, jsonify
from models import User, db
from blueprints.auth import get_serializer, send_magic_link

billing = Blueprint("billing", __name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")


@billing.route("/checkout")
def checkout():
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        mode="subscription",
        success_url="https://swiftciv.com/checkout/success?session_id={CHECKOUT_SESSION_ID}",
        cancel_url="https://swiftciv.com/",
        allow_promotion_codes=True,
    )

    return redirect(session.url, code=303)


@billing.route("/checkout/success")
def checkout_success():
    return render_template("success.html")


@billing.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email")

        if email:
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, is_approved=True, is_active=True)
                db.session.add(user)
            elif not user.is_approved:
                user.is_approved = True
            db.session.commit()

            s = get_serializer()
            token = s.dumps(email, salt="magic-link")
            send_magic_link(email, token)

    return jsonify({"status": "ok"}), 200

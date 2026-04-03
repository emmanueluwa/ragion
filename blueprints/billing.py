import os
import stripe

from flask import Blueprint, redirect, render_template, request, jsonify
from flask_login import current_user, login_required
from models import User, db
from blueprints.auth import get_serializer, send_magic_link

billing = Blueprint("billing", __name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")


def get_stripe_customer(email):
    """find stripe customer using email"""
    customers = stripe.Customer.list(email=email, limit=1)
    if customers.data:
        return customers.data[0]

    return None


def get_stripe_subscription(customer_id):
    """get active subscription for customer"""
    subscriptions = stripe.Subscription.list(
        customer=customer_id, status="all", limit=1
    )
    if subscriptions.data:
        return subscriptions.data[0]

    return None


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


@billing.route("/account")
@login_required
def account():
    """account page showing subscription details"""
    subscription_data = {
        "plan": "SwiftCiv Pro",
        "price": "$97/month",
        "status": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
        "customer_id": None,
        "subscription_id": None,
    }

    try:
        customer = get_stripe_customer(current_user.email)
        if customer:
            subscription_data["customer_id"] = customer.id
            subscription = get_stripe_subscription(customer.id)

            if subscription:
                subscription_data["subscription_id"] = subscription.id
                subscription_data["status"] = subscription.status
                subscription_data["cancel_at_period_end"] = (
                    subscription.cancel_at_period_end
                )

                from datetime import datetime, timezone

                subscription_data["current_period_end"] = datetime.fromtimestamp(
                    subscription.current_period_end, tz=timezone.utc
                ).strftime("%d %B %Y")

    except stripe.error.StripeError as e:
        subscription_data["error"] = "Could not load subscription details."

    return render_template("account.html", subscription=subscription_data)


@billing.route("/account/cancel", methods=["POST"])
@login_required
def cancel_subscription():
    """
    cancel subscription at period end
    """
    try:
        customer = get_stripe_customer(current_user.email)
        if not customer:
            return jsonify({"error": "No subscription found."}), 404

        subscription = get_stripe_subscription(customer.id)
        if not subscription:
            return jsonify({"error": "No active subscription found."}), 404

        if subscription.cancel_at_period_end:
            return jsonify({"error": "Subscription is already set to cancel."}), 400

        stripe.Subscription.modify(subscription.id, cancel_at_period_end=True)

        return (
            jsonify(
                {
                    "message": "Your subscription will cancel at the end of the current billing period. You will keep access until then."
                }
            ),
            200,
        )

    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 500


@billing.route("/account/reactivate", methods=["POST"])
@login_required
def reactivate_subscription():
    """
    reactivate a subscription that was set to cancel at period end
    """
    try:
        customer = get_stripe_customer(current_user.email)
        if not customer:
            return jsonify({"error": "No subscription found."}), 404

        subscription = get_stripe_subscription(customer.id)
        if not subscription:
            return jsonify({"error": "No subscription found."}), 400

        if not subscription.cancel_at_period_end:
            return jsonify({"error": "Subscription is not set to cancel."}), 400

        stripe.Subscription.modify(subscription.id, cancel_at_period_end=False)

        return jsonify({"message": "Your subscription has been activated"}), 200

    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 500


@billing.route("/account/update-payment", methods=["POST"])
@login_required
def update_payment():
    """
    stripe hosted session allowing user to update payement method
    """
    try:
        customer = get_stripe_customer(current_user.email)
        if not customer:
            return jsonify({"error": "No subscription found."})

        session = stripe.billing_portal.Session.create(
            customer=customer.id, return_url="https://swiftciv.com/account"
        )

        return jsonify({"url": session.url}), 200

    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 500


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

    elif event["type"] == "customer.subscription.deleted":
        # subscription fully ended and revoking access
        customer_id = event["data"]["object"]["customer"]

        try:
            customer = stripe.Customer.retrieve(customer_id)

            email = customer.email
            if email:
                user = User.query.filter_by(email=email).first()
                if user:
                    user.is_approved = False
                    db.session.commit()

        except stripe.error.StripeError:
            pass

    return jsonify({"status": "ok"}), 200

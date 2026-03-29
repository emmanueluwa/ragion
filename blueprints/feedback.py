import os
import requests

from flask import request, jsonify
from flask_login import current_user, login_required

from flask import Blueprint

feedback = Blueprint("feedback", __name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram(message):
    """sending feedback to telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=5,
        )

        return res.ok

    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


@feedback.route("/feedback", methods=["POST"])
@login_required
def submit_feedback():
    data = request.get_json()

    message = (data or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400

    if len(message) > 2000:
        return (
            jsonify(
                {"error": "Message is too long. Please keep it under 2000 characters."}
            ),
            400,
        )

    telegram_message = (
        f"💬 SwiftCiv Feedback\n\n"
        f"From: {current_user.email}\n\n"
        f"Message:\n{message}"
    )

    success = send_telegram(telegram_message)

    if success:
        return jsonify({"message": "Thank you for your feedback!"}), 200
    else:
        return (
            jsonify(
                {
                    "error": "Failed to send feedback. Please email us at support@swiftciv.com"
                }
            ),
            500,
        )

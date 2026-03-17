import os
from flask import Flask, render_template, redirect, url_for
from flask_login import current_user, login_required

from extensions import mail, login_manager
from models import db, User
from celery_config import celery_app
from blueprints.auth import auth as auth_blueprint
from blueprints.chat import chat as chat_blueprint
from blueprints.documents import documents as documents_blueprint
from blueprints.billing import billing as billing_blueprint

celery_app.autodiscover_tasks(["tasks"], force=True)


def create_app():
    app = Flask(__name__)

    # config
    app.secret_key = os.environ.get("SECRET_KEY")

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # mail config
    app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
    app.config["MAIL_USE_TLS"] = True
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME")

    # initialise extensions
    db.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(user_id)

    # register blueprints
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(chat_blueprint)
    app.register_blueprint(documents_blueprint)
    app.register_blueprint(billing_blueprint)

    # creating tables
    with app.app_context():
        db.create_all()

    return app


app = create_app()


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("landing.html")


@app.route("/app")
@login_required
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

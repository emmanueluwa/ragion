import os
import uuid
import boto3
from botocore.client import Config
from datetime import datetime

from flask import Flask, render_template, jsonify, request, session
from flask_login import current_user, login_required
from tasks import llm_get_state, llm_call, process_file
from werkzeug.utils import secure_filename
from celery_config import celery_app

import redis

from models import Document, db, User
from extensions import mail, login_manager
from auth import auth as auth_blueprint

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

    # initialise extenstions
    db.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(user_id)

    # register blueprints
    app.register_blueprint(auth_blueprint)

    # creating tables
    with app.app_context():
        db.create_all()

    return app


app = create_app()

# redis connection for progress tracking
redis_endpoint = os.environ.get("REDIS_ENDPOINT", "172.18.0.1")
redis_port = os.environ.get("REDIS_PORT", "6379")
redis_password = os.environ.get("REDIS_PASSWORD")

if redis_password:
    redis_url = f"redis://default:{redis_password}@{redis_endpoint}:{redis_port}/0"
else:
    redis_url = f"redis://{redis_endpoint}:{redis_port}/0"

r = redis.Redis.from_url(redis_url, decode_responses=True)

# s3 client pointing to hetzner object storage
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
    aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
    aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
    config=Config(signature_version="s3v4"),
)

S3_BUCKET = os.environ.get("S3_BUCKET_NAME")


@app.route("/")
@login_required
def index():
    return render_template("index.html")


# routes
@app.route("/get", methods=["GET", "POST"])
@login_required
def chat():
    try:
        from tasks import llm_get_state, llm_call

        msg = request.form["msg"]
        input = msg.strip()

        # checking to see if we are waiting for a State from the user
        if "waiting_for_state" in session and session["waiting_for_state"]:
            # state provided for previous question from user
            original_question = session.get("original_question", "")
            state_provided = input

            combined_query = f"{original_question} for {state_provided}"

            # saving state for future questions and clearing flags
            session["last_state"] = state_provided
            session.pop("waiting_for_state", None)
            session.pop("original_question", None)

            task = llm_call.delay(combined_query, state_provided)

            return jsonify(
                {
                    "success": True,
                    "task_id": task.id,
                    "needs_state": False,
                }
            )

        else:
            # checking if state is mentioned in new question
            state_task = llm_get_state.delay(input)
            state_result = state_task.get(timeout=7)

            if state_result.strip().lower() == "none":
                last_state = session.get("last_state")

                if last_state:
                    combined_query = f"{input} for {last_state}"

                    task = llm_call.delay(combined_query, last_state)

                    return jsonify(
                        {
                            "success": True,
                            "task_id": task.id,
                            "needs_state": False,
                        }
                    )

                else:
                    # no state mentioned and no previous state
                    session["waiting_for_state"] = True
                    session["original_question"] = input

                    return jsonify(
                        {
                            "success": True,
                            "response": "For which county would you like to know this information?",
                            "needs_state": True,
                        }
                    )

            else:
                # saving found state for future and continuing to llm query
                session["last_state"] = state_result.strip()

                task = llm_call.delay(input, state_result)

                return jsonify(
                    {
                        "success": True,
                        "task_id": task.id,
                        "needs_state": False,
                    }
                )

    except Exception as e:
        session.pop("waiting_for_state", None)
        session.pop("original_question", None)

        return jsonify({"error": str(e)}), 500


# checking status of task
@app.route("/check_task/<task_id>", methods=["GET"])
@login_required
def check_task(task_id):
    try:
        from tasks import celery_app

        task = celery_app.AsyncResult(task_id)

        if task.state == "SUCCESS":
            return jsonify({"status": "SUCCESS", "response": task.result})

        elif task.state == "PENDING":
            return jsonify({"status": "PENDING"})

        elif task.state == "FAILURE":
            return jsonify({"status": "FAILURE", "error": str(task.info)})

        else:
            return jsonify({"status": task.state})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# uploading documents for chatbot reference
@app.route("/upload", methods=["POST"])
@login_required
def upload():
    from tasks import process_file

    if "file" not in request.files:
        return jsonify({"error": "no files added"}), 400

    file = request.files["file"]
    county = request.form.get("county", "")
    description = request.form.get("description", "")

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    file_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    s3_key = f"{current_user.id}/{file_id}_{filename}"

    # upload to object storage
    s3.upload_fileobj(
        file, S3_BUCKET, s3_key, ExtraArgs={"ContentType": "application/pdf"}
    )

    # save to db
    doc = Document(
        id=file_id,
        user_id=current_user.id,
        filename=filename,
        s3_key=s3_key,
        county=county,
        description=description,
        status="processing",
    )
    db.session.add(doc)
    db.session.commit()

    # start indexing task
    task = process_file.delay(s3_key, file_id, county, description)

    return jsonify({"task_id": task.id, "file_id": file_id})


@app.route("/documents", methods=["GET"])
@login_required
def get_documents():
    docs = (
        Document.query.filter_by(user_id=current_user.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    return jsonify([doc.to_dict() for doc in docs])


@app.route("/documents/<doc_id>", methods=["DELETE"])
@login_required
def delete_docuemnt(doc_id):
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({"error": "Document not found"}), 404

    s3.delete_object(Bucket=S3_BUCKET, Key=doc.s3_key)

    db.session.delete(doc)
    db.session.commit()

    return jsonify({"message": "Document deleted"}), 200


@app.route("/index_progress/<task_id>")
def get_index_progress(task_id):
    """
    redis hash with task id key, celery updates hash as it runs
    r.hset(task_id, mapping={"percent": 40, "status": "Embedding chunks"})
    """
    progress = r.hgetall(task_id)
    percent = progress.get("percent", 0)
    status = progress.get("status", "Starting...")

    # update document status in db
    if str(percent) == "100" and status == "Indexing complete":
        doc = Document.query.filter_by(
            user_id=current_user.id, status="processing"
        ).first()

        if doc:
            doc.status = "indexed"
            doc.indexed_at = datetime.utcnow()
            db.session.commit()

    return jsonify({"percent": percent, "status": status})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

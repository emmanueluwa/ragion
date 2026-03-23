import os
import uuid
import hashlib
import boto3
from botocore.client import Config
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from models import Document, db
from tasks import delete_document_task, process_file

import redis

documents = Blueprint("documents", __name__)


# s3 client pointing to hetzner object storage
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
    aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
    aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
    config=Config(signature_version="s3v4"),
)

S3_BUCKET = os.environ.get("S3_BUCKET_NAME")

# redis connection for progress tracking
redis_endpoint = os.environ.get("REDIS_ENDPOINT", "172.18.0.1")
redis_port = os.environ.get("REDIS_PORT", "6379")
redis_password = os.environ.get("REDIS_PASSWORD")

if redis_password:
    redis_url = f"redis://default:{redis_password}@{redis_endpoint}:{redis_port}/0"
else:
    redis_url = f"redis://{redis_endpoint}:{redis_port}/0"

r = redis.Redis.from_url(redis_url, decode_responses=True)


def hash_file(file):
    md5 = hashlib.md5()
    # reading file in 4kb chunks till end
    for chunk in iter(lambda: file.read(4096), b""):
        md5.update(chunk)

    # reset file pointer to start
    file.seek(0)

    return md5.hexdigest()


@documents.route("/upload", methods=["POST"])
@login_required
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    county = request.form.get("county", "")
    description = request.form.get("description", "")

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    content_hash = hash_file(file)

    existing = Document.query.filter_by(
        user_id=current_user.id, content_hash=content_hash
    ).first()

    if existing:
        return (
            jsonify(
                {
                    "error": f"This document is already in your knowledge base as '{existing.filename}'. Delete it first if you want to re-upload."
                }
            ),
            400,
        )

    file_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    s3_key = f"{current_user.id}/{file_id}_{filename}"
    namespace = f"user_{current_user.id}"

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
        content_hash=content_hash,
        pinecone_namespace=namespace,
        status="processing",
    )
    db.session.add(doc)
    db.session.commit()

    # start indexing task
    task = process_file.delay(s3_key, file_id, county, description, current_user.id)

    return jsonify({"task_id": task.id, "file_id": file_id})


@documents.route("/documents", methods=["GET"])
@login_required
def get_documents():
    docs = (
        Document.query.filter_by(user_id=current_user.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    return jsonify([doc.to_dict() for doc in docs])


@documents.route("/documents/<doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({"error": "Document not found"}), 404

    if doc.status in ("deleting", "delete_failed"):
        return jsonify({"error": "Document is already being deleted"}), 400

    # mark as deleting  so ui updates
    doc.status = "deleting"
    db.session.commit()

    # queue async delition task
    delete_document_task.delay(
        doc.id, doc.s3_key, doc.pinecone_namespace, current_user.email
    )

    return jsonify({"message": "Document deletion started"}), 200


@documents.route("/index_progress/<task_id>")
@login_required
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
            doc.indexed_at = datetime.now(timezone.utc)
            db.session.commit()

    return jsonify({"percent": percent, "status": status})

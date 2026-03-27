from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone
import uuid

db = SQLAlchemy()


def generate_uuid():
    return str(uuid.uuid4())


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_login = db.Column(db.DateTime, nullable=True)
    documents = db.relationship(
        "Document", backref="owner", lazy=True, cascade="all, delete-orphan"
    )
    messages = db.relationship(
        "Message", backref="user", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User {self.email}>"


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False)
    filename = db.Column(db.String(500), nullable=False)
    s3_key = db.Column(db.String(500), nullable=False)
    county = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    content_hash = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(50), default="processing", nullable=False)
    uploaded_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    indexed_at = db.Column(db.DateTime, nullable=True)
    pinecone_namespace = db.Column(db.String(255), nullable=True)

    vectors = db.relationship(
        "DocumentVector", backref="document", lazy=True, cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "county": self.county,
            "description": self.description,
            "status": self.status,
            "uploaded_at": self.uploaded_at.isoformat(),
        }

    def __repr__(self):
        return f"<Document {self.filename}>"


class DocumentVector(db.Model):
    __tablename__ = "document_vectors"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    document_id = db.Column(
        db.String(36), db.ForeignKey("documents.id"), nullable=False, index=True
    )
    vector_id = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f"<DocumentVector {self.vector_id}>"


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    user_id = db.Column(
        db.String(36), db.ForeignKey("users.id"), nullable=False, index=True
    )
    role = db.Column(db.String(10), nullable=False)  # user or assistant
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self):
        return f"<Message {self.role} {self.id}>"

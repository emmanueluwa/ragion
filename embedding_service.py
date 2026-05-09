"""
loads huggingface model once on startup
expose /embed endpoint for celery workers to call
"""

from flask import Flask, jsonify, request
from langchain_huggingface import HuggingFaceEmbeddings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

logger.info("loading huggingface embedding model...")
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
logger.info("embedding model loaded and ready")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/embed")
def embed():
    data = request.get_json()
    texts = data.get("texts", [])
    vectors = embeddings.embed_documents(texts)

    return jsonify({"embeddings": vectors})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)

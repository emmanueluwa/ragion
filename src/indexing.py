"""
index document into pinecone under the users namespace
process pdf in page batches
calls embedding service instead of loading model inline
"""

import os
import time
import hashlib
import requests
import logging

from langchain_community.document_loaders import PyPDFLoader
from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from pinecone.grpc import PineconeGRPC as Pinecone
from pinecone import ServerlessSpec
from pinecone.exceptions import PineconeApiException
import google.generativeai as genai
import pytesseract
from pdf2image import convert_from_path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
EMBEDDING_SERVICE_URL = os.environ.get("EMBEDDING_SERVICE_URL", "http://embedding:8001")

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
genai.configure(api_key=GOOGLE_API_KEY)
pc = Pinecone(api_key=PINECONE_API_KEY)


def get_embeddings(texts):
    """
    calling embedding service where model lives
    """
    response = requests.post(
        f"{EMBEDDING_SERVICE_URL}/embed", json={"texts": texts}, timeout=120
    )

    response.raise_for_status()

    return response.json()["embeddings"]


def detect_jurisdiction(extracted_data):
    """extract jurisdiction from first 3 pages using llm"""
    first_pages_text = "".join([doc.page_content for doc in extracted_data[:3]])

    model = genai.GenerativeModel(model_name="gemini-3.1-pro-preview")

    prompt = f"""
    Analyze this text from the first pages of a document:
    
    "{first_pages_text[:3000]}"
    
    Your task: Identify the jurisdiction, county, or local authority this document belongs to.
    
    Rules:
    - Return ONLY the jurisdiction name in lowercase, nothing else.
    - Examples: "manatee", "orange", "surrey", "london borough of southwark"
    - If you cannot determine the jurisdiction with confidence, return exactly: unknown
    """

    try:
        response = model.generate_content(prompt)

        result = response.text.strip().lower()
        if result == "unknown" or len(result) > 50:
            return None

        return result

    except Exception as e:
        logger.error(f"jurisdiction detection failed: {e}")

        return None


def make_vector_id(user_id, source, chunk_index):
    """generate deterministic vecotr id from user, source and chunk index"""
    raw = f"{user_id}::{source}::{chunk_index}"

    return hashlib.sha256(raw.encode()).hexdigest()[:48]


# TODO: make dynamic, set by document county given by user
def index_document(
    file_path,
    county,
    description,
    user_id,
    index_name="ragion",
    progress_callback=None,
    page_batch_size=20,
):
    """
    index document into pinecone using streaming page batches
    never holds entire document in memory
    calls embedding service for vectors instead of loading model inline
    """
    namespace = f"user_{user_id}"
    source_name = os.path.basename(file_path)
    abs_file_path = os.path.abspath(file_path)

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=600)

    if progress_callback:
        progress_callback(10, "Loading PDF")

    loader = PyPDFLoader(abs_file_path)
    all_pages = loader.load()
    total_pages = len(all_pages)

    logger.info(
        f"PDF has {total_pages} pages, processing in batches of {page_batch_size}"
    )

    if progress_callback:
        progress_callback(20, "Detecting jurisdiction")

    detected = detect_jurisdiction(all_pages[:3])
    jurisdiction = detected or county or ""

    logger.info(
        f"jurisdiction detected: {detected}, user input: {county}, using: {jurisdiction}"
    )

    # ocr for scanned pages
    ocr_needed = [
        i for i, p in enumerate(all_pages) if len(p.page_content.strip()) < 50
    ]

    if ocr_needed:
        logger.info(f"running ocr on {len(ocr_needed)} scanned pages")

        images = convert_from_path(abs_file_path)

        for page_num in ocr_needed:
            try:
                text = pytesseract.image_to_string(images[page_num])
                if text.strip():
                    all_pages[page_num] = Document(
                        page_content=text,
                        metadata={
                            "page": page_num,
                            "page_label": str(page_num + 1),
                            "source": source_name,
                        },
                    )
            except Exception as e:
                logger.warning(f"OCR failed for page {page_num}: {e}")

        del images

    if progress_callback:
        progress_callback(30, "Preparing index")

    try:
        pc.create_index(
            name=index_name,
            dimension=384,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

        while not pc.describe_index(index_name).status["ready"]:
            time.sleep(5)

    except PineconeApiException as e:
        if "ALREADY_EXISTS" not in str(e):
            raise

    index = pc.Index(index_name)

    # stream through pages in batches
    vector_ids = []
    global_chunk_index = 0
    pages_processed = 0

    for batch_start in range(0, total_pages, page_batch_size):
        batch_pages = all_pages[batch_start : batch_start + page_batch_size]

        valid_pages = [p for p in batch_pages if len(p.page_content.strip()) > 10]
        if not valid_pages:
            pages_processed += len(batch_pages)
            continue

        chunks = text_splitter.split_documents(valid_pages)
        if not chunks:
            pages_processed += len(batch_pages)
            continue

        # adding metadata
        for chunk in chunks:

            # prepend county to improve semantic search
            chunk.page_content = (
                f"Jurisdiction: {jurisdiction}."
                f"Page: {chunk.metadata.get('page_label', 'unknown')}. "
                f"{chunk.page_content}"
            )

            chunk.metadata.update(
                {
                    "jurisdiction": jurisdiction,
                    "document": description,
                    "page": chunk.metadata.get("page", "unknown"),
                    "source": source_name,
                    "user_id": user_id,
                    "chunk_index": global_chunk_index,
                }
            )
            global_chunk_index += 1

        # embed via embedding service
        texts = [chunk.page_content for chunk in chunks]
        vectors = get_embeddings(texts)

        upsert_batch = []

        for chunk, vector in zip(chunks, vectors):
            chunk_idx = chunk.metadata["chunk_index"]
            vector_id = make_vector_id(user_id, source_name, chunk_idx)
            vector_ids.append(vector_id)
            upsert_batch.append(
                {
                    "id": vector_id,
                    "values": vector,
                    "metadata": {**chunk.metadata, "text": chunk.page_content},
                }
            )

        for i in range(0, len(upsert_batch), 100):
            index.upsert(vectors=upsert_batch[i : i + 100], namespace=namespace)

        # discard batch from memory immediately
        del chunks, texts, vectors, upsert_batch, valid_pages, batch_pages

        pages_processed += page_batch_size

        progress = 30 + int((pages_processed / total_pages) * 65)
        if progress_callback:
            progress_callback(
                min(progress, 95), f"indexing page {pages_processed}/{total_pages}"
            )

        logger.info(
            f"processed pages {batch_start}-{min(batch_start+page_batch_size, total_pages)}/{total_pages}"
        )

    del all_pages

    if progress_callback:
        progress_callback(100, "Indexing complete")

    logger.info(f"Indexed {global_chunk_index} chunks, {len(vector_ids)} vectors")

    return jurisdiction, vector_ids

"""
index document into pinecone under the users namespace
"""

from src.helper import load_pdf_file, download_hugging_face_embeddings
from pinecone.grpc import PineconeGRPC as Pinecone
from pinecone import ServerlessSpec
from pinecone.exceptions import PineconeApiException
from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
import google.generativeai as genai
import os
import time
import hashlib


from dotenv import load_dotenv

load_dotenv()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY

genai.configure(api_key=GOOGLE_API_KEY)
pc = Pinecone(api_key=PINECONE_API_KEY)


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

    except Exception:
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
):
    namespace = f"user_{user_id}"
    source_name = os.path.basename(file_path)

    if progress_callback:
        progress_callback(10, "Loading PDF")

    abs_file_path = os.path.abspath(file_path)
    extracted_data = load_pdf_file(data=abs_file_path)

    if progress_callback:
        progress_callback(20, "Detecting jurisdiction")

    detected = detect_jurisdiction(extracted_data)
    jurisdiction = detected or county or ""

    print(
        f"jurisdiction detected: {detected}, user input: {county}, using: {jurisdiction}"
    )

    if progress_callback:
        progress_callback(30, "Splitting document")
    """
    increase chunk overlap and adjust chunk size to improve performance

    chunk_size = 800-1200 for technical docs
    """
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=600)
    text_chunks = text_splitter.split_documents(extracted_data)

    if progress_callback:
        progress_callback(50, "Adding metadata")

    for i, chunk in enumerate(text_chunks):
        # prepend county to improve semantic search
        chunk.page_content = f"Jurisdiction: {jurisdiction}. Page: {chunk.metadata.get('page_label', 'unknown')}. {chunk.page_content}"

        chunk.metadata.update(
            {
                "jurisdiction": jurisdiction,
                "document": description,
                "page": chunk.metadata.get("page", "unknown"),
                "source": source_name,
                "user_id": user_id,
                "chunk_index": i,
            }
        )

    if progress_callback:
        progress_callback(70, "Preparing pinecone index")

    embeddings = download_hugging_face_embeddings()

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

    if progress_callback:
        progress_callback(80, "Generating embeddings")

    index = pc.Index(index_name)
    texts = [chunk.page_content for chunk in text_chunks]
    vectors_data = embeddings.embed_documents(texts)

    if progress_callback:
        progress_callback(90, "Upserting to pinecone")

    vector_ids = []
    upsert_vectors = []
    batch_size = 100

    for i, (chunk, vector) in enumerate(zip(text_chunks, vectors_data)):
        vector_id = make_vector_id(user_id, source_name, i)
        vector_ids.append(vector_id)
        upsert_vectors.append(
            {
                "id": vector_id,
                "values": vector,
                "metadata": {**chunk.metadata, "text": chunk.page_content},
            }
        )

    for i in range(0, len(upsert_vectors), batch_size):
        batch = upsert_vectors[i : i + batch_size]
        index.upsert(vectors=batch, namespace=namespace)

    if progress_callback:
        progress_callback(100, "Indexing complete")

    return jurisdiction, vector_ids

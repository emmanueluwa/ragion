import psycopg2
import redis
import os
import logging
import tempfile
import requests

from celery_config import celery_app
from celery.utils.log import get_task_logger
from src.helper import download_hugging_face_embeddings
from langchain_pinecone import PineconeVectorStore
from langchain_google_genai import GoogleGenerativeAI
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
import google.generativeai as genai
import boto3
from botocore.client import Config
from pinecone.grpc import PineconeGRPC as Pinecone

from src.prompt import *
from src.indexing import index_document


load_dotenv()

# debugging
logger = get_task_logger(__name__)
logger.setLevel(logging.INFO)
handler = logging.FileHandler("my_log.log")
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
genai.configure(api_key=GOOGLE_API_KEY)

# redis connection for progress tracking
redis_endpoint = os.environ.get("REDIS_ENDPOINT", "172.18.0.1")
redis_port = os.environ.get("REDIS_PORT", "6379")
redis_password = os.environ.get("REDIS_PASSWORD", "")

if redis_password:
    redis_url = f"redis://default:{redis_password}@{redis_endpoint}:{redis_port}/0"
else:
    redis_url = f"redis://{redis_endpoint}:{redis_port}/0"


r = redis.Redis.from_url(
    redis_url,
    decode_responses=True,
)

# s3 client pointing to hetzner object storage
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
    aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
    aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
    config=Config(signature_version="s3v4"),
)


def get_db_conn():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))


def send_telegram(message):
    """send alert to telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=5,
        )
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")


def get_rag_chain(user_id):
    embeddings = download_hugging_face_embeddings()
    namespace = f"user_{user_id}"

    index_name = "ragion"

    # loading existing index
    docsearch = PineconeVectorStore.from_existing_index(
        index_name=index_name, embedding=embeddings, namespace=namespace
    )

    # By default, similarity search ignores metadata unless you explicitly filter or boost based on it.
    retriever = docsearch.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 15},
    )

    llm = GoogleGenerativeAI(
        model="gemini-3.1-pro-preview", google_api_key=GOOGLE_API_KEY
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
        ]
    )

    question_answer_chain = create_stuff_documents_chain(llm, prompt)

    return create_retrieval_chain(retriever, question_answer_chain)


@celery_app.task(name="tasks.llm_get_state")
def llm_get_state(msg):
    model = genai.GenerativeModel(model_name="gemini-3.1-pro-preview")

    chat_session = model.start_chat()

    try:
        initial_prompt = f"""
Analyze this question: "{msg}"
 
Your task: Identify if a specific county, jurisdiction or local authority is explicitly mentioned.
 
Rules:
- Only return a jurisdiction if it is directly and clearly mentioned.
- Do NOT infer or guess.
- If nothing is mentioned return exactly: None
 
Return format:
- Return ONLY the jurisdiction name in lowercase with no extra words.
- Examples: "manatee", "surrey"
- If nothing is mentioned: None
"""

        response = chat_session.send_message(initial_prompt)

        return response.text.strip()

    except Exception as e:
        print(f"Error in llm_get_state: {e}")
        return "None"


@celery_app.task(name="tasks.llm_call")
def llm_call(msg, county, user_id):
    """
    Main RAG chain call for answering user questions
    """
    rag_chain = get_rag_chain(user_id)
    try:
        if county and county.strip().lower() != "none":
            query = f"{msg} in {county}"
        else:
            query = msg

        response = rag_chain.invoke({"input": query})

        return str(response["answer"])

    except Exception as e:
        return (
            f"Sorry, an error was encountered when processing your question: {str(e)}"
        )


@celery_app.task(bind=True)
def process_file(self, s3_key, file_id, county, description, user_id):
    def progress_callback(percent, status):
        r.hset(self.request.id, mapping={"percent": percent, "status": status})
        r.expire(self.request.id, 3600)  # 1hr

    # download file from object storage to temp file
    tmp_path = None

    try:
        progress_callback(10, "Downloading file")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            s3.download_fileobj(S3_BUCKET, s3_key, tmp)
            tmp_path = tmp.name

        jurisdiction, vector_ids = index_document(
            tmp_path, county, description, user_id, progress_callback=progress_callback
        )

        conn = get_db_conn()
        try:
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE documents
                SET county = %s, status = %s, indexed_at = NOW()
                WHERE id = %s                
                """,
                (
                    jurisdiction or county,
                    "indexed",
                    file_id,
                ),
            )

            for vector_id in vector_ids:
                cur.execute(
                    """
                    INSERT INTO document_vectors (id, document_id, vector_id)
                    VALUES (gen_random_uuid(), %s, %s)
                    """,
                    (file_id, vector_id),
                )

            conn.commit()

        finally:
            cur.close()
            conn.close()

        progress_callback(100, "Indexing complete")

    except Exception as e:
        progress_callback(100, f"Error: {str(e)}")
        raise

    finally:
        # cleaning up temp file
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@celery_app.task(
    bind=True, name="tasks.delete_document_task", max_retries=5, default_retry_delay=60
)
def delete_document_task(self, document_id, s3_key, namespace, user_email):
    """
    delete pinecone vectors in batches of 100.
    retry up to 5 times with exponential backoff.
    send telegram alert on permanent failure
    """
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT vector_id FROM document_vectors WHERE document_id = %s",
            (document_id,),
        )

        vector_ids = [row[0] for row in cur.fetchall()]

        cur.close()
        conn.close()

        if vector_ids:
            pc = Pinecone(api_key=PINECONE_API_KEY)

            index = pc.Index("ragion")

            batch_size = 100

            for i in range(0, len(vector_ids), batch_size):
                batch = vector_ids[i : i + batch_size]
                index.delete(ids=batch, namespace=namespace)

            s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)

            conn = get_db_conn()

            try:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM document_vectors WHERE document_id = %(doc_id)s",
                    {"doc_id": document_id},
                )
                cur.execute(
                    "DELETE FROM documents WHERE id = %(doc_id)s",
                    {"doc_id": document_id},
                )

                conn.commit()
            finally:
                cur.close()
                conn.close()

            logger.info(f"Document {document_id} deleted successfully")

    except Exception as exc:
        retry_count = self.request.retries
        max_retries = self.max_retries

        if retry_count < max_retries:
            countdown = 60 * (2**retry_count)
            logger.warning(
                f"Delete failed for {document_id}, retry {retry_count + 1}/{max_retries} "
                f"in {countdown}s. Error: {exc}"
            )

            raise self.retry(exc=exc, countdown=countdown)
        else:
            logger.error(
                f"Delete permanently failed for document {document_id} "
                f"after {max_retries} retries. Error: {exc}"
            )

            try:
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE documents SET status = %s WHERE id = %s",
                    (
                        "delete_failed",
                        document_id,
                    ),
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception as db_exc:
                logger.error(f"Failed to mark delete_failed: {db_exc}")

                send_telegram(
                    f"🚨 SwiftCiv Alert\n\n"
                    f"Document deletion permanently failed.\n"
                    f"Document ID: {document_id}\n"
                    f"User: {user_email}\n"
                    f"Error: {exc}\n\n"
                    f"Manual intervention required."
                )


@celery_app.task(name="tasks.cleanup_stuck_deletions")
def cleanup_stuck_deletions():
    """
    scheduled every 30 mins via celery beat.
    finds documents stuck in deleting > 15 mninutes and requeues deletion.
    alerts on delete_failed_documents
    """
    try:
        conn = get_db_conn()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT d.id, d.s3_key, d.pinecone_namespace, u.email
            FROM documents d
            JOIN users u on d.user_id = u.id
            WHERE d.status = 'deleting'
            AND d.indexed_at < NOW() - INTERVAL '15 minutes'
            """
        )
        stuck = cur.fetchall()

        cur.execute(
            """
            SELECT d.id, u.email
            FROM documents d
            JOIN users u ON d.user_id = u.id
            WHERE d.status = 'delete_failed'
            """
        )
        failed = cur.fetchall()

        cur.close()
        conn.close()

        for doc_id, s3_key, namespace, email in stuck:
            logger.info(f"Requeuing stuck deletion for document {doc_id}")
            delete_document_task.delay(doc_id, s3_key, namespace, email)

        if failed:
            failed_list = "\n".join(
                [f"- {doc_id} ({email})" for doc_id, email in failed]
            )

            send_telegram(
                f"⚠️ SwiftCiv Cleanup Report\n\n"
                f"{len(failed)} document(s) in delete_failed state:\n"
                f"{failed_list}\n\n"
                f"Manual intervention required."
            )

        if stuck:
            send_telegram(
                f"ℹ️ SwiftCiv Cleanup\n\n" f"Requeued {len(stuck)} stuck deletion(s)."
            )

    except Exception as e:
        logger.error(f"Cleanup job failed: {e}")
        send_telegram(f"🚨 SwiftCiv Cleanup job failed: {e}")

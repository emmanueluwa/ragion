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
import redis
import boto3
from botocore.client import Config

from src.prompt import *
from src.indexing import index_document

import os
import logging
import tempfile

load_dotenv()
indexing_progress = {}

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

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
client = genai.configure(api_key=GOOGLE_API_KEY)

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


def get_rag_chain(county):
    embeddings = download_hugging_face_embeddings()

    index_name = "ragion"

    # loading existing index
    docsearch = PineconeVectorStore.from_existing_index(
        index_name=index_name,
        embedding=embeddings,
    )

    if county and county.strip().lower() != "none":
        search_kwargs = {"k": 8, "filter": {"jurisdiction": county}}
    else:
        search_kwargs = {"k": 8}

    # By default, similarity search ignores metadata unless you explicitly filter or boost based on it.
    retriever = docsearch.as_retriever(
        search_type="similarity",
        search_kwargs=search_kwargs,
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

        Your task: Identify if a specific county or state is explicitly mentioned in the question.

        Rules:
        - Only return a county or state if it is directly, clearly mentioned in the text (e.g., "Manatee County", "Florida", "Orange County").
        - Do NOT infer or guess based on context, document references, or implied meaning.
        - If there is no explicit mention of a county or state, return exactly: None

        Return format:
        - If a county is mentioned: County, State (e.g., "Manatee County, Florida")
        - If only a state is mentioned: State (e.g., "Florida")
        - If nothing is mentioned: None
        """

        response = chat_session.send_message(initial_prompt)

        return response.text.strip()

    except Exception as e:
        print(f"Error in llm_get_state: {e}")
        return "None"


@celery_app.task(name="tasks.llm_call")
def llm_call(msg, county):
    """
    Main RAG chain call for answering user questions
    """
    rag_chain = get_rag_chain(county)
    try:
        response = rag_chain.invoke({"input": msg})
        answer = str(response["answer"])
        print("llm query task complete :)")
        return answer

    except Exception as e:
        print(f"Error in llm_call: {e}")
        return (
            f"Sorry, an error was encountered when processing your question: {str(e)}"
        )


@celery_app.task(bind=True)
def process_file(self, s3_key, file_id, county, description):
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

        index_document(
            tmp_path, county, description, progress_callback=progress_callback
        )

        progress_callback(100, "Indexing complete")

    except Exception as e:
        progress_callback(100, f"Error: {str(e)}")
        raise

    finally:
        # cleaning up temp file
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

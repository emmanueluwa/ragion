from celery_config import celery_app
from celery.utils.log import get_task_logger
from src.helper import download_hugging_face_embeddings
from langchain_pinecone import PineconeVectorStore
from langchain_google_genai import GoogleGenerativeAI
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from src.prompt import *

import os
import logging

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

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY


embeddings = download_hugging_face_embeddings()

index_name = "ragion"

# loading existing index
docsearch = PineconeVectorStore.from_existing_index(
    index_name=index_name,
    embedding=embeddings,
)

retriever = docsearch.as_retriever(search_type="similarity", search_kwargs={"k": 3})

llm = GoogleGenerativeAI(
    model="models/gemini-1.5-pro-001", google_api_key=GOOGLE_API_KEY
)

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        ("human", "{input}"),
    ]
)

question_answer_chain = create_stuff_documents_chain(llm, prompt)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)


@celery_app.task(name="tasks.llm_call")
def llm_call(msg):
    """
    asyncio task
    return None
    """

    print("starting some work")

    response = rag_chain.invoke({"input": msg})
    print("Response: ", response["answer"])
    print("task complete :)")

    return str(response["answer"])

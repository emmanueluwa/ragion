from celery_config import celery_app
from celery.utils.log import get_task_logger
from src.helper import download_hugging_face_embeddings
from langchain_pinecone import PineconeVectorStore
from langchain_google_genai import GoogleGenerativeAI
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

import google.generativeai as genai
from src.prompt import *
from src.indexing import index_document


import os
import logging

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

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
client = genai.configure(api_key=GOOGLE_API_KEY)


# TODO: Make jurisdiction dynamic
def get_rag_chain():
    embeddings = download_hugging_face_embeddings()

    index_name = "ragion"

    # loading existing index
    docsearch = PineconeVectorStore.from_existing_index(
        index_name=index_name,
        embedding=embeddings,
    )

    # By default, similarity search ignores metadata unless you explicitly filter or boost based on it.
    retriever = docsearch.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 5, "filter": {"jurisdiction": "Manatee County, Florida"}},
    )

    llm = GoogleGenerativeAI(
        model="models/gemini-2.0-flash", google_api_key=GOOGLE_API_KEY
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
    """
    Detecting if a U.S state is mentioned in the user's question
    """
    # model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")
    model = genai.GenerativeModel(model_name="models/gemini-pro")
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
        result = response.text.strip()

        print(f"State detection check - Input: '{msg}' -> Output: '{result}'")
        return result

    except Exception as e:
        print(f"Error in llm_get_state: {e}")
        return "None"


@celery_app.task(name="tasks.llm_call")
def llm_call(msg):
    """
    Main RAG chain call for answering user questions
    """
    rag_chain = get_rag_chain()
    try:
        print(f"processing query: {msg}")
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
def process_file(self, file_path, file_id):
    def progress_callback(percent, status):
        indexing_progress[self.request.id] = {"percent": percent, "status": status}

    try:
        index_document(file_path, progress_callback=progress_callback)

    except Exception as e:
        progress_callback(100, f"Error: {str(e)}")
        raise

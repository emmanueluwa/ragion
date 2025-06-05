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

client = genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")
chat_session = model.start_chat()


# loading existing index
docsearch = PineconeVectorStore.from_existing_index(
    index_name=index_name,
    embedding=embeddings,
)

# By default, similarity search ignores metadata unless you explicitly filter or boost based on it.
retriever = docsearch.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 3, "filter": {"jurisdiction": "Manatee County, Florida"}},
)

llm = GoogleGenerativeAI(model="models/gemini-2.0-flash", google_api_key=GOOGLE_API_KEY)

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        ("human", "{input}"),
    ]
)

question_answer_chain = create_stuff_documents_chain(llm, prompt)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)


@celery_app.task(name="tasks.llm_get_state")
def llm_get_state(msg):
    """
    Detecting if a U.S state is mentioned in the user's question
    """
    try:
        initial_prompt = f"""
        Analyze this question: "{msg}"

        Identify the relevant jurisdiction (state and/or county) for regulatory compliance based on:
        1. Explicit mentions of Florida counties (especially Manatee County)
        2. References to Florida Land Development Codes (LDCs), ordinances, or Manatee County forms/manuals
        3. Implied jurisdiction through document context

        Detection criteria:
        - Look for "Manatee County" or "Florida" specifically
        - Check for Florida-specific terms: "Chapter 163", "LDC", "Florida Statutes", "Fla. Stat"
        - Identify Manatee County references: "DEV REVIEW PROCEDURES MANUAL", "Public Works Standards Manual", "Form D1-D8"
        - Recognize Florida county abbreviations: FL, Manatee Co.
        - Note general terms like "county regulations" or "local ordinances" when paired with document context

        Response rules:
        1. If Manatee County is mentioned or implied by document references, return "Manatee County, Florida"
        2. If only Florida is mentioned, return "Florida"
        3. If no location specified but question references LDCs/ordinances/manuals from context, default to "Manatee County, Florida"
        4. For other states, return "State: [Full State Name]"
        5. If truly no geographic context, return "None"

        Return ONLY the jurisdiction in this format:
        - County, State (when county specified)
        - State only (when state specified)
        - "None" (no geographic context)
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

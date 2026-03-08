"""
this file should be ran once to create index for query operation

execute once unless data source is updated
"""

from src.helper import load_pdf_file, text_split, download_hugging_face_embeddings
from pinecone.grpc import PineconeGRPC as Pinecone
from pinecone import ServerlessSpec
from pinecone.exceptions import PineconeApiException
from langchain_pinecone import PineconeVectorStore
from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
import os
import time


from dotenv import load_dotenv

load_dotenv()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY

pc = Pinecone(api_key=PINECONE_API_KEY)


# TODO: make dynamic, set by document county given by user
def index_document(
    file_path,
    county,
    description,
    index_name="ragion",
    progress_callback=None,
):
    if progress_callback:
        progress_callback(10, "Loading PDF")

    abs_file_path = os.path.abspath(file_path)
    extracted_data = load_pdf_file(data=abs_file_path)

    if progress_callback:
        progress_callback(30, "Splitting document")
    """
    increase chunk overlap and adjust chunk size to improve performance

    chunk_size = 800-1200 for technical docs
    """
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=400)
    text_chunks = text_splitter.split_documents(extracted_data)

    if progress_callback:
        progress_callback(50, "Adding metadata")

    for chunk in text_chunks:
        # prepend county to improve sementic search
        chunk.page_content = f"Jurisdiction: {county}. {chunk.page_content}"

        chunk.metadata.update(
            {
                "jurisdiction": county,
                "document": description,
                "page": chunk.metadata.get("page", "unknown"),
                "source": os.path.basename(file_path),
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
        progress_callback(90, "Upserting to pinecone")
    # embedding each chunk and upsert the embeddings into pinecone index
    PineconeVectorStore.from_documents(
        documents=text_chunks, index_name=index_name, embedding=embeddings
    )

    if progress_callback:
        progress_callback(100, "Indexing complete")
    return True

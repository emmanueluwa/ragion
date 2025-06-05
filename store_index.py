"""
this file should be ran once to create index for query operation

execute once unless data source is updated
"""

from src.helper import load_pdf_file, text_split, download_hugging_face_embeddings
from pinecone.grpc import PineconeGRPC as Pinecone
from pinecone import ServerlessSpec
from langchain_pinecone import PineconeVectorStore
from langchain.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
import os


from dotenv import load_dotenv

load_dotenv()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY


extracted_data = load_pdf_file(data="data/")
# text_chunks = text_split(extracted_data)

"""
increase chunk overlap and adjust chunk size to improve performance

chunk_size = 800-1200 for technical docs
"""
text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=200)
text_chunks = text_splitter.split_documents(extracted_data)

# TODO: make dynamic, set by document
county = "Manatee County, Florida"

for chunk in text_chunks:
    # prepend county to chunk text
    chunk.page_content = f"Jurisdiction: {county}. {chunk.page_content}"

    chunk.metadata.update(
        {
            "jurisdiction": "Manatee County, Florida",
            "document": "Storm Water Design Procedure Manual",
        }
    )

embeddings = download_hugging_face_embeddings()

pc = Pinecone(api_key=PINECONE_API_KEY)

index_name = "ragion"

pc.create_index(
    name=index_name,
    dimension=384,
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
)

# embedding each chunk and upsert the embeddings into pinecone index
docsearch = PineconeVectorStore.from_documents(
    documents=text_chunks, index_name=index_name, embedding=embeddings
)

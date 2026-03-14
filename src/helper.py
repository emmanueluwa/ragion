from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
import pytesseract
from pdf2image import convert_from_path
import os


def load_pdf_file(data):
    if data.endswith(".pdf"):
        loader = PyPDFLoader(data)
    else:
        loader = DirectoryLoader(data, glob="*.pdf", loader_cls=PyPDFLoader)

    documents = loader.load()

    # finding pages w/ little or no text, likely scanned images
    enhanced_documents = []
    ocr_needed_pages = []

    for doc in documents:
        if len(doc.page_content.strip()) < 50:
            ocr_needed_pages.append(doc.metadata.get("page", 0))
        else:
            enhanced_documents.append(doc)

    # run ocr on scanned pages
    if ocr_needed_pages:
        images = convert_from_path(data)

        for page_num in ocr_needed_pages:
            try:
                image = images[page_num]
                text = pytesseract.image_to_string(image)

                if text.strip():
                    enhanced_documents.append(
                        Document(
                            page_content=text,
                            metadata={
                                "page": page_num,
                                "page_label": str(page_num + 1),
                                "source": os.path.basename(data),
                            },
                        )
                    )

            except Exception as e:
                print(f"OCR failed for page {page_num}: {e}")

    return enhanced_documents


# text_split function splits the data into text chunks
def text_split(extracted_data):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=600)
    text_chunks = text_splitter.split_documents(extracted_data)

    return text_chunks


def download_hugging_face_embeddings():
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    return embeddings

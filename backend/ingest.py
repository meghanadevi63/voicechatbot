"""Load PDFs from the docs folder, split them, and store embeddings in Milvus Lite.

Run standalone (only while the backend is stopped — Milvus Lite allows one process):
    python -m backend.ingest
"""

import re
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.config import get_settings
from backend.vectorstore import get_vectorstore


def load_documents(docs_dir: str) -> list[Document]:
    docs: list[Document] = []
    for pdf in sorted(Path(docs_dir).glob("*.pdf")):
        for page in PyPDFLoader(str(pdf)).load():
            # PDF extraction often uses tabs/odd spacing between words
            page.page_content = re.sub(r"[ \t]+", " ", page.page_content).strip()
            if not page.page_content:
                continue
            # Keep only simple metadata fields; Milvus needs a consistent schema
            page.metadata = {
                "source": pdf.name,
                "page": int(page.metadata.get("page", 0)) + 1,
            }
            docs.append(page)
    return docs


def ingest() -> dict:
    settings = get_settings()
    pages = load_documents(settings.docs_dir)
    if not pages:
        raise ValueError(f"No PDF content found in {settings.docs_dir}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = splitter.split_documents(pages)

    vectorstore = get_vectorstore(drop_old=True)
    vectorstore.add_documents(chunks)

    return {
        "files": sorted({p.metadata["source"] for p in pages}),
        "pages": len(pages),
        "chunks": len(chunks),
    }


if __name__ == "__main__":
    print(ingest())

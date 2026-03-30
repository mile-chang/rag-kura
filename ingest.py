"""Knowledge ingestion pipeline for the RAG Knowledge Assistant.

Reads Markdown documents from the ``docs/`` directory, splits them into
chunks, generates embeddings via a local HuggingFace model (CPU-only),
and persists the vectors into a ChromaDB collection stored at ``./chroma_db``.

Usage::

    # Make sure the virtual environment is activated first
    python ingest.py
"""

from pathlib import Path

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOCS_DIR = Path(__file__).parent / "docs"
CHROMA_DIR = Path(__file__).parent / "chroma_db"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def ingest() -> None:
    """Execute the full ingestion pipeline."""

    # 1. Load documents
    print(f"[1/4] Loading Markdown files from: {DOCS_DIR}")

    loader = DirectoryLoader(
        str(DOCS_DIR),
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
        show_progress=True,
    )
    raw_docs = loader.load()
    print(f"      Loaded {len(raw_docs)} document(s)")

    if not raw_docs:
        print("WARNING: No documents found. Place .md files in the docs/ directory.")
        return

    # 2. Split into chunks
    print(f"[2/4] Splitting documents (chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
    )
    chunks = splitter.split_documents(raw_docs)
    print(f"      Generated {len(chunks)} chunk(s)")

    # 3. Initialise embedding model (CPU only)
    print(f"[3/4] Loading embedding model: {EMBEDDING_MODEL} (device=cpu)")

    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # 4. Persist into ChromaDB
    print(f"[4/4] Storing vectors in: {CHROMA_DIR}")

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DIR),
        collection_name="rag_knowledge",
    )

    # Summary
    count = vectorstore._collection.count()
    print()
    print("=" * 60)
    print(f"Ingestion complete: {count} vector(s) stored.")
    print(f"  ChromaDB path : {CHROMA_DIR}")
    print(f"  Embedding model: {EMBEDDING_MODEL}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ingest()

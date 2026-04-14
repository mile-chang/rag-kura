"""Knowledge base routes — file upload and ingestion."""

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from langchain_community.vectorstores import Chroma

import config

router = APIRouter(prefix="/api", tags=["knowledge"])

# Root of the project (one level above this file's api/ directory)
_PROJECT_ROOT = Path(__file__).parent.parent
_DOCS_DIR = _PROJECT_ROOT / "docs"


@router.post("/upload")
async def api_upload_files(files: list[UploadFile] = File(...)):
    """Upload .md or .pdf files to the knowledge base and trigger re-ingestion.

    Files are sanitised (directory components stripped to prevent path
    traversal) then saved to docs/.  The ingest.py script rebuilds the
    ChromaDB vector store; the in-memory reference is hot-reloaded so
    subsequent queries immediately reflect the new documents.
    """
    allowed = {".md", ".pdf"}
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        # Strip directory separators to neutralise path-traversal payloads
        safe_name = Path(f.filename).name
        ext = Path(safe_name).suffix.lower()
        if ext not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {ext}. Allowed: {allowed}",
            )
        dest = _DOCS_DIR / safe_name
        content = await f.read()
        dest.write_bytes(content)
        saved.append(safe_name)

    # Re-run ingestion in the same Python environment as the server process.
    # cwd is the project root so ingest.py can be found correctly.
    proc = subprocess.run(
        [sys.executable, "ingest.py"],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {proc.stderr}",
        )

    # Hot-reload: reassign config._vectorstore so all modules pick up the
    # new data via their ``import config; config._vectorstore`` access pattern.
    config._vectorstore = Chroma(
        persist_directory=str(config.CHROMA_DIR),
        embedding_function=config._embeddings,
        collection_name="rag_knowledge",
    )

    return {"files": saved, "message": f"{len(saved)} file(s) ingested successfully."}

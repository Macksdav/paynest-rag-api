"""
FastAPI service around the RAG pipeline.

Run locally:   uvicorn app.main:app --reload
Interactive docs: http://localhost:8000/docs
"""

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.rag import RagService, build_rag_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("paynest")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the vector store once at startup, not on every request.
    logger.info("Building RAG service...")
    app.state.rag = await asyncio.to_thread(build_rag_service)
    logger.info("RAG ready: mode=%s chunks=%d", app.state.rag.mode, app.state.rag.chunk_count)
    yield
    logger.info("Shutting down")


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="PayNest Support Assistant",
    description="RAG-powered support assistant (LangChain + FAISS + Ollama).",
    version="1.0.0",
    lifespan=lifespan,
)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    """Serve the chat UI."""
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------
# Schemas
# ---------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500, examples=["How long do instant transfers take?"])


class AskResponse(BaseModel):
    answer: str
    sources: list[str]
    mode: str
    latency_ms: float


# ---------------------------------------------------------
# Security: optional API key. If API_KEY is set, /ask requires it.
# ---------------------------------------------------------
def require_api_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("API_KEY")
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------
# Observability: log every request with its duration
# ---------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info("%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


# ---------------------------------------------------------
# Endpoints
# ---------------------------------------------------------
@app.get("/health")
async def health(request: Request):
    """Liveness check used by Docker and load balancers."""
    rag: RagService = request.app.state.rag
    return {"status": "ok", "mode": rag.mode, "chunks": rag.chunk_count}


@app.post("/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
async def ask(body: AskRequest, request: Request):
    rag: RagService = request.app.state.rag
    start = time.perf_counter()
    try:
        result = await rag.ask(body.question)
    except Exception:
        logger.exception("RAG pipeline failed")
        raise HTTPException(status_code=503, detail="The assistant is temporarily unavailable")
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    return AskResponse(answer=result["answer"], sources=result["sources"], mode=rag.mode, latency_ms=latency_ms)

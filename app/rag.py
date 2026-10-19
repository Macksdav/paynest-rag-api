"""
Core RAG pipeline (LangChain).

Built ONCE at API startup and reused by every request. Rebuilding the
vector store on each request would be slow and wasteful.

LLM_MODE=ollama   real models through Ollama (production)
LLM_MODE=offline  word-count embeddings + a quote-the-best-fact stand-in for
                  the LLM. Used by the automated tests and CI so they need
                  no GPU, no model download and no network.
"""

import math
import os
import re
import warnings
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path

warnings.filterwarnings("ignore")

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel, RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter

FALLBACK = "I can't find that in our help centre."
DEFAULT_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.txt"

PROMPT_TEMPLATE = f"""You are a PayNest customer support assistant.
Answer the question using ONLY the context below.
If the answer is not in the context, say: "{FALLBACK}"

Context:
{{context}}

Question:
{{question}}

Answer:"""


# ---------------------------------------------------------
# Offline stand-ins (tests / CI only)
# ---------------------------------------------------------
def _tokenize(text):
    return re.findall(r"[a-z]+", text.lower())


class WordCountEmbeddings(Embeddings):
    """Normalised word-count vectors. Implements the same interface as OllamaEmbeddings."""

    def __init__(self, texts):
        self.vocab = sorted({w for t in texts for w in _tokenize(t)})

    def _embed(self, text):
        words = _tokenize(text)
        vector = [float(words.count(w)) for w in self.vocab]
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm else vector

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


_STOPWORDS = {"what", "when", "where", "which", "does", "this", "that", "with",
              "from", "have", "many", "much", "long", "about", "your", "there",
              "need", "needed", "take", "takes"}


def _content_words(text):
    return {w.rstrip("s") for w in _tokenize(text) if len(w) > 3 and w not in _STOPWORDS}


def _offline_llm(prompt_value):
    """Quote the context line that shares most words with the question."""
    text = prompt_value.to_string()
    context = text.split("Context:")[1].split("Question:")[0]
    question = text.split("Question:")[1].split("Answer:")[0]
    q_words = _content_words(question)
    best, best_score = None, 0
    for line in context.strip().split("\n"):
        score = len(q_words & _content_words(line))
        if score > best_score:
            best, best_score = line.strip(), score
    return best if best_score >= 2 else FALLBACK


# ---------------------------------------------------------
# Service
# ---------------------------------------------------------
def _format_docs(docs):
    return "\n".join(doc.page_content for doc in docs)


@dataclass
class RagService:
    chain: object
    mode: str
    chunk_count: int

    async def ask(self, question: str) -> dict:
        """Returns {"answer": str, "sources": [str, ...]}. Async so the server
        stays responsive while the LLM is thinking."""
        result = await self.chain.ainvoke(question)
        sources = list(dict.fromkeys(doc.page_content for doc in result["docs"]))
        return {"answer": result["answer"].strip(), "sources": sources}


def load_knowledge_base(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def build_rag_service() -> RagService:
    mode = os.getenv("LLM_MODE", "ollama").lower()
    data_file = Path(os.getenv("KNOWLEDGE_BASE_FILE", DEFAULT_DATA_FILE))

    texts = load_knowledge_base(data_file)
    documents = [
        Document(page_content=t, metadata={"source": data_file.name, "id": i})
        for i, t in enumerate(texts)
    ]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(os.getenv("CHUNK_SIZE", "300")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "30")),
    )
    chunks = splitter.split_documents(documents)

    if mode == "offline":
        embeddings = WordCountEmbeddings(texts)
        llm = RunnableLambda(_offline_llm)
    elif mode == "ollama":
        from langchain_ollama import ChatOllama, OllamaEmbeddings

        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        embeddings = OllamaEmbeddings(
            model=os.getenv("EMBED_MODEL", "nomic-embed-text"), base_url=base_url
        )
        llm = ChatOllama(
            model=os.getenv("CHAT_MODEL", "llama3.2:3b"), base_url=base_url, temperature=0
        )
    else:
        raise ValueError(f"LLM_MODE must be 'ollama' or 'offline', got '{mode}'")

    store = FAISS.from_documents(chunks, embeddings)
    retriever = store.as_retriever(search_kwargs={"k": int(os.getenv("TOP_K", "3"))})

    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    answer_chain = (
        {"context": lambda x: _format_docs(x["docs"]), "question": itemgetter("question")}
        | prompt
        | llm
        | StrOutputParser()
    )
    # Retrieve once, then reuse the same documents for the answer AND the sources.
    chain = RunnableParallel(docs=retriever, question=RunnablePassthrough()) | RunnableParallel(
        answer=answer_chain, docs=itemgetter("docs")
    )
    return RagService(chain=chain, mode=mode, chunk_count=len(chunks))

import hashlib
import re
from functools import lru_cache

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from database import DATABASE_PATH


@lru_cache(maxsize=1)
def context_collection():
    client = chromadb.PersistentClient(
        path=str(DATABASE_PATH.parent / "interview-vectors"), settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection("interview_context", embedding_function=DefaultEmbeddingFunction())


def _chunks(text, size=420):
    paragraphs = re.split(r"\n\s*\n", text)
    result = []
    for paragraph in paragraphs:
        words = paragraph.split()
        chunk = ""
        for word in words:
            if len(chunk) + len(word) + 1 > size and chunk:
                result.append(chunk)
                chunk = " ".join(chunk.split()[-12:])
            chunk = (chunk + " " + word).strip()
        if chunk:
            result.append(chunk)
    return result


def index_context(session_id, resume, jd):
    fingerprint = hashlib.sha256((resume + "\0" + jd).encode()).hexdigest()
    collection = context_collection()
    where = {"$and": [{"session_id": session_id}, {"fingerprint": fingerprint}]}
    if collection.get(where=where, limit=1)["ids"]:
        return where
    documents, metadata, ids = [], [], []
    for source, text, prefix in [("resume", resume, "R"), ("jd", jd, "J")]:
        for number, chunk in enumerate(_chunks(text), 1):
            source_id = f"{prefix}{number}"
            ids.append(f"{session_id}:{fingerprint}:{source_id}")
            documents.append(chunk)
            metadata.append({"session_id": session_id, "fingerprint": fingerprint, "source": source, "source_id": source_id})
    collection.upsert(ids=ids, documents=documents, metadatas=metadata)
    return where


def retrieve_context(session_id, resume, jd, query, count=4):
    where = index_context(session_id, resume, jd)
    collection = context_collection()
    passages = []
    # Reserve space for both documents so resume similarity cannot hide the JD.
    for source in ["jd", "resume"]:
        filtered = {"$and": [*where["$and"], {"source": source}]}
        available = len(collection.get(where=filtered)["ids"])
        if not available:
            continue
        result = collection.query(query_texts=[query[:1800]], where=filtered, n_results=min(max(1, count // 2), available))
        for text, meta in zip(result["documents"][0], result["metadatas"][0]):
            passages.append({"id": meta["source_id"], "source": source, "text": text})
    return passages

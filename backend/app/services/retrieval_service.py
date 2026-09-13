from __future__ import annotations

import math
import re
from collections import Counter

from sqlalchemy import select

from ..core.db import Chunk, Paper, SessionLocal


STOP = {"the","a","an","of","to","and","in","for","on","with","is","are","this","that","we","our","by","as","be"}


def tokens(text: str) -> list[str]:
    return [x for x in re.findall(r"[A-Za-z][A-Za-z0-9_\-]+|[\u4e00-\u9fff]{1,4}|\d+(?:\.\d+)?", text.lower()) if x not in STOP]


def _bm25(query: str, docs: list[str]) -> list[float]:
    q = tokens(query)
    tok_docs = [tokens(d) for d in docs]
    n = len(tok_docs)
    if not n:
        return []
    avgdl = sum(map(len, tok_docs)) / n or 1
    df = Counter()
    for d in tok_docs:
        df.update(set(d))
    scores = []
    k1, b = 1.5, 0.75
    for d in tok_docs:
        tf = Counter(d)
        s = 0.0
        for term in q:
            f = tf[term]
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avgdl))
        scores.append(s)
    return scores


def retrieve(project_id: str, query: str, paper_ids: list[str] | None = None, top_k: int = 8) -> list[dict]:
    with SessionLocal() as db:
        stmt = select(Chunk, Paper).join(Paper, Paper.id == Chunk.paper_id).where(Paper.project_id == project_id)
        if paper_ids:
            stmt = stmt.where(Paper.id.in_(paper_ids))
        rows = db.execute(stmt).all()
    docs = [c.content for c, _ in rows]
    scores = _bm25(query, docs)
    ranked = sorted(zip(rows, scores), key=lambda x: x[1], reverse=True)[:top_k]
    out = []
    for ((c, p), score) in ranked:
        out.append({
            "chunk_id": c.id, "paper_id": p.id, "paper_title": p.title, "page": c.page,
            "section": c.section, "content": c.content, "score": round(float(score), 5),
        })
    return out

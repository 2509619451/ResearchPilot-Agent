from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Iterable

import fitz
from sqlalchemy import select

from ..core.config import settings
from ..core.db import Chunk, Paper, PaperProfile, SessionLocal
from ..core.llm import llm


SECTION_PATTERNS = [
    ("Abstract", r"^\s*abstract\s*$"),
    ("Introduction", r"^\s*(\d+\.?\s*)?introduction\s*$"),
    ("Related Work", r"^\s*(\d+\.?\s*)?(related work|background)\s*$"),
    ("Method", r"^\s*(\d+\.?\s*)?(method|methodology|proposed method|model|approach)\s*$"),
    ("Experiments", r"^\s*(\d+\.?\s*)?(experiment|experiments|experimental setup|evaluation)\s*$"),
    ("Results", r"^\s*(\d+\.?\s*)?(results|discussion|results and discussion)\s*$"),
    ("Conclusion", r"^\s*(\d+\.?\s*)?(conclusion|conclusions|limitations|future work)\s*$"),
]


def _safe_filename(name: str) -> str:
    name = Path(name).name
    return re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name)[:180]


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_section(text: str, previous: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for line in lines[:12]:
        small = line.lower()[:100]
        for section, pat in SECTION_PATTERNS:
            if re.match(pat, small, flags=re.I):
                return section
    return previous


def parse_pdf(path: str) -> list[dict]:
    doc = fitz.open(path)
    pages = []
    section = "Unknown"
    for i, page in enumerate(doc):
        raw = page.get_text("text")
        section = _detect_section(raw, section)
        pages.append({"page": i + 1, "section": section, "text": _clean_text(raw)})
    return pages


def chunk_pages(pages: list[dict], chunk_size: int = 1800, overlap: int = 260) -> list[dict]:
    chunks = []
    for page in pages:
        text = page["text"]
        if not text:
            continue
        start = 0
        idx = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            piece = text[start:end].strip()
            if piece:
                seed = f"{page['page']}|{idx}|{piece[:100]}".encode("utf-8", errors="ignore")
                chunks.append({
                    "id": hashlib.sha1(seed).hexdigest(),
                    "page": page["page"],
                    "section": page["section"],
                    "content": piece,
                })
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
            idx += 1
    return chunks


def save_uploaded_pdf(project_id: str, filename: str, fileobj) -> Paper:
    root = Path(settings.storage_dir) / "papers"
    root.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    target = root / f"{hashlib.sha1((project_id + safe).encode()).hexdigest()[:10]}_{safe}"
    with target.open("wb") as f:
        shutil.copyfileobj(fileobj, f)

    pages = parse_pdf(str(target))
    title = target.stem
    if pages and pages[0]["text"]:
        first_lines = [x.strip() for x in pages[0]["text"].split("\n") if len(x.strip()) > 8]
        if first_lines:
            title = first_lines[0][:300]

    paper = Paper(project_id=project_id, title=title, pdf_path=str(target), source="uploaded", analysis_level="fulltext")
    with SessionLocal() as db:
        db.add(paper)
        db.flush()
        for c in chunk_pages(pages):
            db.add(Chunk(id=f"{paper.id}_{c['id'][:24]}", paper_id=paper.id, page=c["page"], section=c["section"], content=c["content"]))
        db.commit()
        db.refresh(paper)
    return paper


def import_metadata_papers(project_id: str, papers: Iterable[dict]) -> list[Paper]:
    saved = []
    with SessionLocal() as db:
        for item in papers:
            doi = (item.get("doi") or "").strip().lower()
            arxiv_id = (item.get("arxiv_id") or "").strip()
            title = (item.get("title") or "Untitled").strip()
            existing = None
            if doi:
                existing = db.scalar(select(Paper).where(Paper.project_id == project_id, Paper.doi == doi))
            if not existing and arxiv_id:
                existing = db.scalar(select(Paper).where(Paper.project_id == project_id, Paper.arxiv_id == arxiv_id))
            if not existing:
                normalized = re.sub(r"\W+", "", title.lower())
                for p in db.scalars(select(Paper).where(Paper.project_id == project_id)).all():
                    if re.sub(r"\W+", "", p.title.lower()) == normalized:
                        existing = p
                        break
            if existing:
                saved.append(existing)
                continue
            p = Paper(
                project_id=project_id,
                title=title,
                authors_json=json.dumps(item.get("authors") or [], ensure_ascii=False),
                year=item.get("year"),
                abstract=item.get("abstract") or "",
                doi=doi,
                arxiv_id=arxiv_id,
                venue=item.get("venue") or "",
                source=item.get("source") or "search",
                url=item.get("url") or "",
                pdf_url=item.get("pdf_url") or "",
                analysis_level="metadata_only",
                relevance_score=float(item.get("relevance_score") or 0.0),
            )
            db.add(p)
            db.flush()
            if p.abstract:
                db.add(Chunk(id=f"{p.id}_abstract", paper_id=p.id, page=1, section="Abstract", content=p.abstract))
            saved.append(p)
        db.commit()
    return saved


def analyze_paper(paper_id: str, evidence_chunks: list[dict]) -> dict:
    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        if not paper:
            raise ValueError("paper not found")
        context = "\n\n".join(
            f"[chunk_id={c['chunk_id']} page={c['page']} section={c['section']}]\n{c['content']}" for c in evidence_chunks[:14]
        )
        joined = "\n".join(c.get("content", "") for c in evidence_chunks)
        known_methods = ["Transformer", "Informer", "Autoformer", "FEDformer", "PatchTST", "LSTM", "GRU", "CNN", "XGBoost", "Random Forest", "ARIMA", "RAG", "GraphRAG"]
        methods = [m for m in known_methods if re.search(rf"\b{re.escape(m)}\b", joined, flags=re.I)]
        known_metrics = ["MAE", "RMSE", "MAPE", "MSE", "R2", "Accuracy", "Precision", "Recall", "F1", "AUC", "BLEU", "ROUGE"]
        metrics = [m for m in known_metrics if re.search(rf"\b{re.escape(m)}\b", joined, flags=re.I)]
        dataset_hits = re.findall(r"(?:dataset|data set|benchmark)\s*(?:is|:|=|named|called)?\s*([A-Z][A-Za-z0-9_\- ]{2,40})", joined, flags=re.I)
        datasets = []
        for d in dataset_hits:
            d = re.split(r"[.,;:\n]", d)[0].strip()
            if d and d.lower() not in {x.lower() for x in datasets}:
                datasets.append(d)
        baselines = [m for m in ["LSTM", "GRU", "MLP", "XGBoost", "ARIMA", "GA", "PSO"] if re.search(rf"\b{re.escape(m)}\b", joined, flags=re.I)]
        fallback = {
            "research_problem": paper.abstract[:500] or (joined[:500] if joined else ""),
            "method": ", ".join(methods[:6]),
            "datasets": datasets[:8], "baselines": baselines[:8], "metrics": metrics[:10], "main_results": [],
            "contributions": [], "limitations": [], "evidence": [
                {"chunk_id": c["chunk_id"], "page": c["page"], "section": c["section"], "claim": "heuristic evidence"} for c in evidence_chunks[:6]
            ],
        }
        prompt = f"""基于下面论文证据抽取结构化 PaperProfile。禁止猜测；找不到就用空字符串或空数组。
输出字段：research_problem, method, datasets, baselines, metrics, main_results, contributions, limitations, evidence。
evidence 数组元素必须包含 chunk_id、page、claim。
论文：{paper.title}\n证据：\n{context}"""
        data = llm.json(prompt, fallback=fallback)
        profile = db.get(PaperProfile, paper_id) or PaperProfile(paper_id=paper_id)
        profile.research_problem = data.get("research_problem") or ""
        profile.method = data.get("method") or ""
        profile.datasets_json = json.dumps(data.get("datasets") or [], ensure_ascii=False)
        profile.baselines_json = json.dumps(data.get("baselines") or [], ensure_ascii=False)
        profile.metrics_json = json.dumps(data.get("metrics") or [], ensure_ascii=False)
        profile.main_results_json = json.dumps(data.get("main_results") or [], ensure_ascii=False)
        profile.contributions_json = json.dumps(data.get("contributions") or [], ensure_ascii=False)
        profile.limitations_json = json.dumps(data.get("limitations") or [], ensure_ascii=False)
        profile.evidence_json = json.dumps(data.get("evidence") or fallback["evidence"], ensure_ascii=False)
        db.add(profile)
        db.commit()
        return profile.as_dict()


def attach_pdf_to_paper(paper_id: str, path: str) -> Paper:
    pages = parse_pdf(path)
    chunks = chunk_pages(pages)
    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        if not paper:
            raise ValueError("paper not found")
        for old in list(paper.chunks):
            db.delete(old)
        db.flush()
        paper.pdf_path = path
        paper.analysis_level = "fulltext"
        for c in chunks:
            db.add(Chunk(id=f"{paper.id}_{c['id'][:24]}", paper_id=paper.id, page=c["page"], section=c["section"], content=c["content"]))
        db.commit(); db.refresh(paper)
        return paper

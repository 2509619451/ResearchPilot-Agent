from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import httpx

from ..core.config import settings


HEADERS = {"User-Agent": "ResearchPilot-Agent/5.0 (academic research assistant)"}


def _score(query: str, title: str, abstract: str, year: int | None) -> float:
    q = set(re.findall(r"[\w\-]+", query.lower()))
    t = set(re.findall(r"[\w\-]+", (title + " " + abstract).lower()))
    overlap = len(q & t) / max(1, len(q))
    title_sim = SequenceMatcher(None, query.lower(), title.lower()).ratio()
    recency = 0.1 if year and year >= 2022 else 0.0
    return round(min(1.0, 0.55 * overlap + 0.35 * title_sim + recency), 4)


async def search_crossref(query: str, limit: int) -> list[dict]:
    url = f"https://api.crossref.org/works?query.bibliographic={quote_plus(query)}&rows={limit}&select=DOI,title,author,published,abstract,URL,container-title,type"
    async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
        r = await client.get(url)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
    out = []
    for x in items:
        title = (x.get("title") or [""])[0]
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in x.get("author", [])]
        parts = ((x.get("published") or {}).get("date-parts") or [[None]])[0]
        year = parts[0] if parts else None
        abstract = re.sub(r"<[^>]+>", " ", x.get("abstract") or "")
        out.append({
            "title": title, "authors": authors, "year": year, "abstract": abstract,
            "doi": x.get("DOI") or "", "arxiv_id": "", "venue": ((x.get("container-title") or [""])[0]),
            "url": x.get("URL") or "", "pdf_url": "", "source": "crossref",
            "relevance_score": _score(query, title, abstract, year),
        })
    return out


async def search_arxiv(query: str, limit: int) -> list[dict]:
    url = f"https://export.arxiv.org/api/query?search_query=all:{quote_plus(query)}&start=0&max_results={limit}&sortBy=relevance"
    async with httpx.AsyncClient(timeout=25, headers=HEADERS) as client:
        r = await client.get(url)
        r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        title = " ".join((e.findtext("a:title", default="", namespaces=ns)).split())
        abstract = " ".join((e.findtext("a:summary", default="", namespaces=ns)).split())
        published = e.findtext("a:published", default="", namespaces=ns)
        year = int(published[:4]) if published[:4].isdigit() else None
        arxiv_url = e.findtext("a:id", default="", namespaces=ns)
        arxiv_id = arxiv_url.rsplit("/", 1)[-1]
        authors = [a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)]
        pdf = ""
        for link in e.findall("a:link", ns):
            if link.attrib.get("type") == "application/pdf":
                pdf = link.attrib.get("href", "")
        out.append({
            "title": title, "authors": authors, "year": year, "abstract": abstract,
            "doi": "", "arxiv_id": arxiv_id, "venue": "arXiv", "url": arxiv_url,
            "pdf_url": pdf, "source": "arxiv", "relevance_score": _score(query, title, abstract, year),
        })
    return out


async def search_semantic_scholar(query: str, limit: int) -> list[dict]:
    fields = "title,authors,year,abstract,url,venue,externalIds,openAccessPdf"
    headers = dict(HEADERS)
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote_plus(query)}&limit={min(limit,100)}&fields={fields}"
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        r = await client.get(url)
        if r.status_code in {403, 429}:
            return []
        r.raise_for_status()
        items = r.json().get("data", [])
    out = []
    for x in items:
        ext = x.get("externalIds") or {}
        pdf = (x.get("openAccessPdf") or {}).get("url") or ""
        title = x.get("title") or ""
        abstract = x.get("abstract") or ""
        year = x.get("year")
        out.append({
            "title": title, "authors": [a.get("name", "") for a in x.get("authors") or []], "year": year,
            "abstract": abstract, "doi": ext.get("DOI") or "", "arxiv_id": ext.get("ArXiv") or "",
            "venue": x.get("venue") or "", "url": x.get("url") or "", "pdf_url": pdf,
            "source": "semantic_scholar", "relevance_score": _score(query, title, abstract, year),
        })
    return out


def deduplicate(items: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for x in items:
        if x.get("doi"):
            key = "doi:" + x["doi"].lower().strip()
        elif x.get("arxiv_id"):
            key = "arxiv:" + x["arxiv_id"].lower().strip()
        else:
            key = "title:" + re.sub(r"\W+", "", (x.get("title") or "").lower())
        if key not in best or x.get("relevance_score", 0) > best[key].get("relevance_score", 0):
            best[key] = x
    return sorted(best.values(), key=lambda z: z.get("relevance_score", 0), reverse=True)


async def search_all(query: str, limit: int = 20, sources: list[str] | None = None, year_from: int | None = None, year_to: int | None = None) -> list[dict]:
    sources = sources or ["crossref", "arxiv", "semantic_scholar"]
    tasks = []
    if "crossref" in sources:
        tasks.append(search_crossref(query, limit))
    if "arxiv" in sources:
        tasks.append(search_arxiv(query, limit))
    if "semantic_scholar" in sources:
        tasks.append(search_semantic_scholar(query, min(limit, 20)))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged = []
    for result in results:
        if not isinstance(result, Exception):
            merged.extend(result)
    if year_from is not None:
        merged = [x for x in merged if x.get("year") is None or x["year"] >= year_from]
    if year_to is not None:
        merged = [x for x in merged if x.get("year") is None or x["year"] <= year_to]
    return deduplicate(merged)[:limit]

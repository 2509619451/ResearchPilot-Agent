from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..agent.graph import research_graph
from ..core.config import settings
from ..core.db import Paper, Project, Report, ResearchRun, SessionLocal, init_db, utcnow
from ..core.llm import llm
from ..schemas import ChatRequest, ImportPapersRequest, ProjectCreate, ReportRequest, ResearchRequest, SearchRequest
from ..services.graph_service import graph_data, graph_retrieve, rebuild_project_graph
from ..services.paper_service import analyze_paper, attach_pdf_to_paper, import_metadata_papers, save_uploaded_pdf
from ..services.report_service import create_report
from ..services.retrieval_service import retrieve
from ..services.search_service import search_all


router = APIRouter()


def paper_dict(p: Paper) -> dict:
    return {
        "id": p.id, "project_id": p.project_id, "title": p.title, "authors": p.authors, "year": p.year,
        "abstract": p.abstract, "doi": p.doi, "arxiv_id": p.arxiv_id, "venue": p.venue, "source": p.source,
        "url": p.url, "pdf_url": p.pdf_url, "pdf_path": p.pdf_path, "analysis_level": p.analysis_level,
        "relevance_score": p.relevance_score,
    }


def _ensure_project(project_id: str):
    with SessionLocal() as db:
        if not db.get(Project, project_id):
            raise HTTPException(404, "project not found")


@router.get("/health")
def health():
    return {"ok": True, "version": "5.0.0", "llm_enabled": llm.enabled}


@router.post("/projects")
def create_project(req: ProjectCreate):
    with SessionLocal() as db:
        p = Project(name=req.name.strip(), query=req.query, description=req.description)
        db.add(p); db.commit(); db.refresh(p)
        return {"id": p.id, "name": p.name, "query": p.query, "description": p.description, "created_at": p.created_at}


@router.get("/projects")
def list_projects():
    with SessionLocal() as db:
        rows = db.scalars(select(Project).order_by(Project.updated_at.desc())).all()
        return [{"id": p.id, "name": p.name, "query": p.query, "description": p.description, "created_at": p.created_at, "updated_at": p.updated_at} for p in rows]


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        if not p: raise HTTPException(404, "project not found")
        return {"id": p.id, "name": p.name, "query": p.query, "description": p.description}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        if not p: raise HTTPException(404, "project not found")
        db.delete(p); db.commit()
        return {"ok": True}


@router.post("/papers/upload")
def upload_paper(project_id: str, file: UploadFile = File(...)):
    _ensure_project(project_id)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF")
    file.file.seek(0, 2); size = file.file.tell(); file.file.seek(0)
    if size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {settings.max_upload_mb}MB")
    try:
        p = save_uploaded_pdf(project_id, file.filename or "paper.pdf", file.file)
        return paper_dict(p)
    except Exception as e:
        raise HTTPException(400, f"PDF 解析失败: {e}")


@router.get("/papers")
def list_papers(project_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(Paper).where(Paper.project_id == project_id).order_by(Paper.created_at.desc())).all()
        return [paper_dict(p) for p in rows]


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str):
    with SessionLocal() as db:
        p = db.get(Paper, paper_id)
        if not p: raise HTTPException(404, "paper not found")
        data = paper_dict(p)
        data["profile"] = p.profile.as_dict() if p.profile else None
        data["chunk_count"] = len(p.chunks)
        return data


@router.post("/papers/{paper_id}/analyze")
def analyze_one(paper_id: str):
    with SessionLocal() as db:
        p = db.get(Paper, paper_id)
        if not p: raise HTTPException(404, "paper not found")
        project_id = p.project_id
    ev = retrieve(project_id, "research problem method dataset baseline metric results contribution limitation", [paper_id], top_k=18)
    return analyze_paper(paper_id, ev)


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "非法 PDF URL")
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise HTTPException(400, "禁止访问内网/本机地址")
    except socket.gaierror:
        raise HTTPException(400, "URL 域名无法解析")


@router.post("/papers/{paper_id}/fetch-pdf")
def fetch_pdf(paper_id: str):
    with SessionLocal() as db:
        p = db.get(Paper, paper_id)
        if not p: raise HTTPException(404, "paper not found")
        url = p.pdf_url
    if not url: raise HTTPException(400, "该论文没有可用 pdf_url")
    _validate_public_url(url)
    root = Path(settings.storage_dir) / "papers"; root.mkdir(parents=True, exist_ok=True)
    target = root / f"{paper_id}.pdf"
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "ResearchPilot-Agent/5.0"}) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            total = 0
            with target.open("wb") as f:
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > settings.max_upload_mb * 1024 * 1024:
                        target.unlink(missing_ok=True); raise HTTPException(413, "下载 PDF 超过大小限制")
                    f.write(chunk)
    p = attach_pdf_to_paper(paper_id, str(target))
    return paper_dict(p)


@router.post("/search")
async def search(req: SearchRequest):
    _ensure_project(req.project_id)
    try:
        rows = await search_all(req.query, req.limit, req.sources, req.year_from, req.year_to)
        return {"count": len(rows), "papers": rows}
    except Exception as e:
        raise HTTPException(502, f"学术搜索失败: {e}")


@router.post("/papers/import")
def import_papers(req: ImportPapersRequest):
    _ensure_project(req.project_id)
    rows = import_metadata_papers(req.project_id, req.papers)
    return {"count": len(rows), "papers": [paper_dict(x) for x in rows]}


@router.post("/chat")
def chat(req: ChatRequest):
    _ensure_project(req.project_id)
    evidence = retrieve(req.project_id, req.question, req.paper_ids or None, req.top_k)
    graph_ctx = graph_retrieve(req.project_id, req.question, limit=10, hops=2)
    ctx = "\n\n".join(f"[{x['paper_title']} p.{x['page']} chunk={x['chunk_id']}]\n{x['content']}" for x in evidence)
    graph_text = "\n".join(f"{x['type']}: {x['label']}" for x in graph_ctx)
    prompt = f"""仅根据给出的论文证据与知识图谱回答问题。每个事实性结论尽量给出 [论文标题 p.页码]。证据不足就明确说明。
问题：{req.question}\nGraphRAG实体：\n{graph_text}\n论文证据：\n{ctx}"""
    answer = llm.chat(prompt)
    if not answer:
        answer = "当前未配置 LLM_API_KEY。已返回最相关证据，请配置 .env 后获得生成式回答。\n\n" + "\n\n".join(f"- {x['paper_title']} p.{x['page']}: {x['content'][:350]}" for x in evidence[:5])
    return {"answer": answer, "evidence": evidence, "graph_context": graph_ctx}


@router.post("/graph/rebuild/{project_id}")
def rebuild_graph(project_id: str):
    _ensure_project(project_id)
    return rebuild_project_graph(project_id)


@router.get("/graph/{project_id}")
def get_graph(project_id: str):
    _ensure_project(project_id)
    return graph_data(project_id)


@router.post("/research/run")
async def run_research(req: ResearchRequest):
    _ensure_project(req.project_id)
    with SessionLocal() as db:
        run = ResearchRun(project_id=req.project_id, query=req.query, status="running")
        db.add(run); db.commit(); db.refresh(run); run_id = run.id
    inputs = req.model_dump()
    inputs.update({"trace": [], "errors": [], "retry_count": 0, "unsupported_claims": []})
    try:
        result = await asyncio.to_thread(research_graph.invoke, inputs, {"recursion_limit": 30})
        with SessionLocal() as db:
            run = db.get(ResearchRun, run_id)
            run.status = "completed"
            run.state_json = json.dumps(result, ensure_ascii=False, default=str)
            run.final_report = result.get("final_report", "")
            run.finished_at = utcnow(); db.commit()
        return {"run_id": run_id, "status": "completed", **result}
    except Exception as e:
        with SessionLocal() as db:
            run = db.get(ResearchRun, run_id)
            run.status = "failed"; run.state_json = json.dumps({"error": str(e)}, ensure_ascii=False); run.finished_at = utcnow(); db.commit()
        raise HTTPException(500, f"研究工作流失败: {e}")


@router.get("/research/runs")
def list_runs(project_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(ResearchRun).where(ResearchRun.project_id == project_id).order_by(ResearchRun.created_at.desc())).all()
        return [{"id": r.id, "query": r.query, "status": r.status, "created_at": r.created_at, "finished_at": r.finished_at} for r in rows]


@router.get("/research/runs/{run_id}")
def get_run(run_id: str):
    with SessionLocal() as db:
        r = db.get(ResearchRun, run_id)
        if not r: raise HTTPException(404, "run not found")
        state = json.loads(r.state_json or "{}")
        return {"id": r.id, "project_id": r.project_id, "query": r.query, "status": r.status, "final_report": r.final_report, "state": state}


@router.post("/reports")
def export_report(req: ReportRequest):
    try:
        data = create_report(req.project_id, req.run_id, req.title, req.formats)
    except ValueError as e:
        raise HTTPException(400, str(e))
    data["downloads"] = {k: f"{settings.api_prefix}/reports/{data['report_id']}/download/{fmt}" for k, fmt in [("markdown","md"),("html","html"),("pdf","pdf")]}
    return data


@router.get("/reports")
def list_reports(project_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(Report).where(Report.project_id == project_id).order_by(Report.created_at.desc())).all()
        return [{"id": r.id, "title": r.title, "run_id": r.run_id, "created_at": r.created_at, "has_md": bool(r.markdown_path), "has_html": bool(r.html_path), "has_pdf": bool(r.pdf_path)} for r in rows]


@router.get("/reports/{report_id}/download/{fmt}")
def download_report(report_id: str, fmt: str):
    with SessionLocal() as db:
        r = db.get(Report, report_id)
        if not r: raise HTTPException(404, "report not found")
        path = {"md": r.markdown_path, "html": r.html_path, "pdf": r.pdf_path}.get(fmt, "")
    if not path or not Path(path).exists(): raise HTTPException(404, f"{fmt} 文件不存在")
    return FileResponse(path, filename=Path(path).name)

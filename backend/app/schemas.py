from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str
    query: str = ""
    description: str = ""


class SearchRequest(BaseModel):
    project_id: str
    query: str
    year_from: int | None = None
    year_to: int | None = None
    limit: int = Field(20, ge=1, le=100)
    sources: list[str] = ["crossref", "arxiv", "semantic_scholar"]


class ImportPapersRequest(BaseModel):
    project_id: str
    papers: list[dict[str, Any]]


class ChatRequest(BaseModel):
    project_id: str
    question: str
    paper_ids: list[str] = []
    top_k: int = Field(8, ge=1, le=30)


class ResearchRequest(BaseModel):
    project_id: str
    query: str
    year_from: int | None = None
    year_to: int | None = None
    search_limit: int = Field(30, ge=5, le=100)
    select_limit: int = Field(8, ge=1, le=20)
    auto_search: bool = True


class ReportRequest(BaseModel):
    project_id: str
    run_id: str | None = None
    title: str = "ResearchPilot Research Report"
    formats: list[str] = ["md", "html", "pdf"]

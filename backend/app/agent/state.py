from __future__ import annotations
from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    project_id: str
    query: str
    year_from: int | None
    year_to: int | None
    search_limit: int
    select_limit: int
    auto_search: bool

    research_goal: str
    sub_questions: list[str]
    search_queries: list[str]
    candidate_papers: list[dict[str, Any]]
    selected_papers: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    paper_profiles: list[dict[str, Any]]
    graph_context: list[dict[str, Any]]
    comparison_results: dict[str, Any]
    claims: list[dict[str, Any]]
    unsupported_claims: list[dict[str, Any]]
    report_outline: list[str]
    final_report: str
    retry_count: int
    trace: list[str]
    errors: list[str]

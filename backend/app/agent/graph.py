from __future__ import annotations
from typing import Literal

from langgraph.graph import END, START, StateGraph

from ..core.config import settings
from .nodes import analyst_node, citation_node, extractor_node, graph_node, planner_node, reader_node, search_node, selector_node, writer_node
from .state import ResearchState


def route_after_citation(state: ResearchState) -> Literal["reader", "writer"]:
    unsupported = state.get("unsupported_claims") or []
    if unsupported and state.get("retry_count", 0) <= settings.citation_retry_limit:
        return "reader"
    return "writer"


def build_research_graph():
    g = StateGraph(ResearchState)
    g.add_node("planner", planner_node)
    g.add_node("search", search_node)
    g.add_node("selector", selector_node)
    g.add_node("reader", reader_node)
    g.add_node("extractor", extractor_node)
    g.add_node("graph_rag", graph_node)
    g.add_node("analyst", analyst_node)
    g.add_node("citation", citation_node)
    g.add_node("writer", writer_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "search")
    g.add_edge("search", "selector")
    g.add_edge("selector", "reader")
    g.add_edge("reader", "extractor")
    g.add_edge("extractor", "graph_rag")
    g.add_edge("graph_rag", "analyst")
    g.add_edge("analyst", "citation")
    g.add_conditional_edges("citation", route_after_citation, {"reader": "reader", "writer": "writer"})
    g.add_edge("writer", END)
    return g.compile()


research_graph = build_research_graph()

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter

from sqlalchemy import select

from ..core.config import settings
from ..core.db import Paper, SessionLocal
from ..core.llm import llm
from ..services.graph_service import graph_retrieve, rebuild_project_graph
from ..services.paper_service import analyze_paper, import_metadata_papers
from ..services.retrieval_service import retrieve
from ..services.search_service import search_all
from .state import ResearchState


def _trace(state: ResearchState, text: str) -> list[str]:
    return [*(state.get("trace") or []), text]


def planner_node(state: ResearchState) -> dict:
    query = state["query"]
    fallback = {
        "research_goal": query,
        "sub_questions": [
            f"{query} 的主要研究问题是什么？", f"{query} 使用了哪些方法？", f"{query} 常用哪些数据集和评价指标？",
            f"{query} 的代表性实验结果与局限是什么？", f"{query} 存在哪些潜在研究空白？",
        ],
        "search_queries": [query, f"{query} methods", f"{query} dataset benchmark", f"{query} review"],
        "report_outline": ["研究背景", "检索与筛选", "主要方法", "数据集与评价指标", "跨论文比较", "局限与研究空白", "未来方向", "参考文献"],
    }
    prompt = f"""把科研主题拆成结构化研究计划：{query}
输出 JSON：research_goal, sub_questions(3-6项), search_queries(2-5项), report_outline。检索词尽量适合学术搜索。"""
    plan = llm.json(prompt, fallback)
    return {**plan, "trace": _trace(state, "✓ Research Planner：已拆分研究问题与搜索词")}


def search_node(state: ResearchState) -> dict:
    if not state.get("auto_search", True):
        return {"candidate_papers": [], "trace": _trace(state, "✓ Search Agent：跳过自动搜索，使用项目现有论文")}
    queries = state.get("search_queries") or [state["query"]]
    all_items = []
    per_query = max(5, min(20, state.get("search_limit", 30) // max(1, len(queries))))
    for q in queries:
        try:
            all_items.extend(asyncio.run(search_all(q, per_query, year_from=state.get("year_from"), year_to=state.get("year_to"))))
        except Exception as e:
            state.setdefault("errors", []).append(f"search {q}: {e}")
    # local dedup
    seen, merged = set(), []
    for x in sorted(all_items, key=lambda z: z.get("relevance_score", 0), reverse=True):
        key = (x.get("doi") or x.get("arxiv_id") or re.sub(r"\W+", "", (x.get("title") or "").lower())).lower()
        if key and key not in seen:
            seen.add(key); merged.append(x)
    merged = merged[: state.get("search_limit", 30)]
    return {"candidate_papers": merged, "trace": _trace(state, f"✓ Search Agent：检索并去重得到 {len(merged)} 篇候选论文")}


def selector_node(state: ResearchState) -> dict:
    project_id = state["project_id"]
    select_limit = min(settings.max_research_papers, state.get("select_limit", 8))
    candidates = state.get("candidate_papers") or []
    if candidates:
        selected_meta = candidates[:select_limit]
        import_metadata_papers(project_id, selected_meta)
    with SessionLocal() as db:
        papers = db.scalars(select(Paper).where(Paper.project_id == project_id).order_by(Paper.relevance_score.desc(), Paper.created_at.desc())).all()
    selected = [{
        "paper_id": p.id, "title": p.title, "authors": p.authors, "year": p.year,
        "abstract": p.abstract, "doi": p.doi, "source": p.source, "analysis_level": p.analysis_level,
        "relevance_score": p.relevance_score,
    } for p in papers[:select_limit]]
    return {"selected_papers": selected, "trace": _trace(state, f"✓ Paper Selector：选择 {len(selected)} 篇核心论文")}


def reader_node(state: ResearchState) -> dict:
    selected_ids = [p["paper_id"] for p in state.get("selected_papers", [])]
    questions = [state["query"], *(state.get("sub_questions") or [])]
    if state.get("unsupported_claims"):
        questions += [x.get("claim", "") for x in state["unsupported_claims"] if x.get("claim")]
    collected, seen = [], set()
    for q in questions[:8]:
        for c in retrieve(state["project_id"], q, selected_ids, top_k=5):
            if c["chunk_id"] not in seen:
                seen.add(c["chunk_id"]); collected.append(c)
    collected.sort(key=lambda x: x["score"], reverse=True)
    return {"evidence": collected[:40], "trace": _trace(state, f"✓ Reader Agent：检索到 {len(collected[:40])} 条论文证据")}


def extractor_node(state: ResearchState) -> dict:
    profiles = []
    ev_by_paper = {}
    for e in state.get("evidence", []):
        ev_by_paper.setdefault(e["paper_id"], []).append(e)
    for p in state.get("selected_papers", []):
        try:
            profiles.append(analyze_paper(p["paper_id"], ev_by_paper.get(p["paper_id"], [])[:14]))
        except Exception as e:
            state.setdefault("errors", []).append(f"extract {p['paper_id']}: {e}")
    return {"paper_profiles": profiles, "trace": _trace(state, f"✓ Extraction Agent：结构化抽取 {len(profiles)} 篇 PaperProfile")}


def graph_node(state: ResearchState) -> dict:
    stats = rebuild_project_graph(state["project_id"])
    context = graph_retrieve(state["project_id"], state["query"], limit=16, hops=2)
    return {
        "graph_context": context,
        "trace": _trace(state, f"✓ GraphRAG：构建知识图谱 {stats['nodes']} 节点 / {stats['edges']} 关系，并检索 {len(context)} 个相关实体"),
    }


def analyst_node(state: ResearchState) -> dict:
    profiles = state.get("paper_profiles") or []
    papers = {p["paper_id"]: p for p in state.get("selected_papers", [])}
    rows = []
    method_counter, dataset_counter, metric_counter = Counter(), Counter(), Counter()
    for pf in profiles:
        p = papers.get(pf["paper_id"], {})
        method = pf.get("method") or "未从当前证据确认"
        datasets = pf.get("datasets") or []
        metrics = pf.get("metrics") or []
        rows.append({"paper_id": pf["paper_id"], "title": p.get("title", ""), "year": p.get("year"), "method": method, "datasets": datasets, "metrics": metrics, "limitations": pf.get("limitations") or []})
        if method and method != "未从当前证据确认": method_counter[method[:100]] += 1
        dataset_counter.update(str(x.get("name") if isinstance(x, dict) else x) for x in datasets)
        metric_counter.update(str(x.get("name") if isinstance(x, dict) else x) for x in metrics)

    ev = state.get("evidence") or []
    claims_fallback = []
    for row in rows[:6]:
        claim = f"论文《{row['title']}》的方法被抽取为：{row['method']}。"
        matches = [e for e in ev if e["paper_id"] == row["paper_id"]][:2]
        claims_fallback.append({"claim": claim, "paper_id": row["paper_id"], "evidence_chunk_ids": [m["chunk_id"] for m in matches]})

    context = json.dumps({"rows": rows, "graph_context": state.get("graph_context", [])}, ensure_ascii=False)[:24000]
    prompt = f"""基于结构化论文信息和 GraphRAG 实体，生成跨论文分析 JSON。
严禁跨不同数据集直接做性能排名。输出：themes(list), common_limitations(list), research_gaps(list), future_directions(list), claims(list)。
claims 每项：claim, paper_id(可空), evidence_chunk_ids(list)。若没有证据不要写成确定事实。
数据：{context}"""
    fallback = {
        "themes": [x for x, _ in method_counter.most_common(5)],
        "common_limitations": [str(x) for pf in profiles for x in (pf.get("limitations") or [])][:8],
        "research_gaps": ["当前集合中部分论文仅有摘要级证据，研究空白需要结合更多全文进一步核验。"],
        "future_directions": ["扩大可获得全文的论文集合并对关键结论进行页码级证据核验。"],
        "claims": claims_fallback,
    }
    analysis = llm.json(prompt, fallback)
    comparison = {
        "papers": rows,
        "method_frequency": method_counter.most_common(10),
        "dataset_frequency": dataset_counter.most_common(10),
        "metric_frequency": metric_counter.most_common(10),
        "themes": analysis.get("themes") or [],
        "common_limitations": analysis.get("common_limitations") or [],
        "research_gaps": analysis.get("research_gaps") or [],
        "future_directions": analysis.get("future_directions") or [],
    }
    return {"comparison_results": comparison, "claims": analysis.get("claims") or claims_fallback, "trace": _trace(state, "✓ Analyst Agent：完成跨论文比较、趋势与研究空白分析")}


def citation_node(state: ResearchState) -> dict:
    evidence_index = {e["chunk_id"]: e for e in state.get("evidence", [])}
    unsupported, verified = [], []
    for item in state.get("claims", []):
        ids = [x for x in item.get("evidence_chunk_ids", []) if x in evidence_index]
        claim = item.get("claim", "")
        if ids:
            verified.append({**item, "supported": True, "evidence": [evidence_index[x] for x in ids[:3]]})
        else:
            unsupported.append({**item, "supported": False})
    retry = state.get("retry_count", 0)
    return {
        "claims": verified,
        "unsupported_claims": unsupported,
        "retry_count": retry + (1 if unsupported else 0),
        "trace": _trace(state, f"✓ Citation Agent：核验 {len(verified)} 条支持性 Claim，{len(unsupported)} 条证据不足"),
    }


def writer_node(state: ResearchState) -> dict:
    comp = state.get("comparison_results") or {}
    evidence = state.get("evidence") or []
    citations = []
    for e in evidence[:20]:
        citations.append(f"- [{e['paper_title']}] paper_id={e['paper_id']}, page={e['page']}, chunk_id={e['chunk_id']}")
    claims_text = "\n".join(
        f"- {c['claim']} " + " ".join(f"[{x['paper_title']} p.{x['page']} / {x['chunk_id']}]" for x in c.get("evidence", []))
        for c in state.get("claims", [])
    ) or "- 当前没有通过证据映射核验的确定性 Claim。"
    table_lines = ["| Paper | Year | Method | Dataset | Metric |", "|---|---:|---|---|---|"]
    for r in comp.get("papers", []):
        ds = ", ".join(str(x.get("name") if isinstance(x, dict) else x) for x in r.get("datasets", [])) or "未确认"
        ms = ", ".join(str(x.get("name") if isinstance(x, dict) else x) for x in r.get("metrics", [])) or "未确认"
        method = str(r.get("method", "未确认")).replace("|", "/")[:180]
        title = str(r.get("title", "")).replace("|", "/")[:120]
        table_lines.append(f"| {title} | {r.get('year') or ''} | {method} | {ds[:120]} | {ms[:120]} |")

    fallback = f"""# ResearchPilot 研究报告\n\n## 1. 研究问题\n{state['query']}\n\n## 2. 研究计划\n""" + "\n".join(f"- {x}" for x in state.get("sub_questions", [])) + \
        "\n\n## 3. 论文对比\n" + "\n".join(table_lines) + \
        "\n\n## 4. 已核验关键结论\n" + claims_text + \
        "\n\n## 5. 研究主题\n" + "\n".join(f"- {x}" for x in comp.get("themes", [])) + \
        "\n\n## 6. 共同局限\n" + "\n".join(f"- {x}" for x in comp.get("common_limitations", [])) + \
        "\n\n## 7. 潜在研究空白\n" + "\n".join(f"- {x}" for x in comp.get("research_gaps", [])) + \
        "\n\n## 8. 未来方向\n" + "\n".join(f"- {x}" for x in comp.get("future_directions", [])) + \
        "\n\n## 9. Evidence Index\n" + "\n".join(citations)

    llm_prompt = f"""基于下列已结构化且经过 Citation Agent 处理的材料，写中文科研调研报告 Markdown。
必须区分：论文证据、Agent 综合分析、Agent 推断的研究空白。只把 supported claims 写成确定性事实；不要编造参考文献。
研究问题：{state['query']}
已核验 claims：{claims_text}
跨论文分析：{json.dumps(comp, ensure_ascii=False)[:18000]}
证据索引：{chr(10).join(citations)}"""
    report = llm.chat(llm_prompt) or fallback
    if state.get("unsupported_claims"):
        report += "\n\n## 证据不足的主张\n" + "\n".join(f"- 未能从当前证据直接验证：{x.get('claim','')}" for x in state["unsupported_claims"])
    return {"final_report": report, "trace": _trace(state, "✓ Writer Agent：已生成最终研究报告")}

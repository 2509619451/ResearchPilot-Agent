from __future__ import annotations

import hashlib
import json
import re
from collections import deque

import networkx as nx
from sqlalchemy import delete, select

from ..core.db import (
    GraphEdge,
    GraphNode,
    Paper,
    PaperProfile,
    SessionLocal,
)
from .retrieval_service import tokens


# ============================================================
# Graph ID
# ============================================================

def _normalize_label(label: str) -> str:
    """
    对知识图谱节点名称进行归一化。

    例如：
        " Transformer "
        "transformer"
        "TRANSFORMER"

    会得到相同的规范形式：
        "transformer"
    """
    return " ".join(
        str(label or "")
        .strip()
        .lower()
        .split()
    )


def _id(
    project_id: str,
    kind: str,
    label: str,
) -> str:
    """
    为 GraphNode 生成项目级唯一 ID。

    重要：
    ID 必须包含 project_id。

    否则：
        Project A / Year / 2025
        Project B / Year / 2025

    会产生完全相同的主键，
    从而触发 PostgreSQL UniqueViolation。

    ID 格式：
        year_xxxxxxxxxxxxxxxxxxxx
        author_xxxxxxxxxxxxxxxxxxxx
        method_xxxxxxxxxxxxxxxxxxxx
    """

    normalized = _normalize_label(label)

    raw = (
        f"{project_id}:"
        f"{kind.lower().strip()}:"
        f"{normalized}"
    )

    h = hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()[:20]

    return f"{kind.lower()}_{h}"


# ============================================================
# JSON Helper
# ============================================================

def _as_list(raw: str) -> list:
    """
    将数据库中的 JSON 字符串安全转换成 list。
    """

    try:
        value = json.loads(raw or "[]")

        return (
            value
            if isinstance(value, list)
            else []
        )

    except Exception:
        return []


# ============================================================
# Rebuild Knowledge Graph
# ============================================================

def rebuild_project_graph(
    project_id: str,
) -> dict:
    """
    重建指定 Project 的知识图谱。

    流程：

    1. 删除当前项目已有的 GraphEdge
    2. 删除当前项目已有的 GraphNode
    3. 读取当前项目 Paper
    4. 构建 Paper / Author / Year /
       Method / Dataset / Metric / Baseline
    5. 当前 rebuild 内进行 Node / Edge 去重
    6. 提交数据库
    """

    with SessionLocal() as db:

        # ----------------------------------------------------
        # 1. 删除旧 Edge
        # ----------------------------------------------------

        db.execute(
            delete(GraphEdge).where(
                GraphEdge.project_id
                == project_id
            )
        )

        # ----------------------------------------------------
        # 2. 删除旧 Node
        # ----------------------------------------------------

        db.execute(
            delete(GraphNode).where(
                GraphNode.project_id
                == project_id
            )
        )

        # 保证删除已经发送到数据库。
        db.flush()

        # ----------------------------------------------------
        # 3. 加载项目论文
        # ----------------------------------------------------

        papers = db.scalars(
            select(Paper).where(
                Paper.project_id
                == project_id
            )
        ).all()

        # 当前 rebuild 内部节点去重
        node_ids: set[str] = set()

        # 当前 rebuild 内部边去重
        edge_keys: set[
            tuple[str, str, str]
        ] = set()

        # ----------------------------------------------------
        # Node Helper
        # ----------------------------------------------------

        def add_node(
            kind: str,
            label: str,
            props=None,
        ) -> str | None:

            label = str(
                label or ""
            ).strip()

            if not label:
                return None

            # 核心修复：
            # ID 现在包含 project_id
            nid = _id(
                project_id,
                kind,
                label,
            )

            # 当前批次已有该实体
            if nid in node_ids:
                return nid

            node = GraphNode(
                id=nid,
                project_id=project_id,
                node_type=kind,
                label=label,
                properties_json=json.dumps(
                    props or {},
                    ensure_ascii=False,
                ),
            )

            db.add(node)

            node_ids.add(nid)

            return nid

        # ----------------------------------------------------
        # Edge Helper
        # ----------------------------------------------------

        def add_edge(
            src: str | None,
            dst: str | None,
            rel: str,
        ) -> None:

            if not src or not dst:
                return

            key = (
                src,
                dst,
                rel,
            )

            # 当前批次已有该关系
            if key in edge_keys:
                return

            db.add(
                GraphEdge(
                    project_id=project_id,
                    source_id=src,
                    target_id=dst,
                    relation=rel,
                )
            )

            edge_keys.add(key)

        # ====================================================
        # 构建知识图谱
        # ====================================================

        for paper in papers:

            # ------------------------------------------------
            # Paper
            # ------------------------------------------------

            pid = add_node(
                "Paper",
                paper.title,
                {
                    "paper_id": paper.id,
                    "year": paper.year,
                    "source": paper.source,
                },
            )

            if not pid:
                continue

            # ------------------------------------------------
            # Author
            # ------------------------------------------------

            for author in (
                paper.authors or []
            ):

                author_label = str(
                    author or ""
                ).strip()

                if not author_label:
                    continue

                author_id = add_node(
                    "Author",
                    author_label,
                )

                add_edge(
                    pid,
                    author_id,
                    "AUTHORED_BY",
                )

            # ------------------------------------------------
            # Year
            # ------------------------------------------------

            if paper.year:

                year_id = add_node(
                    "Year",
                    str(paper.year),
                )

                add_edge(
                    pid,
                    year_id,
                    "PUBLISHED_IN",
                )

            # ------------------------------------------------
            # PaperProfile
            # ------------------------------------------------

            profile = db.get(
                PaperProfile,
                paper.id,
            )

            if not profile:
                continue

            # ------------------------------------------------
            # Method
            # ------------------------------------------------

            if profile.method:

                methods = re.split(
                    r"[,;/\n]|\band\b|、|；",
                    profile.method,
                    flags=re.IGNORECASE,
                )

                for method in methods[:8]:

                    method = (
                        method
                        .strip()
                    )

                    if not (
                        2
                        <= len(method)
                        <= 120
                    ):
                        continue

                    method_id = add_node(
                        "Method",
                        method,
                    )

                    add_edge(
                        pid,
                        method_id,
                        "USES_METHOD",
                    )

            # ------------------------------------------------
            # Dataset
            # ------------------------------------------------

            for dataset in _as_list(
                profile.datasets_json
            ):

                if isinstance(
                    dataset,
                    dict,
                ):
                    label = dataset.get(
                        "name"
                    )
                else:
                    label = dataset

                label = str(
                    label or ""
                ).strip()

                if not label:
                    continue

                dataset_id = add_node(
                    "Dataset",
                    label,
                )

                add_edge(
                    pid,
                    dataset_id,
                    "EVALUATES_ON",
                )

            # ------------------------------------------------
            # Metric
            # ------------------------------------------------

            for metric in _as_list(
                profile.metrics_json
            ):

                if isinstance(
                    metric,
                    dict,
                ):
                    label = metric.get(
                        "name"
                    )
                else:
                    label = metric

                label = str(
                    label or ""
                ).strip()

                if not label:
                    continue

                metric_id = add_node(
                    "Metric",
                    label,
                )

                add_edge(
                    pid,
                    metric_id,
                    "REPORTS_METRIC",
                )

            # ------------------------------------------------
            # Baseline
            # ------------------------------------------------

            for baseline in _as_list(
                profile.baselines_json
            ):

                if isinstance(
                    baseline,
                    dict,
                ):
                    label = baseline.get(
                        "name"
                    )
                else:
                    label = baseline

                label = str(
                    label or ""
                ).strip()

                if not label:
                    continue

                baseline_id = add_node(
                    "Baseline",
                    label,
                )

                add_edge(
                    pid,
                    baseline_id,
                    "COMPARES_WITH",
                )

        # ----------------------------------------------------
        # 写入数据库
        # ----------------------------------------------------

        try:

            db.commit()

        except Exception:

            db.rollback()

            raise

        return {
            "nodes": len(node_ids),
            "edges": len(edge_keys),
        }


# ============================================================
# Graph Data
# ============================================================

def graph_data(
    project_id: str,
) -> dict:
    """
    获取指定 Project 的完整知识图谱。
    """

    with SessionLocal() as db:

        nodes = db.scalars(
            select(GraphNode).where(
                GraphNode.project_id
                == project_id
            )
        ).all()

        edges = db.scalars(
            select(GraphEdge).where(
                GraphEdge.project_id
                == project_id
            )
        ).all()

    return {
        "nodes": [
            {
                "id": node.id,
                "type": node.node_type,
                "label": node.label,
                "properties": json.loads(
                    node.properties_json
                    or "{}"
                ),
            }
            for node in nodes
        ],
        "edges": [
            {
                "id": edge.id,
                "source": edge.source_id,
                "target": edge.target_id,
                "relation": edge.relation,
            }
            for edge in edges
        ],
    }


# ============================================================
# Graph Retrieval
# ============================================================

def graph_retrieve(
    project_id: str,
    query: str,
    limit: int = 12,
    hops: int = 2,
) -> list[dict]:
    """
    根据 Query 从知识图谱中寻找相关节点，
    并进行最多 hops 跳邻居扩展。
    """

    data = graph_data(
        project_id
    )

    if not data["nodes"]:
        return []

    query = str(
        query or ""
    ).strip()

    if not query:
        return []

    q_tokens = set(
        tokens(query)
    )

    scores = []

    # ========================================================
    # Seed Ranking
    # ========================================================

    for node in data["nodes"]:

        label = str(
            node["label"]
            or ""
        )

        node_tokens = set(
            tokens(label)
        )

        union = (
            q_tokens
            | node_tokens
        )

        overlap = (
            len(
                q_tokens
                & node_tokens
            )
            / max(
                1,
                len(union),
            )
        )

        label_lower = (
            label.lower()
        )

        query_lower = (
            query.lower()
        )

        substring = 0.0

        if (
            label_lower
            and query_lower
            and (
                label_lower
                in query_lower
                or query_lower
                in label_lower
            )
        ):
            substring = 0.5

        score = (
            overlap
            + substring
        )

        if score > 0:
            scores.append(
                (
                    score,
                    node,
                )
            )

    scores.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    seeds = [
        node
        for _, node
        in scores[:5]
    ]

    # 没有关键词命中时，
    # 使用前 3 个 Paper 作为 fallback
    if not seeds:

        seeds = [
            node
            for node
            in data["nodes"]
            if node["type"]
            == "Paper"
        ][:3]

    # ========================================================
    # Build NetworkX Graph
    # ========================================================

    graph = nx.Graph()

    for node in data["nodes"]:

        graph.add_node(
            node["id"],
            **node,
        )

    for edge in data["edges"]:

        source = edge["source"]
        target = edge["target"]

        # 防止数据库异常数据导致
        # NetworkX 创建无属性孤立节点
        if (
            source not in graph
            or target not in graph
        ):
            continue

        graph.add_edge(
            source,
            target,
            relation=edge[
                "relation"
            ],
        )

    # ========================================================
    # BFS
    # ========================================================

    found = {}

    for seed in seeds:

        seed_id = seed["id"]

        if seed_id not in graph:
            continue

        queue = deque(
            [
                (
                    seed_id,
                    0,
                )
            ]
        )

        seen = {
            seed_id
        }

        while queue:

            nid, depth = (
                queue.popleft()
            )

            node = graph.nodes[
                nid
            ]

            found[nid] = {
                "id": nid,
                "type": node.get(
                    "type"
                ),
                "label": node.get(
                    "label"
                ),
                "depth": depth,
            }

            if depth >= hops:
                continue

            for neighbour in (
                graph.neighbors(nid)
            ):

                if neighbour in seen:
                    continue

                seen.add(
                    neighbour
                )

                queue.append(
                    (
                        neighbour,
                        depth + 1,
                    )
                )

    # ========================================================
    # Result
    # ========================================================

    results = list(
        found.values()
    )

    # 优先近距离节点
    results.sort(
        key=lambda item: (
            item["depth"],
            item["type"] or "",
            item["label"] or "",
        )
    )

    return results[:limit]
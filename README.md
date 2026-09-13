# ResearchPilot-Agent V5 Final

ResearchPilot-Agent 是一个面向科研文献调研的 AI Agent 平台。V5 在 V4 的 LangGraph 多 Agent + Citation Verification 基础上，加入 Knowledge Graph、GraphRAG、报告导出和完整 Web 平台。

## 1. V5 最终能力

完整研究链路：

```text
Research Planner
  ↓
Academic Search (Crossref / arXiv / Semantic Scholar)
  ↓
Dedup + Paper Selector
  ↓
Reader Agent (BM25/RAG)
  ↓
Extraction Agent (PaperProfile)
  ↓
Knowledge Graph Builder
  ↓
GraphRAG Entity Expansion
  ↓
Analyst Agent
  ↓
Citation Agent
  ├─ 证据不足 → Reader（有限重试）
  └─ 证据充分 → Writer
  ↓
Markdown / HTML / PDF Report
```

支持：

- Project 管理
- 本地 PDF 上传与页码级切分
- Crossref / arXiv / Semantic Scholar 多源搜索
- DOI / arXiv ID / 标题去重
- PaperProfile：研究问题、方法、数据集、Baseline、Metric、Result、Contribution、Limitation
- Evidence：paper_id / page / chunk_id
- LangGraph 多 Agent 自动研究
- Citation Verification 与证据不足回读
- Knowledge Graph：Paper / Author / Method / Dataset / Metric / Baseline / Year
- GraphRAG：实体匹配 + 多跳图扩展 + 文本证据联合回答
- React 可视化知识图谱
- Markdown / HTML / PDF 报告导出
- SQLite 本地运行 / PostgreSQL Docker 运行
- PDF 下载 SSRF 防护和文件大小限制

## 2. 目录

```text
ResearchPilot-Agent-V5-Final/
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ schemas.py
│  │  ├─ core/
│  │  │  ├─ config.py
│  │  │  ├─ db.py
│  │  │  └─ llm.py
│  │  ├─ api/router.py
│  │  ├─ agent/
│  │  │  ├─ state.py
│  │  │  ├─ nodes.py
│  │  │  └─ graph.py
│  │  └─ services/
│  │     ├─ paper_service.py
│  │     ├─ search_service.py
│  │     ├─ retrieval_service.py
│  │     ├─ graph_service.py
│  │     └─ report_service.py
│  ├─ tests/test_smoke.py
│  ├─ requirements.txt
│  └─ Dockerfile
├─ frontend/
│  ├─ src/main.jsx
│  ├─ src/style.css
│  ├─ package.json
│  ├─ Dockerfile
│  └─ nginx.conf
├─ storage/
├─ docker-compose.yml
├─ .env.example
└─ run_local.ps1
```

## 3. 本地运行（Windows）

### 3.1 环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

至少填写：

```env
LLM_API_KEY=你的Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

不填 `LLM_API_KEY` 也能启动、上传、搜索、检索、建图和导出；但 Planner/Extraction/Writer 会使用降级逻辑，生成能力明显弱于配置 LLM 后。

### 3.2 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

接口文档：

```text
http://localhost:8000/docs
```

### 3.3 前端

另开 PowerShell：

```powershell
cd frontend
npm install
npm run dev
```

访问：

```text
http://localhost:5173
```

## 4. Docker 一键启动

先建立 `.env`：

```powershell
Copy-Item .env.example .env
```

填写 Key 后：

```powershell
docker compose up --build
```

访问：

```text
Web:  http://localhost:8080
API:  http://localhost:8000/docs
```

Docker 模式自动使用 PostgreSQL，本地手工模式默认使用 SQLite。

## 5. 推荐使用顺序

1. 创建 Project。
2. 上传自己的 PDF，或进入“学术搜索”搜索并导入论文元数据。
3. 对重要搜索论文，如返回了开放 PDF，可调用 `POST /api/papers/{paper_id}/fetch-pdf` 获取全文；也可以直接上传 PDF。
4. 进入“自动研究”，输入研究主题并执行 V5 LangGraph 工作流。
5. 查看 Agent Trace、对比报告、Citation 结果。
6. 打开“知识图谱”重建/查看图谱。
7. 在“GraphRAG 问答”中继续追问。
8. 导出 Markdown / HTML / PDF。

## 6. 关键 API

```text
POST   /api/projects
GET    /api/projects
POST   /api/papers/upload?project_id=...
GET    /api/papers?project_id=...
POST   /api/papers/{paper_id}/analyze
POST   /api/papers/{paper_id}/fetch-pdf
POST   /api/search
POST   /api/papers/import
POST   /api/chat
POST   /api/graph/rebuild/{project_id}
GET    /api/graph/{project_id}
POST   /api/research/run
GET    /api/research/runs?project_id=...
POST   /api/reports
GET    /api/reports?project_id=...
GET    /api/reports/{report_id}/download/{md|html|pdf}
```

## 7. V4 → V5 的变化

V4 保留：Planner、Search、Selector、Reader、Extraction、Analyst、Citation、Writer、Evidence Store、条件回读。

V5 新增：

```text
PaperProfile
    ↓
Knowledge Graph Builder
    ↓
Paper ─ USES_METHOD ─ Method
Paper ─ EVALUATES_ON ─ Dataset
Paper ─ REPORTS_METRIC ─ Metric
Paper ─ COMPARES_WITH ─ Baseline
Paper ─ AUTHORED_BY ─ Author
Paper ─ PUBLISHED_IN ─ Year
    ↓
GraphRAG multi-hop expansion
    ↓
Analyst / Chat / Report
```

报告导出会保留 Evidence Index，方便从结论追踪到论文页码和 chunk。

## 8. 测试

```powershell
cd backend
$env:PYTHONPATH="."
pytest -q
```

## 9. 生产环境建议

当前最终版已经能作为完整课程/作品集项目运行。真正线上生产环境还建议继续加入：OAuth/JWT 多用户隔离、对象存储、Redis/Celery 异步任务、pgvector/专用向量库、专业 Cross-Encoder reranker、GROBID、可观测性与限流。

import React, {useEffect, useMemo, useState} from 'react'
import {createRoot} from 'react-dom/client'
import ForceGraph2D from 'react-force-graph-2d'
import {marked} from 'marked'
import './style.css'

const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

async function api(path, options={}) {
  const r = await fetch(API + path, options)
  if (!r.ok) throw new Error((await r.json().catch(()=>({detail:r.statusText}))).detail || r.statusText)
  const type = r.headers.get('content-type') || ''
  return type.includes('json') ? r.json() : r.text()
}
const post = (path, body) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})

function App(){
  const [projects,setProjects]=useState([]), [project,setProject]=useState(null), [papers,setPapers]=useState([])
  const [tab,setTab]=useState('research'), [msg,setMsg]=useState(''), [busy,setBusy]=useState(false)
  const [searchQ,setSearchQ]=useState(''), [searchResults,setSearchResults]=useState([]), [researchQ,setResearchQ]=useState('')
  const [trace,setTrace]=useState([]), [report,setReport]=useState(''), [graph,setGraph]=useState({nodes:[],edges:[]}), [chat,setChat]=useState([])
  const [chatQ,setChatQ]=useState(''), [reports,setReports]=useState([])

  const loadProjects=async()=>{const x=await api('/projects');setProjects(x); if(!project && x.length) setProject(x[0])}
  const refresh=async(p=project)=>{if(!p)return; setPapers(await api('/papers?project_id='+p.id)); setReports(await api('/reports?project_id='+p.id)); try{setGraph(await api('/graph/'+p.id))}catch{}}
  useEffect(()=>{loadProjects().catch(e=>setMsg(e.message))},[])
  useEffect(()=>{if(project){setResearchQ(project.query||'');refresh(project).catch(e=>setMsg(e.message))}},[project?.id])

  const createProject=async()=>{
    const name=prompt('项目名称'); if(!name)return; const query=prompt('研究主题（可留空）')||''
    const p=await post('/projects',{name,query,description:''}); await loadProjects(); setProject(p)
  }
  const upload=async(ev)=>{
    const f=ev.target.files?.[0]; if(!f||!project)return
    const fd=new FormData(); fd.append('file',f); setBusy(true)
    try{await api('/papers/upload?project_id='+project.id,{method:'POST',body:fd}); await refresh(); setMsg('PDF 上传并解析完成')}
    catch(e){setMsg(e.message)} finally{setBusy(false);ev.target.value=''}
  }
  const search=async()=>{
    if(!searchQ.trim())return;setBusy(true)
    try{const x=await post('/search',{project_id:project.id,query:searchQ,limit:30,sources:['crossref','arxiv','semantic_scholar']});setSearchResults(x.papers)}catch(e){setMsg(e.message)}finally{setBusy(false)}
  }
  const importSelected=async()=>{
    if(!searchResults.length)return;setBusy(true)
    try{await post('/papers/import',{project_id:project.id,papers:searchResults.slice(0,10)});await refresh();setMsg('已导入前 10 篇检索结果')}
    catch(e){setMsg(e.message)}finally{setBusy(false)}
  }
  const runResearch=async()=>{
    if(!researchQ.trim())return;setBusy(true);setTrace([]);setReport('')
    try{
      const x=await post('/research/run',{project_id:project.id,query:researchQ,search_limit:30,select_limit:8,auto_search:true})
      setTrace(x.trace||[]);setReport(x.final_report||''); await refresh(); setMsg('V5 多 Agent 研究流程完成')
    }catch(e){setMsg(e.message)}finally{setBusy(false)}
  }
  const ask=async()=>{
    if(!chatQ.trim())return; const q=chatQ;setChatQ('');setChat(v=>[...v,{role:'user',text:q}]);setBusy(true)
    try{const x=await post('/chat',{project_id:project.id,question:q,top_k:8});setChat(v=>[...v,{role:'assistant',text:x.answer,evidence:x.evidence}])}
    catch(e){setChat(v=>[...v,{role:'assistant',text:'错误：'+e.message}])}finally{setBusy(false)}
  }
  const rebuild=async()=>{setBusy(true);try{await post('/graph/rebuild/'+project.id,{});setGraph(await api('/graph/'+project.id));setMsg('知识图谱已重建')}catch(e){setMsg(e.message)}finally{setBusy(false)}}
  const exportReport=async()=>{setBusy(true);try{const x=await post('/reports',{project_id:project.id,title:project.name+' Research Report',formats:['md','html','pdf']});await refresh();setMsg('报告已导出')}catch(e){setMsg(e.message)}finally{setBusy(false)}}

  const graphData=useMemo(()=>({nodes:graph.nodes.map(x=>({...x,name:x.label})),links:graph.edges.map(x=>({source:x.source,target:x.target,relation:x.relation}))}),[graph])
  if(!project) return <div className="empty"><h1>ResearchPilot-Agent V5</h1><button onClick={createProject}>创建第一个科研项目</button>{msg&&<p>{msg}</p>}</div>

  return <div className="app">
    <aside>
      <div className="brand">ResearchPilot <b>V5</b><small>AI 科研助手 Agent</small></div>
      <button className="new" onClick={createProject}>＋ 新建项目</button>
      <div className="project-list">{projects.map(p=><div key={p.id} className={'project '+(p.id===project.id?'active':'')} onClick={()=>setProject(p)}>{p.name}<small>{p.query||'未设置主题'}</small></div>)}</div>
    </aside>
    <main>
      <header><div><h2>{project.name}</h2><span>{papers.length} papers · {graph.nodes.length} graph nodes</span></div><div>{busy&&<span className="badge">Agent 正在执行…</span>}</div></header>
      <nav>{[['research','自动研究'],['library','论文库'],['search','学术搜索'],['chat','GraphRAG 问答'],['graph','知识图谱'],['reports','报告导出']].map(([k,v])=><button onClick={()=>setTab(k)} className={tab===k?'on':''} key={k}>{v}</button>)}</nav>
      {msg&&<div className="notice" onClick={()=>setMsg('')}>{msg}</div>}

      {tab==='research'&&<section>
        <h3>V5 LangGraph 多 Agent 自动研究</h3><textarea value={researchQ} onChange={e=>setResearchQ(e.target.value)} placeholder="例如：研究 Transformer 在供应链需求预测中的应用，重点比较模型结构、数据集、评价指标和实验效果。"/>
        <div className="row"><button onClick={runResearch} disabled={busy}>开始自动研究</button>{report&&<button className="secondary" onClick={exportReport}>导出当前报告</button>}</div>
        {!!trace.length&&<div className="trace"><h4>Agent 执行过程</h4>{trace.map((x,i)=><div key={i}>{x}</div>)}</div>}
        {report&&<article dangerouslySetInnerHTML={{__html:marked.parse(report)}}/>}
      </section>}

      {tab==='library'&&<section><div className="section-head"><h3>Paper Library</h3><label className="upload">上传 PDF<input type="file" accept="application/pdf" onChange={upload}/></label></div>
        <div className="cards">{papers.map(p=><div className="card" key={p.id}><h4>{p.title}</h4><p>{(p.authors||[]).join(', ')}</p><div><span>{p.year||'—'}</span><span>{p.source}</span><span>{p.analysis_level}</span></div><small>{p.doi||p.arxiv_id||''}</small></div>)}</div>
      </section>}

      {tab==='search'&&<section><h3>Academic Search</h3><div className="searchbar"><input value={searchQ} onChange={e=>setSearchQ(e.target.value)} onKeyDown={e=>e.key==='Enter'&&search()} placeholder="agentic rag / transformer forecasting ..."/><button onClick={search}>搜索</button></div>
        {!!searchResults.length&&<><div className="row"><b>共 {searchResults.length} 篇</b><button className="secondary" onClick={importSelected}>导入前 10 篇到项目</button></div><div className="cards">{searchResults.map((p,i)=><div className="card" key={i}><h4>{p.title}</h4><p>{(p.authors||[]).slice(0,4).join(', ')}</p><div><span>{p.year||'—'}</span><span>{p.source}</span><span>score {p.relevance_score}</span></div><p className="abstract">{p.abstract||'无摘要'}</p></div>)}</div></>}
      </section>}

      {tab==='chat'&&<section className="chat"><h3>GraphRAG 科研问答</h3><div className="messages">{chat.map((m,i)=><div key={i} className={'message '+m.role}><b>{m.role==='user'?'你':'ResearchPilot'}</b><div>{m.text}</div>{m.evidence&&<details><summary>查看证据 {m.evidence.length}</summary>{m.evidence.map(e=><p key={e.chunk_id}>[{e.paper_title} p.{e.page}] {e.content.slice(0,300)}</p>)}</details>}</div>)}</div><div className="searchbar"><input value={chatQ} onChange={e=>setChatQ(e.target.value)} onKeyDown={e=>e.key==='Enter'&&ask()} placeholder="比较这些论文的方法、数据集和指标…"/><button onClick={ask}>发送</button></div></section>}

      {tab==='graph'&&<section><div className="section-head"><h3>Knowledge Graph / GraphRAG</h3><button onClick={rebuild}>重建图谱</button></div><div className="graphbox"><ForceGraph2D graphData={graphData} nodeLabel={n=>`${n.type}: ${n.label}`} linkLabel={l=>l.relation} nodeAutoColorBy="type"/></div></section>}

      {tab==='reports'&&<section><div className="section-head"><h3>Research Reports</h3><button onClick={exportReport}>导出最新研究结果</button></div><div className="cards">{reports.map(r=><div className="card" key={r.id}><h4>{r.title}</h4><p>{new Date(r.created_at).toLocaleString()}</p><div>{r.has_md&&<a href={`${API}/reports/${r.id}/download/md`} target="_blank">Markdown</a>}{r.has_html&&<a href={`${API}/reports/${r.id}/download/html`} target="_blank">HTML</a>}{r.has_pdf&&<a href={`${API}/reports/${r.id}/download/pdf`} target="_blank">PDF</a>}</div></div>)}</div></section>}
    </main>
  </div>
}

createRoot(document.getElementById('root')).render(<App/>)

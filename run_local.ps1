$ErrorActionPreference = "Stop"
Write-Host "ResearchPilot-Agent V5 本地启动助手" -ForegroundColor Cyan
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "已生成 .env，请填写 LLM_API_KEY 后再启动可获得完整 LLM 能力。" -ForegroundColor Yellow }
Write-Host "请分别打开两个 PowerShell：" -ForegroundColor Green
Write-Host "终端1: cd backend; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; uvicorn app.main:app --reload --port 8000"
Write-Host "终端2: cd frontend; npm install; npm run dev"
Write-Host "浏览器: http://localhost:5173"

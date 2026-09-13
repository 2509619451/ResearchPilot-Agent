from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .core.config import settings
from .core.db import init_db


app = FastAPI(title=settings.app_name, version=settings.app_version, description="AI 科研助手 Agent V5：Multi-Agent + Citation Verification + Knowledge Graph + GraphRAG + Report Export")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_prefix)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {"name": settings.app_name, "version": settings.app_version, "docs": "/docs"}

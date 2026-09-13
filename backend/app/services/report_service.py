from __future__ import annotations

import html
import re
from pathlib import Path

import markdown as mdlib
from sqlalchemy import select

from ..core.config import settings
from ..core.db import Report, ResearchRun, SessionLocal


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", name)[:80]


def create_report(project_id: str, run_id: str | None, title: str, formats: list[str]) -> dict:
    with SessionLocal() as db:
        run = db.get(ResearchRun, run_id) if run_id else db.scalar(select(ResearchRun).where(ResearchRun.project_id == project_id).order_by(ResearchRun.created_at.desc()))
        if not run:
            raise ValueError("没有可导出的研究运行结果")
        content = run.final_report or "# Empty Report\n\n暂无报告内容。"
        root = Path(settings.storage_dir) / "reports" / project_id
        root.mkdir(parents=True, exist_ok=True)
        stem = f"{_safe(title)}_{run.id[:8]}"
        record = Report(project_id=project_id, run_id=run.id, title=title)

        if "md" in formats:
            path = root / f"{stem}.md"
            path.write_text(content, encoding="utf-8")
            record.markdown_path = str(path)

        html_body = mdlib.markdown(content, extensions=["tables", "fenced_code"])
        full_html = f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>
<style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;line-height:1.75;color:#1f2937}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}code{{background:#f3f4f6;padding:2px 5px}}blockquote{{border-left:4px solid #ddd;padding-left:16px;color:#555}}</style>
</head><body>{html_body}</body></html>"""
        if "html" in formats or "pdf" in formats:
            html_path = root / f"{stem}.html"
            html_path.write_text(full_html, encoding="utf-8")
            record.html_path = str(html_path)
        if "pdf" in formats:
            try:
                from weasyprint import HTML
                pdf_path = root / f"{stem}.pdf"
                HTML(string=full_html, base_url=str(root)).write_pdf(str(pdf_path))
                record.pdf_path = str(pdf_path)
            except Exception as e:
                record.pdf_path = ""
                content += f"\n\n> PDF 导出失败：{e}"
        db.add(record); db.commit(); db.refresh(record)
        return {
            "report_id": record.id,
            "run_id": run.id,
            "markdown_path": record.markdown_path,
            "html_path": record.html_path,
            "pdf_path": record.pdf_path,
        }

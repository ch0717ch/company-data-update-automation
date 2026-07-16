from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import BASE_DIR, settings
from .db import CompanyDatabase
from .excel import build_excel
from .source_client import HiSeoulClient, SourceAPIError, normalize_company_name


STATIC_DIR = Path(__file__).resolve().parent / "static"
APP_DATA_DIR = Path(__file__).resolve().parent / "data"
database = CompanyDatabase(settings.database_path)
source_client = HiSeoulClient(settings)
bulk_jobs: dict[str, dict] = {}
bulk_tasks: dict[str, asyncio.Task] = {}
active_bulk_job_id: str | None = None
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(database.initialize)
    yield


app = FastAPI(
    title="하이서울기업 로컬 데이터 관리자",
    version="1.2.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SourceSearchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    item: str = Field(default="", max_length=500)
    interest_category: str = Field(default="", max_length=500)
    address: str = Field(default="", max_length=500)
    export_cd: str = Field(default="", max_length=500)
    sort: str = Field(default="1", max_length=10)
    include_details: bool = True


class BulkStartRequest(BaseModel):
    company_names_text: str = Field(default="", max_length=250_000)
    sync_all: bool = False


def parse_company_names(text: str) -> list[str]:
    """Parse a copied Excel column into a normalized, de-duplicated name list."""
    names: list[str] = []
    seen: set[str] = set()
    ignored_headers = {"기업명", "회사명", "업체명", "기업이름", "companyname"}
    for raw_line in text.replace("\ufeff", "").splitlines():
        cells = [cell.strip() for cell in raw_line.split("\t") if cell.strip()]
        if not cells:
            continue
        name = cells[0].strip().strip('"').strip("'")
        name = re.sub(r"^\s*[•·*]\s*", "", name)
        name = re.sub(r"^\s*\d+\s*[.)-]\s*", "", name).strip()
        key = normalize_company_name(name)
        if not key or key in ignored_headers or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _filter_dict(
    *,
    q: str = "",
    industry: str = "",
    representative: str = "",
    address: str = "",
    founded_from: int | None = None,
    founded_to: int | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    status: str = "",
    changed_only: bool = False,
    sort: str = "name",
) -> dict:
    return {
        "q": q,
        "industry": industry,
        "representative": representative,
        "address": address,
        "founded_from": founded_from,
        "founded_to": founded_to,
        "employees_min": employees_min,
        "employees_max": employees_max,
        "status": status,
        "changed_only": changed_only,
        "sort": sort,
    }


def _prepare_company_urls(companies: list[dict]) -> None:
    for company in companies:
        relative = company.get("source_url") or ""
        if relative.startswith("/"):
            company["source_url"] = f"{settings.source_base_url}{relative}"
        available = company.setdefault("available_fields", [])
        if "source_url" not in available:
            available.append("source_url")


async def _run_bulk_job(job_id: str, names: list[str], sync_all: bool) -> None:
    global active_bulk_job_id
    job = bulk_jobs[job_id]
    run_id: int | None = None

    def update_progress(current: int, total: int, message: str) -> None:
        job.update(
            {
                "status": "running",
                "progress_current": current,
                "progress_total": total,
                "message": message,
            }
        )

    try:
        run_label = "[전체 대량동기화]" if sync_all else f"[대량추가] {len(names):,}개"
        run_id = await asyncio.to_thread(
            database.start_run,
            run_label,
            {"bulk": True, "sync_all": sync_all, "requested_count": len(names)},
        )
        job["run_id"] = run_id
        result = await source_client.bulk_search(
            names,
            sync_all=sync_all,
            include_details=True,
            progress_callback=update_progress,
        )
        _prepare_company_urls(result.companies)
        job.update(
            {
                "status": "saving",
                "message": f"{len(result.companies):,}개 기업을 SQLite에 저장하고 비교하는 중입니다.",
                "progress_current": len(result.companies),
                "progress_total": len(result.companies),
            }
        )
        sync_result = await asyncio.to_thread(
            database.sync_companies, run_id, result.companies
        )
        await asyncio.to_thread(
            database.finish_run,
            run_id,
            status="completed",
            source_result_count=result.site_total,
            synchronized_count=len(result.companies),
            detail_failed_count=result.detail_failed,
            new_count=sync_result["new"],
            changed_count=sync_result["changed"],
            unchanged_count=sync_result["unchanged"],
        )
        job.update(
            {
                "status": "completed",
                "message": "대량추가와 상세정보 저장을 완료했습니다.",
                "progress_current": result.matched_count,
                "progress_total": result.matched_count,
                "site_total": result.site_total,
                "requested_count": result.requested_count,
                "matched_count": result.matched_count,
                "unmatched_count": len(result.unmatched_names),
                "unmatched_names": result.unmatched_names,
                "detail_succeeded": result.detail_succeeded,
                "detail_failed": result.detail_failed,
                "new_count": sync_result["new"],
                "changed_count": sync_result["changed"],
                "unchanged_count": sync_result["unchanged"],
                "warnings": result.warnings,
            }
        )
    except Exception as exc:
        if run_id is not None:
            await asyncio.to_thread(
                database.finish_run,
                run_id,
                status="failed",
                error_message=str(exc),
            )
        job.update(
            {
                "status": "failed",
                "message": str(exc),
                "error": str(exc),
            }
        )
    finally:
        if active_bulk_job_id == job_id:
            active_bulk_job_id = None
        bulk_tasks.pop(job_id, None)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "database": str(settings.database_path),
        "source": settings.source_base_url,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@app.post("/api/source/search")
async def search_source(request: SourceSearchRequest) -> dict:
    filters = request.model_dump(exclude={"query", "include_details"})
    run_id = await asyncio.to_thread(database.start_run, request.query.strip(), filters)
    try:
        source_result = await source_client.search(
            request.query,
            item=request.item,
            interest_category=request.interest_category,
            address=request.address,
            export_cd=request.export_cd,
            sort=request.sort,
            include_details=request.include_details,
        )
        _prepare_company_urls(source_result.companies)
        sync_result = await asyncio.to_thread(
            database.sync_companies, run_id, source_result.companies
        )
        await asyncio.to_thread(
            database.finish_run,
            run_id,
            status="completed",
            source_result_count=source_result.total_found,
            synchronized_count=len(source_result.companies),
            detail_failed_count=source_result.detail_failed,
            new_count=sync_result["new"],
            changed_count=sync_result["changed"],
            unchanged_count=sync_result["unchanged"],
        )
        return {
            "run_id": run_id,
            "source_result_count": source_result.total_found,
            "synchronized_count": len(source_result.companies),
            "detail_succeeded": source_result.detail_succeeded,
            "detail_failed": source_result.detail_failed,
            "truncated": source_result.truncated,
            "warnings": source_result.warnings,
            "new_count": sync_result["new"],
            "changed_count": sync_result["changed"],
            "unchanged_count": sync_result["unchanged"],
            "results": sync_result["results"],
        }
    except SourceAPIError as exc:
        await asyncio.to_thread(
            database.finish_run,
            run_id,
            status="failed",
            error_message=str(exc),
        )
        status_code = 400 if "기업명을" in str(exc) else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except Exception as exc:
        await asyncio.to_thread(
            database.finish_run,
            run_id,
            status="failed",
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"검색 결과 처리 중 오류가 발생했습니다: {exc}") from exc


@app.get("/api/bulk/default-names")
async def default_bulk_names() -> dict:
    path = APP_DATA_DIR / "default_company_names.txt"
    if not path.exists():
        return {"text": "", "count": 0}
    text = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
    names = parse_company_names(text)
    return {"text": "\n".join(names), "count": len(names)}


@app.post("/api/bulk/start")
async def start_bulk(request: BulkStartRequest) -> dict:
    global active_bulk_job_id
    if active_bulk_job_id:
        active = bulk_jobs.get(active_bulk_job_id)
        if active and active.get("status") not in {"completed", "failed"}:
            return {**active, "already_running": True}

    names = parse_company_names(request.company_names_text)
    if not request.sync_all and not names:
        raise HTTPException(
            status_code=400,
            detail="엑셀에서 복사한 기업명 열을 입력하거나 전체 동기화를 선택하세요.",
        )
    if len(names) > 5_000:
        raise HTTPException(status_code=400, detail="한 번에 입력할 수 있는 기업명은 최대 5,000개입니다.")

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "queued",
        "message": "대량추가 작업을 준비하고 있습니다.",
        "progress_current": 0,
        "progress_total": 0,
        "requested_count": len(names),
        "matched_count": 0,
        "unmatched_count": 0,
        "sync_all": request.sync_all,
        "warnings": [],
    }
    bulk_jobs[job_id] = job
    active_bulk_job_id = job_id
    task = asyncio.create_task(_run_bulk_job(job_id, names, request.sync_all))
    bulk_tasks[job_id] = task
    return job


@app.get("/api/bulk/{job_id}")
async def bulk_status(job_id: str) -> dict:
    job = bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="대량추가 작업을 찾을 수 없습니다.")
    return job


@app.get("/api/bulk/{job_id}/unmatched")
async def bulk_unmatched(job_id: str) -> PlainTextResponse:
    job = bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="대량추가 작업을 찾을 수 없습니다.")
    names = job.get("unmatched_names") or []
    return PlainTextResponse(
        "\n".join(names),
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''unmatched_companies.txt"
        },
    )


@app.get("/api/companies")
async def list_companies(
    q: str = "",
    industry: str = "",
    representative: str = "",
    address: str = "",
    founded_from: int | None = None,
    founded_to: int | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    status: str = "",
    changed_only: bool = False,
    sort: str = "name",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filters = _filter_dict(
        q=q,
        industry=industry,
        representative=representative,
        address=address,
        founded_from=founded_from,
        founded_to=founded_to,
        employees_min=employees_min,
        employees_max=employees_max,
        status=status,
        changed_only=changed_only,
        sort=sort,
    )
    return await asyncio.to_thread(
        database.list_companies, filters, limit=limit, offset=offset
    )


@app.get("/api/companies/{source_id}")
async def company_detail(source_id: str) -> dict:
    company = await asyncio.to_thread(database.get_company, source_id)
    if not company:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    history = await asyncio.to_thread(database.get_history, source_id)
    return {"company": company, "history": history}


@app.get("/api/companies/{source_id}/history")
async def company_history(source_id: str) -> dict:
    company = await asyncio.to_thread(database.get_company, source_id)
    if not company:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    return {"items": await asyncio.to_thread(database.get_history, source_id)}


@app.get("/api/stats")
async def stats() -> dict:
    return await asyncio.to_thread(database.stats)


@app.get("/api/database/download")
async def download_database() -> FileResponse:
    await asyncio.to_thread(database.initialize)
    await asyncio.to_thread(database.checkpoint)
    return FileResponse(
        settings.database_path,
        media_type="application/vnd.sqlite3",
        filename=f"hiseoul_companies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
    )


@app.post("/api/database/open-folder")
async def open_database_folder() -> dict:
    await asyncio.to_thread(database.initialize)
    folder = settings.database_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise HTTPException(status_code=501, detail=f"DB 폴더: {folder}")
    await asyncio.to_thread(os.startfile, str(folder))
    return {"ok": True, "path": str(folder), "database": str(settings.database_path)}


@app.get("/api/export")
async def export_excel(
    scope: Literal["all", "changed"] = "all",
    q: str = "",
    industry: str = "",
    representative: str = "",
    address: str = "",
    founded_from: int | None = None,
    founded_to: int | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    status: str = "",
    changed_only: bool = False,
    sort: str = "name",
) -> StreamingResponse:
    filters = _filter_dict(
        q=q,
        industry=industry,
        representative=representative,
        address=address,
        founded_from=founded_from,
        founded_to=founded_to,
        employees_min=employees_min,
        employees_max=employees_max,
        status=status,
        changed_only=scope == "changed" or changed_only,
        sort=sort,
    )
    result = await asyncio.to_thread(
        database.list_companies,
        filters,
        limit=100_000,
        offset=0,
        include_last_changes=False,
    )
    companies = result["items"]
    changes = (
        await asyncio.to_thread(
            database.changes_for_companies, [item["source_id"] for item in companies]
        )
        if scope == "changed"
        else []
    )
    try:
        output = await asyncio.to_thread(
            build_excel,
            companies,
            changes,
            scope=scope,
            source_base_url=settings.source_base_url,
        )
    except Exception as exc:
        logger.exception("엑셀 내보내기 실패: scope=%s, companies=%s", scope, len(companies))
        raise HTTPException(
            status_code=500,
            detail="엑셀 파일을 생성하지 못했습니다. 프로그램을 다시 실행한 뒤 재시도해 주세요.",
        ) from exc
    date_text = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = "변경결과" if scope == "changed" else "전체결과"
    filename = f"하이서울기업_{label}_{date_text}.xlsx"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/{path:path}", include_in_schema=False)
async def spa_fallback(path: str) -> FileResponse:
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API 경로를 찾을 수 없습니다.")
    return FileResponse(STATIC_DIR / "index.html")

from __future__ import annotations

import os
import io
import re
import time
import zipfile
import secrets
import asyncio
import hashlib
import logging
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

# Manifest modules (100% untouched business logic)
from exporter import create_excel_manifest
from notion_service import (
    fetch_manifest_cases,
    update_case_status_in_notion,
    update_cases_by_codes,
    upload_batch_reports_to_notion,
    upload_single_case_report,
)
from auth import verify_credentials, is_rate_limited, new_csrf_token, clear_rate_limits

# Report Bot modules (100% untouched business logic)
from report_bot.config import BASE_DIR, Settings, get_settings
from report_bot.domain import (
    AppPreferences,
    CaseType,
    ItemState,
    ItemUpdate,
    JobItem,
    JobRecord,
    JobState,
    LabSource,
    Participant,
    ReportData,
    ReviewMode,
)
from report_bot.images import transparent_logo_png
from report_bot.ordering import (
    has_expected_primary_role,
    order_participants,
    role_combination_warnings,
)
from report_bot.parser import (
    CASE_RE,
    valid_date_of_birth,
)
from report_bot.parsing import (
    ReportParseError,
    parse_report,
)
from report_bot.pipeline import ReportPipeline
from report_bot.preferences import PreferencesStore
from report_bot.store import JobNotFoundError, JobStore
from report_bot.integrity import (
    detect_layout_anomalies,
    fetch_notion_verification_data,
    determine_report_classification,
    determine_test_type,
    cross_verify_participants,
)
import concurrent.futures

load_dotenv()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def _settings(request: Request) -> Settings:
    return request.app.state.settings

def _require_api(request: Request) -> None:
    session = request.session
    if not session.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
        )

def _require_mutation(request: Request) -> None:
    _require_api(request)
    # Check CSRF token for mutating Report Bot requests
    expected = str(request.session.get("csrf_token", ""))
    supplied = request.headers.get("x-csrf-token", "") or request.headers.get("X-CSRF-Token", "")
    if expected and supplied and not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")

def _get_job(request: Request, job_id: str) -> JobRecord:
    try:
        return request.app.state.job_store.get(job_id)
    except (JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Job not found or expired") from exc

def _public_participant(participant: Participant) -> dict[str, Any]:
    return participant.model_dump(mode="json")

def _public_report(report: ReportData) -> dict[str, Any]:
    payload = report.model_dump(mode="json", exclude={"participants"})
    payload["participants"] = [
        _public_participant(participant)
        for participant in order_participants(report.test_type, report.participants)
    ]
    return payload

def _public_item(item: JobItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "original_filename": item.original_filename,
        "case_type": item.case_type.value,
        "state": item.state.value,
        "report": _public_report(item.report) if item.report else None,
        "parse_error": item.parse_error,
        "approved": item.approved,
        "output_filename": item.output_filename,
        "processing_error": item.processing_error,
        "layout_warnings": item.layout_warnings,
        "classification_info": item.classification_info,
        "notion_verification": item.notion_verification,
        "needs_manual_review": item.needs_manual_review,
        "notion_page_id": item.notion_page_id,
        "notion_upload_status": item.notion_upload_status,
        "notion_upload_error": item.notion_upload_error,
    }

def _public_job(job: JobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "lab_source": job.lab_source.value,
        "review_mode": job.review_mode.value,
        "default_case_type": job.default_case_type.value,
        "auto_upload_notion": job.auto_upload_notion,
        "state": job.state.value,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "expires_at": job.expires_at.isoformat(),
        "progress": job.progress,
        "download_ready": bool(job.zip_path and job.zip_path.exists()),
        "items": [_public_item(item) for item in job.items],
    }

def _validation_errors(report: ReportData) -> list[str]:
    errors: list[str] = []
    if not CASE_RE.fullmatch(report.case_code):
        errors.append("Case reference must match [PREFIX]-...-DDMMYY-NN")
    else:
        date_segment = report.case_code.split("-")[-2]
        try:
            expected_date = datetime.strptime(date_segment, "%d%m%y").date()
        except ValueError:
            errors.append("Case reference contains an invalid collection date")
        else:
            if report.collection_date != expected_date:
                errors.append("Collection date must match the case reference")

    if not report.test_type.strip():
        errors.append("Test type is required")
    min_participants = (
        1
        if any(t in report.test_type.upper() for t in ("GENDER", "SEX", "PROFILE", "PERSONAL"))
        else 2
    )
    if len(report.participants) < min_participants:
        errors.append(
            f"At least {min_participants} participant{'s are' if min_participants > 1 else ' is'} required"
        )
    if len({participant.id for participant in report.participants}) != len(report.participants):
        errors.append("Participant identifiers must be unique")
    samples = [participant.sample_number for participant in report.participants]
    if len(set(samples)) != len(samples):
        errors.append("Participant sample numbers must be unique")
    if any(
        not participant.role or not participant.full_name
        for participant in report.participants
    ):
        errors.append("Every participant requires a role and full name")
    if any(
        not valid_date_of_birth(participant.date_of_birth)
        for participant in report.participants
    ):
        errors.append("Dates of birth must be valid and use DD/MM/YYYY, --/--/YYYY, or -")
    if not has_expected_primary_role(report.test_type, report.participants):
        errors.append("Expected primary tested adult role was not found")
    return errors

def _duplicate_case_warnings(job: JobRecord) -> None:
    case_items: dict[str, list[JobItem]] = {}
    for item in job.items:
        if item.report:
            case_items.setdefault(item.report.case_code, []).append(item)
    for items in case_items.values():
        if len(items) < 2:
            continue
        for item in items:
            message = "Shared case reference in this batch (outputs will be numbered with _1, _2)"
            if message not in item.report.warnings:
                item.report.warnings.append(message)

def _find_item(job: JobRecord, item_id: str) -> JobItem:
    item = next((candidate for candidate in job.items if candidate.id == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Report item not found")
    return item

def _schedule_processing(app: FastAPI, job_id: str) -> None:
    async def run() -> None:
        try:
            async with app.state.processing_semaphore:
                await asyncio.to_thread(
                    app.state.pipeline.process_job,
                    job_id,
                    app.state.job_store,
                )

            # Check if auto-upload to Notion is requested
            with suppress(JobNotFoundError):
                job = app.state.job_store.get(job_id)
                if job.auto_upload_notion and job.state == JobState.COMPLETE:
                    eligible_items = [
                        item for item in job.items
                        if item.state == ItemState.COMPLETE
                        and item.output_path
                        and item.output_path.exists()
                        and item.report
                        and item.report.case_code
                    ]
                    if eligible_items:
                        items_data = [
                            {
                                "item_id": item.id,
                                "case_code": item.report.case_code,
                                "filename": item.output_filename or f"{item.report.case_code}.pdf",
                                "pdf_bytes": item.output_path.read_bytes(),
                                "page_id": item.notion_page_id,
                            }
                            for item in eligible_items
                        ]
                        for item in eligible_items:
                            item.notion_upload_status = "UPLOADING"
                        app.state.job_store.save(job)

                        try:
                            res = await asyncio.to_thread(
                                upload_batch_reports_to_notion,
                                items_data=items_data,
                                update_status=True,
                                target_status="Result Review",
                            )
                            result_map = {r.get("item_id"): r for r in res.get("results", [])}
                            for item in eligible_items:
                                r = result_map.get(item.id)
                                if r and r.get("success"):
                                    item.notion_upload_status = "UPLOADED"
                                    item.notion_upload_error = None
                                    if r.get("page_id") and not item.notion_page_id:
                                        item.notion_page_id = r.get("page_id")
                                elif r:
                                    item.notion_upload_status = "FAILED"
                                    item.notion_upload_error = r.get("error", "Upload failed")
                                else:
                                    item.notion_upload_status = "FAILED"
                                    item.notion_upload_error = "Upload failed"
                        except Exception as e:
                            logger.exception("Auto-upload to Notion failed", extra={"job_id": job_id})
                            for item in eligible_items:
                                if item.notion_upload_status == "UPLOADING":
                                    item.notion_upload_status = "FAILED"
                                    item.notion_upload_error = str(e)
                        finally:
                            app.state.job_store.save(job)
        except Exception:
            logger.exception("Background job failed", extra={"job_id": job_id})
            with suppress(JobNotFoundError):
                job = app.state.job_store.get(job_id)
                job.state = JobState.FAILED
                app.state.job_store.save(job)

    task = asyncio.create_task(run(), name=f"report-job-{job_id}")
    app.state.processing_tasks.add(task)
    task.add_done_callback(app.state.processing_tasks.discard)

async def _cleanup_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(app.state.settings.cleanup_interval_seconds)
        removed = await asyncio.to_thread(app.state.job_store.cleanup_expired)
        if removed:
            logger.info("Expired report jobs purged", extra={"count": removed})

def create_app(settings: Settings | None = None) -> FastAPI:
    application_settings = settings or get_settings()
    application_settings.ensure_directories()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.job_store.purge_orphans()
        cleanup_task = asyncio.create_task(_cleanup_loop(app), name="job-cleanup")
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            tasks = list(app.state.processing_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(
        title="Blueprint DNA Operations Super App",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.settings = application_settings
    app.state.job_store = JobStore(
        application_settings.job_dir,
        application_settings.job_ttl_seconds,
    )
    app.state.preferences = PreferencesStore(application_settings.settings_path)
    app.state.pipeline = ReportPipeline(application_settings)
    app.state.processing_tasks = set()
    app.state.processing_semaphore = asyncio.Semaphore(1)

    # Unified session configuration
    session_secret = os.getenv("SECRET_KEY") or application_settings.session_secret
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        session_cookie="blueprint_session",
        max_age=application_settings.session_absolute_seconds,
        same_site="lax",
        https_only=application_settings.secure_cookies,
    )

    # Static assets
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    # Security Headers Middleware
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Global Exception Handlers
    @app.exception_handler(JobNotFoundError)
    async def missing_job_handler(_request: Request, _exc: JobNotFoundError):
        return JSONResponse(status_code=404, content={"detail": "Job not found or expired"})

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException):
        # Format errors as { "error": msg } for Manifest API callers or { "detail": msg }
        detail = exc.detail
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": detail, "detail": detail},
        )

    # Health & System Checks
    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "healthy", "service": "blueprint-operations-hub"}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        favicon_path = BASE_DIR / "static" / "img" / "favicon.ico"
        if favicon_path.exists():
            return FileResponse(favicon_path, media_type="image/vnd.microsoft.icon")
        raise HTTPException(status_code=404, detail="Favicon not found")

    @app.get("/brand/logo", include_in_schema=False)
    async def brand_logo():
        logo_path = BASE_DIR / "blueprint_logo.png"
        if not logo_path.exists():
            logo_path = BASE_DIR / "static" / "img" / "blueprint_logo.png"
        if not logo_path.exists():
            raise HTTPException(status_code=404, detail="Logo unavailable")
        return Response(
            content=transparent_logo_png(logo_path),
            media_type="image/png",
        )

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse(url="/", status_code=303)
        csrf_token = request.session.get("csrf_token") or new_csrf_token()
        request.session["csrf_token"] = csrf_token
        next_url = request.query_params.get("next") or "/"
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf_token, "error": None, "next_url": next_url},
        )

    @app.post("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_submit(
        request: Request,
        username: Annotated[str, Form()] = "admin",
        password: Annotated[str, Form()] = "",
        next: Annotated[str, Form()] = "/",
        csrf_token: Annotated[str, Form()] = "",
    ):
        ip = request.client.host if request.client else "127.0.0.1"
        if is_rate_limited(ip, "login", limit=5, window_seconds=60):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "csrf_token": csrf_token,
                    "error": "Too many login attempts. Please wait 1 minute before trying again.",
                    "next_url": next,
                },
                status_code=429,
            )

        if verify_credentials(username, password, application_settings):
            request.session.clear()
            request.session["authenticated"] = True
            request.session["user"] = username
            request.session["csrf_token"] = new_csrf_token()
            request.session["issued_at"] = int(time.time())
            request.session["last_seen"] = int(time.time())
            target_url = next if next and next.startswith("/") else "/"
            return RedirectResponse(url=target_url, status_code=303)
        else:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "csrf_token": new_csrf_token(),
                    "error": "Invalid username or password.",
                    "next_url": next,
                },
                status_code=401,
            )

    @app.get("/logout", include_in_schema=False)
    @app.post("/logout", include_in_schema=False)
    async def logout_submit(request: Request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    # =========================================================================
    # SUPER APP UNIFIED WORKSPACE
    # =========================================================================

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/manifest", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/reports", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    async def hub_workspace(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
        
        csrf_token = request.session.get("csrf_token")
        if not csrf_token:
            csrf_token = new_csrf_token()
            request.session["csrf_token"] = csrf_token

        lo_available = bool(
            getattr(app.state.pipeline, "converter", None)
            and app.state.pipeline.converter.available
        )
        has_key = bool(os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN"))
        has_db = bool(
            os.getenv("NOTION_DATABASE_ID")
            or os.getenv("NOTION_PARTICIPANT_DB_ID")
            or os.getenv("NOTION_CASES_DB_ID")
        )
        notion_ready = has_key and has_db

        preferences = app.state.preferences.read()
        return templates.TemplateResponse(
            request=request,
            name="hub.html",
            context={
                "user": request.session.get("user", "Admin"),
                "csrf_token": csrf_token,
                "default_review_mode": preferences.default_review_mode.value,
                "default_case_type": getattr(preferences, "default_case_type", CaseType.ISSUE).value,
                "max_batch_files": application_settings.max_batch_files,
                "notion_ready": notion_ready,
                "libreoffice_ready": lo_available,
            },
        )

    # =========================================================================
    # SYSTEM STATUS & MANIFEST API ENDPOINTS
    # =========================================================================

    @app.get("/api/system/status")
    async def system_status(request: Request):
        _require_api(request)
        lo_available = bool(
            getattr(app.state.pipeline, "converter", None)
            and app.state.pipeline.converter.available
        )
        has_key = bool(os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN"))
        has_db = bool(
            os.getenv("NOTION_DATABASE_ID")
            or os.getenv("NOTION_PARTICIPANT_DB_ID")
            or os.getenv("NOTION_CASES_DB_ID")
        )
        notion_ready = has_key and has_db
        return {
            "notion": {
                "status": "ready" if notion_ready else "issue",
                "label": "Ready" if notion_ready else "Issue",
                "configured": notion_ready,
            },
            "libreoffice": {
                "status": "ready" if lo_available else "issue",
                "label": "Ready" if lo_available else "Issue",
                "available": lo_available,
            },
        }

    @app.get("/api/notion/status")
    async def notion_status(request: Request):
        _require_api(request)
        has_key = bool(os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN"))
        has_db = bool(
            os.getenv("NOTION_DATABASE_ID")
            or os.getenv("NOTION_PARTICIPANT_DB_ID")
            or os.getenv("NOTION_CASES_DB_ID")
        )
        return {
            "configured": has_key and has_db,
            "has_api_key": has_key,
            "has_database_id": has_db,
        }

    @app.api_route("/api/notion/fetch", methods=["GET", "POST"])
    async def notion_fetch(request: Request):
        _require_api(request)
        req_data = {}
        if request.method == "POST":
            with suppress(Exception):
                req_data = await request.json()

        lab_filter = req_data.get("lab") or request.query_params.get("lab")
        filter_mode = req_data.get("filter_mode") or request.query_params.get("filter_mode") or "manifest_batch"
        force_refresh = (
            req_data.get("force_refresh", False)
            or request.query_params.get("force", "").lower() == "true"
        )

        ip = request.client.host if request.client else "127.0.0.1"
        if is_rate_limited(ip, "notion_fetch", limit=20, window_seconds=60):
            return JSONResponse({"error": "Request limit exceeded. Please wait a moment."}, status_code=429)

        try:
            result = await asyncio.to_thread(
                fetch_manifest_cases,
                filter_mode=filter_mode,
                lab_filter=lab_filter,
                force_refresh=force_refresh,
            )
            return JSONResponse(result)
        except Exception as e:
            logger.exception("Failed to fetch from Notion")
            return JSONResponse({"error": f"Failed to fetch from Notion: {str(e)}"}, status_code=500)

    @app.post("/api/notion/update-status")
    async def notion_update_status(request: Request):
        _require_api(request)
        data = {}
        with suppress(Exception):
            data = await request.json()

        case_page_ids = list(data.get("case_page_ids", []))
        cases = data.get("cases", [])
        target_status = data.get("target_status", "Dispatched to Laboratory")

        if not case_page_ids and cases:
            for c in cases:
                for cid in c.get("case_page_ids", []):
                    if cid and cid not in case_page_ids:
                        case_page_ids.append(cid)

        if not case_page_ids:
            return {
                "success": True,
                "message": "No Notion case IDs provided to update.",
                "total": 0,
                "updated": 0,
                "failed": 0,
                "failed_cases": [],
            }

        ip = request.client.host if request.client else "127.0.0.1"
        if is_rate_limited(ip, "notion_update_status", limit=30, window_seconds=60):
            return JSONResponse({"error": "Update rate limit exceeded. Please wait a moment."}, status_code=429)

        try:
            result = await asyncio.to_thread(
                update_case_status_in_notion,
                case_page_ids=case_page_ids,
                target_status=target_status,
            )
            return JSONResponse({"success": result.get("failed", 0) == 0, **result})
        except Exception as e:
            logger.exception("Failed to update Notion statuses")
            return JSONResponse({"error": f"Failed to update Notion statuses: {str(e)}"}, status_code=500)

    @app.post("/api/export")
    async def manifest_export(request: Request):
        _require_api(request)
        data = await request.json()
        if not data or "cases" not in data:
            return JSONResponse({"error": "Missing cases data"}, status_code=400)

        cases = data["cases"]
        allow_unverified = data.get("allow_unverified", False)
        if not allow_unverified:
            unverified_tags = [
                c.get("bdo_tag") or "Unnamed Case"
                for c in cases
                if c.get("ready_for_manifest") is False
                or "⚠️" in str(c.get("test_type", ""))
                or "CHECK:" in str(c.get("test_type", ""))
            ]
            if unverified_tags:
                return JSONResponse(
                    {
                        "error": (
                            f"Export blocked: {len(unverified_tags)} unverified case(s) detected "
                            f"({', '.join(unverified_tags[:3])}{'...' if len(unverified_tags) > 3 else ''}). "
                            f"Please verify in Notion before exporting."
                        ),
                        "unverified_tags": unverified_tags,
                    },
                    status_code=422,
                )

        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                await asyncio.to_thread(create_excel_manifest, cases, tmp_path)
                with open(tmp_path, "rb") as f:
                    excel_bytes = f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            return Response(
                content=excel_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": 'attachment; filename="finalised_manifest.xlsx"'},
            )
        except Exception as e:
            logger.exception("Failed to generate Excel")
            return JSONResponse({"error": f"Failed to generate Excel: {str(e)}"}, status_code=500)

    @app.post("/api/parse")
    async def manifest_parse_docx(request: Request, file: UploadFile = File(...)):
        _require_api(request)
        if not file.filename.lower().endswith(".docx"):
            return JSONResponse({"error": "File must be in DOCX format"}, status_code=400)

        try:
            content = await file.read()
            file_stream = io.BytesIO(content)
            result = await asyncio.to_thread(parse_docx, file_stream)
            cases = result.get("cases", [])
            docx_rows = result.get("row_count", 0)
            return {"cases": cases, "docx_rows": docx_rows, "parsed_rows": len(cases)}
        except Exception as e:
            logger.exception("Failed to parse DOCX")
            return JSONResponse({"error": f"Failed to parse DOCX: {str(e)}"}, status_code=500)

    # =========================================================================
    # REPORT BOT API ENDPOINTS (100% UNTOUCHED BUSINESS LOGIC)
    # =========================================================================

    @app.get("/api/settings")
    async def get_preferences(request: Request):
        _require_api(request)
        return app.state.preferences.read().model_dump(mode="json")

    @app.put("/api/settings")
    async def set_preferences(request: Request, preferences: AppPreferences):
        _require_mutation(request)
        return app.state.preferences.write(preferences).model_dump(mode="json")

    @app.post("/api/jobs", status_code=status.HTTP_201_CREATED)
    async def create_job(
        request: Request,
        files: Annotated[list[UploadFile], File()],
        review_mode: Annotated[ReviewMode, Form()],
        default_case_type: Annotated[CaseType, Form()] = CaseType.ISSUE,
        lab_source: Annotated[LabSource, Form()] = LabSource.EUROFINS,
        case_types: Annotated[list[CaseType] | None, Form()] = None,
        auto_upload_notion: Annotated[bool, Form()] = False,
    ):
        _require_mutation(request)
        if not files:
            raise HTTPException(
                status_code=400,
                detail="Upload between 1 and 25 PDF reports or a ZIP archive",
            )

        # 1. Unpack incoming files (supporting PDFs, ZIP archives, or a mix)
        extracted_entries: list[tuple[str, bytes, CaseType]] = []
        skipped_non_pdfs: list[str] = []

        for file_index, upload in enumerate(files):
            browser_filename = (upload.filename or "upload").replace("\\", "/")
            orig_name = Path(browser_filename).name
            upload_content = await upload.read(application_settings.max_batch_bytes + 1)
            await upload.close()

            is_zip = orig_name.lower().endswith(".zip") or upload_content.startswith(b"PK\x03\x04")

            if is_zip:
                try:
                    with zipfile.ZipFile(io.BytesIO(upload_content)) as archive:
                        for member in archive.infolist():
                            # Skip directories
                            if member.is_dir() or member.filename.endswith("/"):
                                continue
                            raw_name = Path(member.filename.replace("\\", "/")).name
                            # Skip macOS metadata and hidden system files
                            if (
                                member.filename.startswith("__MACOSX/")
                                or raw_name.startswith("._")
                                or raw_name == ".DS_Store"
                            ):
                                continue

                            # Check if PDF
                            if raw_name.lower().endswith(".pdf"):
                                if member.file_size > application_settings.max_file_bytes:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f"File '{raw_name}' in ZIP exceeds the 25 MB file limit",
                                    )
                                pdf_bytes = archive.read(member)
                                item_case_type = (
                                    case_types[file_index]
                                    if case_types is not None and file_index < len(case_types)
                                    else default_case_type
                                )
                                extracted_entries.append((raw_name, pdf_bytes, item_case_type))
                            else:
                                skipped_non_pdfs.append(raw_name)
                except zipfile.BadZipFile:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Corrupted or invalid ZIP archive: '{orig_name}'",
                    )
                except RuntimeError as exc:
                    if "password" in str(exc).lower():
                        raise HTTPException(
                            status_code=400,
                            detail=f"ZIP archive '{orig_name}' is password protected.",
                        )
                    raise HTTPException(
                        status_code=400,
                        detail=f"Error reading ZIP archive '{orig_name}': {exc}",
                    )
            elif orig_name.lower().endswith(".pdf"):
                item_case_type = (
                    case_types[file_index]
                    if case_types is not None and file_index < len(case_types)
                    else default_case_type
                )
                extracted_entries.append((orig_name, upload_content, item_case_type))
            else:
                skipped_non_pdfs.append(orig_name)

        if not extracted_entries:
            if skipped_non_pdfs:
                raise HTTPException(
                    status_code=400,
                    detail=f"The uploaded archive contains no readable PDF reports. (Skipped: {', '.join(skipped_non_pdfs[:5])})",
                )
            raise HTTPException(
                status_code=400,
                detail=f"Upload between 1 and {application_settings.max_batch_files} PDF files or a ZIP archive",
            )

        if len(extracted_entries) > application_settings.max_batch_files:
            raise HTTPException(
                status_code=400,
                detail=f"The uploaded files contain {len(extracted_entries)} PDF reports, which exceeds the limit of {application_settings.max_batch_files}.",
            )

        job = JobRecord(
            lab_source=lab_source,
            review_mode=review_mode,
            default_case_type=default_case_type,
            auto_upload_notion=auto_upload_notion,
            expires_at=app.state.job_store.new_expiry(),
        )
        app.state.job_store.create(job)
        job_dir = app.state.job_store.job_directory(job.id)
        total_bytes = 0
        seen_digests: set[str] = set()

        try:
            for original_filename, content, item_case_type in extracted_entries:
                item = JobItem(
                    original_filename=original_filename,
                    stored_filename="",
                    source_path=job_dir,
                    case_type=item_case_type,
                )
                job.items.append(item)
                try:
                    if len(original_filename) > 255:
                        raise ReportParseError("Filename exceeds the supported length")
                    if Path(original_filename).suffix.lower() != ".pdf":
                        raise ReportParseError("Only PDF files are supported")
                    total_bytes += len(content)
                    if len(content) > application_settings.max_file_bytes:
                        raise ReportParseError("PDF exceeds the 25 MB file limit")
                    if total_bytes > application_settings.max_batch_bytes:
                        raise ReportParseError("Batch exceeds the total upload size limit")
                    digest = hashlib.sha256(content).hexdigest()
                    if digest in seen_digests:
                        raise ReportParseError("Duplicate PDF file in this batch")
                    seen_digests.add(digest)

                    item.stored_filename = f"source-{item.id}.pdf"
                    item.source_path = job_dir / item.stored_filename
                    item.source_path.write_bytes(content)
                    item.source_path.chmod(0o600)
                    item.report = parse_report(
                        job.lab_source,
                        content,
                        original_filename,
                        max_file_bytes=application_settings.max_file_bytes,
                    )
                    item.state = ItemState.NEEDS_REVIEW
                except ReportParseError as exc:
                    item.state = ItemState.FAILED
                    item.parse_error = str(exc)

            if skipped_non_pdfs:
                unique_skipped = list(dict.fromkeys(skipped_non_pdfs))
                skip_msg = f"⚠️ Skipped non-PDF files from ZIP: {', '.join(unique_skipped[:10])}{' ...' if len(unique_skipped) > 10 else ''}"
                for item in job.items:
                    if item.report and skip_msg not in item.report.warnings:
                        item.report.warnings.append(skip_msg)

            # Run integrity, layout anomaly detection & Notion cross-verification
            parsed_items = [item for item in job.items if item.report and item.state != ItemState.FAILED]
            if parsed_items:
                case_codes = [item.report.case_code for item in parsed_items]
                unique_codes = list(dict.fromkeys(case_codes))
                notion_data_map = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(unique_codes))) as executor:
                    future_to_code = {
                        executor.submit(fetch_notion_verification_data, code): code
                        for code in unique_codes
                    }
                    for future in concurrent.futures.as_completed(future_to_code):
                        c = future_to_code[future]
                        try:
                            notion_data_map[c] = future.result()
                        except Exception as e:
                            logger.warning("Notion verification failed for %s: %s", c, e)
                            notion_data_map[c] = {"notion_available": False, "found": False}

                for item in parsed_items:
                    source_content = item.source_path.read_bytes()
                    # 1. Layout anomaly detection
                    anomalies = detect_layout_anomalies(
                        source_content,
                        job.lab_source,
                        item.case_type,
                        item.report.test_type,
                    )
                    item.layout_warnings = anomalies
                    if anomalies:
                        item.needs_manual_review = True
                        for warning in anomalies:
                            if warning not in item.report.warnings:
                                item.report.warnings.append(f"⚠️ {warning}")

                    # 2. Dual 'AND' auto-classification
                    n_data = notion_data_map.get(item.report.case_code)
                    if n_data and n_data.get("case_page_id"):
                        item.notion_page_id = n_data["case_page_id"]
                    classification = determine_report_classification(
                        item.report.case_code,
                        n_data,
                        default_case_type,
                    )
                    item.classification_info = classification
                    item.case_type = classification["case_type"]
                    if classification.get("needs_manual_review"):
                        item.needs_manual_review = True
                        reason = classification.get("reason")
                        if reason and reason not in item.report.warnings:
                            item.report.warnings.append(f"⚠️ {reason}")

                    # 2b. Dual-source test type reconciliation (Notion + report participant roles)
                    test_type_info = determine_test_type(
                        item.report.test_type,
                        item.report.participants,
                        n_data,
                    )
                    item.report.test_type = test_type_info["test_type"]
                    if test_type_info.get("needs_manual_review"):
                        item.needs_manual_review = True
                        tt_reason = test_type_info.get("reason")
                        if tt_reason and tt_reason not in item.report.warnings:
                            item.report.warnings.append(f"⚠️ {tt_reason}")
                    elif test_type_info.get("reason"):
                        notice = f"ℹ️ {test_type_info.get('reason')}"
                        if notice not in item.report.warnings:
                            item.report.warnings.append(notice)

                    # 3. Participant cross-verification against Notion Participant DB
                    n_parts = n_data.get("participants", []) if n_data else []

                    # Auto-enrich missing participant DOBs (e.g. UFC reports) from Notion records
                    if n_parts and item.report:
                        for ext in item.report.participants:
                            if not ext.date_of_birth or ext.date_of_birth == "-":
                                norm_ext_name = re.sub(r"[^A-Z]", "", ext.full_name.upper())
                                ext_role_clean = ext.role.upper().replace("ALLEGED ", "").strip()
                                for n_part in n_parts:
                                    norm_notion_name = re.sub(r"[^A-Z]", "", str(n_part.get("name", "")).upper())
                                    notion_role_clean = str(n_part.get("role", "")).upper().replace("ALLEGED ", "").strip()
                                    name_matched = (
                                        bool(norm_ext_name and norm_notion_name)
                                        and (norm_ext_name == norm_notion_name or norm_ext_name in norm_notion_name or norm_notion_name in norm_ext_name)
                                    )
                                    role_matched = bool(ext_role_clean and notion_role_clean and ext_role_clean == notion_role_clean)
                                    if name_matched or role_matched:
                                        n_dob = n_part.get("dob")
                                        if n_dob and n_dob != "-":
                                            ext.date_of_birth = n_dob
                                            break

                    part_check = cross_verify_participants(item.report.participants, n_parts)
                    item.notion_verification = part_check
                    if not part_check.get("verified"):
                        item.needs_manual_review = True
                        for disc in part_check.get("discrepancies", []):
                            disc_msg = f"⚠️ Notion Discrepancy: {disc}"
                            if disc_msg not in item.report.warnings:
                                item.report.warnings.append(disc_msg)

            _duplicate_case_warnings(job)
            if review_mode == ReviewMode.AUTO_APPROVE:
                for item in job.items:
                    has_shared_case = any(
                        "Shared case reference" in w
                        for w in (item.report.warnings if item.report else [])
                    )
                    if (
                        item.report
                        and item.state != ItemState.FAILED
                        and not item.report.blocking_errors
                        and not has_shared_case
                        and item.report.confidence >= 0.9
                        and not item.needs_manual_review
                        and not item.layout_warnings
                    ):
                        item.approved = True
                        item.state = ItemState.APPROVED
                if any(item.approved for item in job.items):
                    job.state = JobState.READY
                    app.state.job_store.save(job)
                    _schedule_processing(app, job.id)
                else:
                    job.state = JobState.AWAITING_REVIEW
                    app.state.job_store.save(job)
            else:
                job.state = JobState.AWAITING_REVIEW
                app.state.job_store.save(job)
            return _public_job(job)
        except Exception:
            if not job.items:
                app.state.job_store.cleanup_expired()
            raise

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        _require_api(request)
        return _public_job(_get_job(request, job_id))

    @app.patch("/api/jobs/{job_id}/items/{item_id}")
    async def update_item(request: Request, job_id: str, item_id: str, update: ItemUpdate):
        _require_mutation(request)
        job = _get_job(request, job_id)
        if job.state == JobState.PROCESSING:
            raise HTTPException(status_code=409, detail="Job is already processing")
        item = _find_item(job, item_id)
        if not item.report:
            raise HTTPException(status_code=409, detail="This report could not be parsed")

        participants: list[Participant] = []
        for index, participant in enumerate(update.participants):
            participant_id = participant.id
            if not participant_id and index < len(item.report.participants):
                participant_id = item.report.participants[index].id
            participant_kwargs = {
                "sample_number": participant.sample_number,
                "role": participant.role,
                "full_name": participant.full_name,
                "date_of_birth": participant.date_of_birth,
                "source_order": participant.source_order,
            }
            participants.append(
                Participant(id=participant_id, **participant_kwargs)
                if participant_id
                else Participant(**participant_kwargs)
            )
        warnings = role_combination_warnings(update.test_type, participants)
        if any(
            participant.date_of_birth == "-" or participant.date_of_birth.startswith("--/--/")
            for participant in participants
        ):
            warnings.append("One or more participants have no date of birth in the source report")
        report = ReportData(
            case_code=update.case_code,
            collection_date=update.collection_date,
            report_date=update.report_date,
            test_type=update.test_type,
            participants=participants,
            warnings=warnings,
            blocking_errors=[],
            confidence=1.0,
        )
        report.blocking_errors = _validation_errors(report)
        item.case_type = update.case_type
        item.report = report
        item.approved = False
        item.state = ItemState.NEEDS_REVIEW
        item.needs_manual_review = False
        _duplicate_case_warnings(job)
        app.state.job_store.save(job)
        return _public_item(item)

    @app.post("/api/jobs/{job_id}/items/{item_id}/approve")
    async def approve_item(request: Request, job_id: str, item_id: str):
        _require_mutation(request)
        job = _get_job(request, job_id)
        if job.state == JobState.PROCESSING:
            raise HTTPException(status_code=409, detail="Job is already processing")
        item = _find_item(job, item_id)
        if not item.report:
            raise HTTPException(status_code=409, detail="This report could not be parsed")
        item.report.blocking_errors = _validation_errors(item.report)
        _duplicate_case_warnings(job)
        if item.case_type == CaseType.ISSUE:
            item.approved = False
            item.state = ItemState.NEEDS_REVIEW
            app.state.job_store.save(job)
            raise HTTPException(
                status_code=422,
                detail="Report type must be set to either Personal or Certified / Legal before approval.",
            )
        if item.report.blocking_errors:
            item.approved = False
            item.state = ItemState.NEEDS_REVIEW
            app.state.job_store.save(job)
            raise HTTPException(
                status_code=422,
                detail="Correct the blocking report errors before approval",
            )
        item.approved = True
        item.state = ItemState.APPROVED
        job.state = JobState.READY
        app.state.job_store.save(job)
        return _public_item(item)

    @app.post("/api/jobs/{job_id}/process", status_code=status.HTTP_202_ACCEPTED)
    async def process_job(request: Request, job_id: str):
        _require_mutation(request)
        job = _get_job(request, job_id)
        if job.state == JobState.PROCESSING:
            raise HTTPException(status_code=409, detail="Job is already processing")
        if not any(item.approved and item.report for item in job.items):
            raise HTTPException(status_code=422, detail="Approve at least one report first")
        job.state = JobState.PROCESSING
        app.state.job_store.save(job)
        _schedule_processing(app, job.id)
        return _public_job(job)

    @app.get("/api/jobs/{job_id}/download")
    async def download_job(request: Request, job_id: str):
        _require_api(request)
        job = _get_job(request, job_id)
        if not job.zip_path or not job.zip_path.exists():
            raise HTTPException(status_code=409, detail="Download is not ready")
        return FileResponse(
            job.zip_path,
            media_type="application/zip",
            filename="Blueprint-Reports.zip",
        )

    @app.get("/api/jobs/{job_id}/items/{item_id}/download")
    async def download_item(request: Request, job_id: str, item_id: str):
        _require_api(request)
        job = _get_job(request, job_id)
        item = _find_item(job, item_id)
        if not item.output_path or not item.output_path.exists():
            raise HTTPException(status_code=404, detail="Processed file not found")
        return FileResponse(
            item.output_path,
            media_type="application/pdf",
            filename=item.output_filename or f"{item.id}.pdf",
        )

    @app.post("/api/jobs/{job_id}/sync-notion-status")
    async def sync_job_notion_status(request: Request, job_id: str):
        _require_api(request)
        job = _get_job(request, job_id)
        # Collect case codes: prioritize completed items, fallback to any parsed items in batch
        codes_to_sync = [
            item.report.case_code.strip()
            for item in job.items
            if item.state == ItemState.COMPLETE and item.report and item.report.case_code and item.report.case_code.strip()
        ]
        if not codes_to_sync:
            codes_to_sync = [
                item.report.case_code.strip()
                for item in job.items
                if item.report and item.report.case_code and item.report.case_code.strip()
            ]

        if not codes_to_sync:
            return JSONResponse(
                {
                    "success": False,
                    "message": "No case codes found in this batch to sync with Notion.",
                    "total": 0,
                    "updated": 0,
                    "updated_count": 0,
                    "failed": 0,
                    "unmatched": 0,
                    "target_status": "Result Review",
                    "case_codes": [],
                    "results": [],
                    "unmatched_codes": [],
                },
                status_code=400,
            )

        try:
            result = await asyncio.to_thread(
                update_cases_by_codes,
                case_codes=codes_to_sync,
                target_status="Result Review",
            )
            updated_count = result.get("updated", 0)
            return JSONResponse({
                "job_id": job.id,
                "success": result.get("failed", 0) == 0 and updated_count > 0,
                "updated_count": updated_count,
                "case_codes": codes_to_sync,
                **result,
            })
        except Exception as e:
            logger.exception("Failed to sync case statuses to Notion", extra={"job_id": job.id})
            return JSONResponse(
                {"error": f"Failed to sync with Notion: {str(e)}"},
                status_code=500,
            )

    @app.post("/api/jobs/{job_id}/upload-notion-reports")
    async def upload_job_notion_reports(request: Request, job_id: str):
        _require_api(request)
        job = _get_job(request, job_id)
        
        eligible_items = [
            item for item in job.items
            if item.state == ItemState.COMPLETE
            and item.output_path
            and item.output_path.exists()
            and item.report
            and item.report.case_code
        ]
        
        if not eligible_items:
            return JSONResponse(
                {
                    "success": False,
                    "message": "No finalised reports available to upload.",
                    "total": 0,
                    "uploaded": 0,
                    "failed": 0,
                    "results": [],
                },
                status_code=400,
            )

        items_data = []
        for item in eligible_items:
            items_data.append({
                "item_id": item.id,
                "case_code": item.report.case_code,
                "filename": item.output_filename or f"{item.report.case_code}.pdf",
                "pdf_bytes": item.output_path.read_bytes(),
                "page_id": item.notion_page_id,
            })
            item.notion_upload_status = "UPLOADING"
            item.notion_upload_error = None

        app.state.job_store.save(job)

        try:
            res = await asyncio.to_thread(
                upload_batch_reports_to_notion,
                items_data=items_data,
                update_status=True,
                target_status="Result Review",
            )
            result_map = {r.get("item_id"): r for r in res.get("results", [])}
            for item in eligible_items:
                r = result_map.get(item.id)
                if r and r.get("success"):
                    item.notion_upload_status = "UPLOADED"
                    item.notion_upload_error = None
                    if r.get("page_id") and not item.notion_page_id:
                        item.notion_page_id = r.get("page_id")
                elif r:
                    item.notion_upload_status = "FAILED"
                    item.notion_upload_error = r.get("error", "Upload failed")
                else:
                    item.notion_upload_status = "FAILED"
                    item.notion_upload_error = "Upload failed"

            app.state.job_store.save(job)
            return JSONResponse({
                "job_id": job.id,
                "success": res.get("failed", 0) == 0 and res.get("uploaded", 0) > 0,
                **res,
            })
        except Exception as e:
            logger.exception("Failed to upload reports to Notion", extra={"job_id": job.id})
            for item in eligible_items:
                if item.notion_upload_status == "UPLOADING":
                    item.notion_upload_status = "FAILED"
                    item.notion_upload_error = str(e)
            app.state.job_store.save(job)
            return JSONResponse(
                {"error": f"Failed to upload reports to Notion: {str(e)}"},
                status_code=500,
            )

    @app.post("/api/jobs/{job_id}/items/{item_id}/upload-notion-report")
    async def upload_item_notion_report(request: Request, job_id: str, item_id: str):
        _require_api(request)
        job = _get_job(request, job_id)
        item = _find_item(job, item_id)
        
        if item.state != ItemState.COMPLETE or not item.output_path or not item.output_path.exists():
            raise HTTPException(status_code=400, detail="Report must be completed before uploading to Notion")
        if not item.report or not item.report.case_code:
            raise HTTPException(status_code=400, detail="Report case code is missing")

        item.notion_upload_status = "UPLOADING"
        item.notion_upload_error = None
        app.state.job_store.save(job)

        try:
            filename = item.output_filename or f"{item.report.case_code}.pdf"
            pdf_bytes = item.output_path.read_bytes()
            res = await asyncio.to_thread(
                upload_single_case_report,
                case_code=item.report.case_code,
                pdf_bytes=pdf_bytes,
                filename=filename,
                page_id=item.notion_page_id,
                update_status=True,
                target_status="Result Review",
            )
            if res.get("success"):
                item.notion_upload_status = "UPLOADED"
                item.notion_upload_error = None
                if res.get("page_id"):
                    item.notion_page_id = res.get("page_id")
            else:
                item.notion_upload_status = "FAILED"
                item.notion_upload_error = res.get("error", "Upload failed")
            app.state.job_store.save(job)
            return JSONResponse({"item_id": item.id, **res})
        except Exception as e:
            logger.exception("Single report upload to Notion failed", extra={"item_id": item.id})
            item.notion_upload_status = "FAILED"
            item.notion_upload_error = str(e)
            app.state.job_store.save(job)
            return JSONResponse(
                {"success": False, "item_id": item.id, "error": str(e)},
                status_code=500,
            )

    return app

app = create_app()

def start_server():
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)

if __name__ == "__main__":
    start_server()

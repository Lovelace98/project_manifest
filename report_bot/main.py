from __future__ import annotations

import asyncio
import hashlib
import logging
import time
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
from report_bot.security import (
    LoginThrottle,
    client_key,
    new_csrf_token,
    password_matches,
    require_session,
    validate_csrf,
)
from report_bot.store import JobNotFoundError, JobStore

logger = logging.getLogger(__name__)
REPORT_BOT_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=REPORT_BOT_DIR / "templates")
def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _require_api(request: Request) -> None:
    require_session(request, _settings(request))


def _require_mutation(request: Request) -> None:
    _require_api(request)
    validate_csrf(request)


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
    }


def _public_job(job: JobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "lab_source": job.lab_source.value,
        "review_mode": job.review_mode.value,
        "default_case_type": job.default_case_type.value,
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
        title=application_settings.app_name,
        version="1.0.0",
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
    app.state.login_throttle = LoginThrottle()
    app.state.processing_tasks = set()
    app.state.processing_semaphore = asyncio.Semaphore(1)
    app.add_middleware(
        SessionMiddleware,
        secret_key=application_settings.session_secret,
        session_cookie="blueprint_report_session",
        max_age=application_settings.session_absolute_seconds,
        same_site="strict",
        https_only=application_settings.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=REPORT_BOT_DIR / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/jobs":
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    request_bytes = int(content_length)
                except ValueError:
                    return JSONResponse(status_code=400, content={"detail": "Invalid request size"})
                multipart_allowance = 1024 * 1024
                if request_bytes > application_settings.max_batch_bytes + multipart_allowance:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Batch exceeds the total upload size limit"},
                    )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if application_settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(JobNotFoundError)
    async def missing_job_handler(_request: Request, _exc: JobNotFoundError):
        return JSONResponse(status_code=404, content={"detail": "Job not found or expired"})

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/brand/logo", include_in_schema=False)
    async def brand_logo() -> Response:
        if not application_settings.logo_path.exists():
            raise HTTPException(status_code=404, detail="Logo unavailable")
        return Response(
            content=transparent_logo_png(application_settings.logo_path),
            media_type="image/png",
        )

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse(url="/", status_code=303)
        csrf_token = request.session.get("login_csrf")
        if not csrf_token:
            csrf_token = new_csrf_token()
            request.session["login_csrf"] = csrf_token
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf_token, "error": None},
        )

    @app.post("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(
        request: Request,
        password: Annotated[str, Form(min_length=1, max_length=256)],
        csrf_token: Annotated[str, Form()],
    ):
        expected_csrf = str(request.session.get("login_csrf", ""))
        key = client_key(request)
        if not expected_csrf or not secrets_compare(expected_csrf, csrf_token):
            refreshed_csrf = new_csrf_token()
            request.session["login_csrf"] = refreshed_csrf
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "csrf_token": refreshed_csrf,
                    "error": "Your sign-in session expired. Please enter the password again.",
                },
                status_code=401,
            )
        if not app.state.login_throttle.allowed(key):
            error = "Too many sign-in attempts. Try again later."
        elif not password_matches(password, application_settings):
            app.state.login_throttle.failure(key)
            error = "The password is incorrect."
        else:
            app.state.login_throttle.success(key)
            now = int(time.time())
            request.session.clear()
            request.session.update(
                {
                    "authenticated": True,
                    "issued_at": now,
                    "last_seen": now,
                    "csrf_token": new_csrf_token(),
                }
            )
            return RedirectResponse(url="/", status_code=303)
        refreshed_csrf = new_csrf_token()
        request.session["login_csrf"] = refreshed_csrf
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": refreshed_csrf, "error": error},
            status_code=401 if "incorrect" in error else 429,
        )

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        _require_mutation(request)
        request.session.clear()
        return JSONResponse({"ok": True})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def workspace(request: Request):
        try:
            _require_api(request)
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)
        preferences = app.state.preferences.read()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "csrf_token": request.session["csrf_token"],
                "default_review_mode": preferences.default_review_mode.value,
                "max_batch_files": application_settings.max_batch_files,
            },
        )

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
        default_case_type: Annotated[CaseType, Form()],
        lab_source: Annotated[LabSource, Form()] = LabSource.EUROFINS,
        case_types: Annotated[list[CaseType] | None, Form()] = None,
    ):
        _require_mutation(request)
        if not files or len(files) > application_settings.max_batch_files:
            raise HTTPException(
                status_code=400,
                detail=f"Upload between 1 and {application_settings.max_batch_files} PDF files",
            )
        if case_types is not None and len(case_types) != len(files):
            raise HTTPException(
                status_code=400,
                detail="Every uploaded PDF must have one report type",
            )

        job = JobRecord(
            lab_source=lab_source,
            review_mode=review_mode,
            default_case_type=default_case_type,
            expires_at=app.state.job_store.new_expiry(),
        )
        app.state.job_store.create(job)
        job_dir = app.state.job_store.job_directory(job.id)
        total_bytes = 0
        seen_digests: set[str] = set()

        try:
            for file_index, upload in enumerate(files):
                browser_filename = (upload.filename or "report.pdf").replace("\\", "/")
                original_filename = Path(browser_filename).name
                item_case_type = (
                    case_types[file_index] if case_types is not None else default_case_type
                )
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
                    content = await upload.read(application_settings.max_file_bytes + 1)
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
                finally:
                    await upload.close()

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

    return app


def secrets_compare(expected: str, supplied: str) -> bool:
    import secrets

    return secrets.compare_digest(expected, supplied)


app = create_app()

"""HTTP layer (FastAPI)."""
import io
import os
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import service
from app.config import Settings
from app.database import make_session_factory
from app.models import Certificate, CertificateStatus, Job
from app.schemas import CertificateOut, FailureOut, JobCreate, JobOut

FAILURE_PREVIEW_LIMIT = 100
FRONTEND_FILE = Path(__file__).parent / "static" / "index.html"


def _slug(name: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_") or "certificate"


def _certificate_out(cert: Certificate) -> CertificateOut:
    out = CertificateOut.model_validate(cert)
    if cert.status == CertificateStatus.GENERATED.value:
        out.download_url = f"/certificates/{cert.id}/download"
    return out


def _job_out(session: Session, job: Job) -> JobOut:
    counts = service.status_counts(session, job.id)
    generated = counts[CertificateStatus.GENERATED.value]
    failed = counts[CertificateStatus.FAILED.value]
    pending = counts[CertificateStatus.PENDING.value]
    done = generated + failed
    failures = session.scalars(
        select(Certificate)
        .where(
            Certificate.job_id == job.id,
            Certificate.status == CertificateStatus.FAILED.value,
        )
        .order_by(Certificate.position)
        .limit(FAILURE_PREVIEW_LIMIT)
    ).all()
    return JobOut(
        id=job.id,
        title=job.title,
        issued_by=job.issued_by,
        issue_date=job.issue_date,
        status=job.status,
        total=job.total,
        generated=generated,
        failed=failed,
        pending=pending,
        progress_percent=round(100 * done / job.total, 1) if job.total else 100.0,
        created_at=job.created_at,
        completed_at=job.completed_at,
        failures=[
            FailureOut(
                position=f.position,
                recipient_name=f.recipient_name,
                recipient_email=f.recipient_email,
                error=f.error,
            )
            for f in failures
        ],
        download_all_url=f"/jobs/{job.id}/download" if generated else None,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    os.makedirs(settings.storage_dir, exist_ok=True)
    session_factory = make_session_factory(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Pick up jobs interrupted by a previous shutdown, without blocking startup.
        threading.Thread(
            target=service.resume_unfinished_jobs,
            args=(session_factory, settings.storage_dir),
            daemon=True,
        ).start()
        yield

    app = FastAPI(title="Bulk Certificate Generator", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory

    def get_session():
        with session_factory() as session:
            yield session

    def get_job_or_404(session: Session, job_id: str) -> Job:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/", include_in_schema=False)
    def frontend():
        """The single-page web UI (plain HTML + JS, talks to the API below)."""
        return FileResponse(FRONTEND_FILE, media_type="text/html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/jobs", response_model=JobOut, status_code=202)
    def create_job(
        payload: JobCreate,
        background_tasks: BackgroundTasks,
        session: Session = Depends(get_session),
    ):
        """Accept a bulk request. Returns immediately (202) with a job id;
        generation continues in the background."""
        if len(payload.recipients) > settings.max_recipients:
            raise HTTPException(
                status_code=422,
                detail=f"Too many recipients: max {settings.max_recipients} per job",
            )
        job = service.create_job(session, payload)
        background_tasks.add_task(
            service.process_job, session_factory, settings.storage_dir, job.id
        )
        return _job_out(session, job)

    @app.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, session: Session = Depends(get_session)):
        """Status + progress + counts + preview of failures."""
        return _job_out(session, get_job_or_404(session, job_id))

    @app.get("/jobs/{job_id}/certificates")
    def list_certificates(
        job_id: str,
        status: str | None = Query(None, description="PENDING, GENERATED or FAILED"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: Session = Depends(get_session),
    ):
        """Paginated per-recipient results, optionally filtered by status."""
        job = get_job_or_404(session, job_id)
        query = select(Certificate).where(Certificate.job_id == job.id)
        if status is not None:
            status = status.upper()
            if status not in {s.value for s in CertificateStatus}:
                raise HTTPException(status_code=422, detail="Invalid status filter")
            query = query.where(Certificate.status == status)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(
            query.order_by(Certificate.position).limit(limit).offset(offset)
        ).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_certificate_out(c) for c in items],
        }

    @app.get("/certificates/{certificate_id}/download")
    def download_certificate(
        certificate_id: str, session: Session = Depends(get_session)
    ):
        cert = session.get(Certificate, certificate_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="Certificate not found")
        if cert.status != CertificateStatus.GENERATED.value:
            raise HTTPException(
                status_code=409,
                detail=f"Certificate is {cert.status}, not available for download",
            )
        if not cert.file_path or not os.path.exists(cert.file_path):
            raise HTTPException(status_code=404, detail="Certificate file missing")
        return FileResponse(
            cert.file_path,
            media_type="application/pdf",
            filename=f"certificate_{_slug(cert.recipient_name)}.pdf",
        )

    @app.get("/jobs/{job_id}/download")
    def download_job_zip(job_id: str, session: Session = Depends(get_session)):
        """All successfully generated certificates of a job as one ZIP."""
        job = get_job_or_404(session, job_id)
        certs = session.scalars(
            select(Certificate)
            .where(
                Certificate.job_id == job.id,
                Certificate.status == CertificateStatus.GENERATED.value,
            )
            .order_by(Certificate.position)
        ).all()
        if not certs:
            raise HTTPException(
                status_code=409, detail="No generated certificates for this job yet"
            )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for cert in certs:
                if cert.file_path and os.path.exists(cert.file_path):
                    zf.write(
                        cert.file_path,
                        arcname=f"{cert.position + 1:04d}_{_slug(cert.recipient_name)}.pdf",
                    )
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="certificates_{job.id}.zip"'
            },
        )

    return app

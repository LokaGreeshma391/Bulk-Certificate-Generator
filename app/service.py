"""Business logic: creating jobs, generating certificates, tracking status.

Kept separate from the HTTP layer (main.py) so it can be tested directly and
reused if the processing moves to a real task queue later.
"""
import logging
import os
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import certificate
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, Recipient

logger = logging.getLogger(__name__)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "recipient"
        parts.append(f"{field}: {err['msg']}")
    return "Invalid recipient - " + "; ".join(parts)


def _safe_str(value, limit: int) -> str | None:
    """Best-effort text from untrusted input, just for display in status."""
    return value[:limit] if isinstance(value, str) else None


def create_job(session: Session, payload: JobCreate) -> Job:
    """Store the job and one row per recipient.

    Each recipient is validated on its own. Invalid ones are stored as FAILED
    immediately (with the reason); valid ones are stored as PENDING and picked
    up by process_job().
    """
    job = Job(
        title=payload.title,
        issued_by=payload.issued_by,
        issue_date=payload.issue_date.isoformat(),
        total=len(payload.recipients),
        status=JobStatus.PENDING.value,
    )
    session.add(job)

    seen_emails: set[str] = set()
    for position, raw in enumerate(payload.recipients):
        cert = Certificate(
            job=job,
            position=position,
            raw_data=raw,
            recipient_name=_safe_str(raw.get("name"), 200),
            recipient_email=_safe_str(raw.get("email"), 320),
        )
        try:
            recipient = Recipient.model_validate(raw)
        except ValidationError as exc:
            cert.status = CertificateStatus.FAILED.value
            cert.error = _format_validation_error(exc)
        else:
            cert.recipient_name = recipient.name
            cert.recipient_email = recipient.email
            key = recipient.email.lower()
            if key in seen_emails:
                cert.status = CertificateStatus.FAILED.value
                cert.error = "Duplicate recipient email within this job"
            else:
                seen_emails.add(key)
                cert.status = CertificateStatus.PENDING.value
        session.add(cert)

    session.commit()
    return job


def status_counts(session: Session, job_id: str) -> dict[str, int]:
    rows = session.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {s.value: 0 for s in CertificateStatus}
    counts.update({status: n for status, n in rows})
    return counts


def _finalize_job(session: Session, job: Job) -> None:
    counts = status_counts(session, job.id)
    if counts[CertificateStatus.FAILED.value] == 0:
        job.status = JobStatus.COMPLETED.value
    elif counts[CertificateStatus.GENERATED.value] == 0:
        job.status = JobStatus.FAILED.value
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS.value
    job.completed_at = datetime.now(timezone.utc)
    session.commit()


def process_job(session_factory, storage_dir: str, job_id: str) -> None:
    """Generate a PDF for every PENDING certificate of the job.

    Runs in the background. It opens its own DB session (the request's session
    is already closed by then). Each certificate is committed separately, so:
      * clients polling the status see live progress, and
      * one failing certificate never affects the others.
    Safe to run again: only PENDING certificates are touched.
    """
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.warning("process_job: job %s not found", job_id)
            return
        try:
            job.status = JobStatus.PROCESSING.value
            session.commit()

            job_dir = os.path.join(storage_dir, job_id)
            os.makedirs(job_dir, exist_ok=True)

            pending = session.scalars(
                select(Certificate)
                .where(
                    Certificate.job_id == job_id,
                    Certificate.status == CertificateStatus.PENDING.value,
                )
                .order_by(Certificate.position)
            ).all()

            for cert in pending:
                try:
                    pdf_bytes = certificate.render_certificate(
                        recipient_name=cert.recipient_name,
                        title=job.title,
                        issued_by=job.issued_by,
                        issue_date=job.issue_date,
                        certificate_id=cert.id,
                    )
                    path = os.path.join(job_dir, f"{cert.id}.pdf")
                    with open(path, "wb") as fh:
                        fh.write(pdf_bytes)
                    cert.file_path = path
                    cert.status = CertificateStatus.GENERATED.value
                    cert.error = None
                except Exception as exc:  # isolate: one bad cert must not stop the job
                    logger.exception("Certificate %s failed", cert.id)
                    cert.status = CertificateStatus.FAILED.value
                    cert.error = f"Generation failed: {exc}"
                session.commit()

            _finalize_job(session, job)
        except Exception:
            # Something outside a single certificate broke (DB down, disk...).
            logger.exception("Job %s crashed", job_id)
            session.rollback()
            job = session.get(Job, job_id)
            job.status = JobStatus.FAILED.value
            job.completed_at = datetime.now(timezone.utc)
            session.commit()


def resume_unfinished_jobs(session_factory, storage_dir: str) -> None:
    """On startup, continue jobs that were interrupted by a restart.

    Background tasks live in process memory, so a crash/restart would leave
    jobs stuck in PENDING/PROCESSING. This re-runs them (already generated
    certificates are skipped).
    """
    with session_factory() as session:
        ids = session.scalars(
            select(Job.id).where(
                Job.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value])
            )
        ).all()
    for job_id in ids:
        process_job(session_factory, storage_dir, job_id)

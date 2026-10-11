import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"                          # accepted, not started
    PROCESSING = "PROCESSING"                    # worker is generating
    COMPLETED = "COMPLETED"                      # every certificate generated
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"  # some generated, some failed
    FAILED = "FAILED"                            # nothing could be generated


class CertificateStatus(str, enum.Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


class Job(Base):
    """One bulk request: shared certificate info + many recipients."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))       # event / course name
    issued_by: Mapped[str] = mapped_column(String(200))   # organisation
    issue_date: Mapped[str] = mapped_column(String(32))   # ISO date as text
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING.value)
    total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", order_by="Certificate.position"
    )


class Certificate(Base):
    """One recipient inside a job. Failures are stored as rows too, so the
    client can see exactly which recipient failed and why."""

    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # index in the original request
    recipient_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON)    # what the client actually sent
    status: Mapped[str] = mapped_column(
        String(32), default=CertificateStatus.PENDING.value, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped[Job] = relationship(back_populates="certificates")

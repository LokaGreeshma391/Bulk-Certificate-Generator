"""Pydantic schemas.

Two levels of validation on purpose:

* Request level (JobCreate): problems here mean the whole request is
  malformed -> HTTP 422, nothing is stored.
* Recipient level (Recipient): problems here only fail THAT recipient.
  The rest of the job still runs. That is why JobCreate.recipients is a list
  of loose dicts and each one is validated individually in the service.
"""
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Recipient(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr

    @field_validator("name")
    @classmethod
    def name_must_be_printable_latin(cls, v: str) -> str:
        # The template uses the built-in Helvetica font, which only covers
        # Latin characters. Reject others instead of printing broken glyphs.
        try:
            v.encode("cp1252")
        except UnicodeEncodeError:
            raise ValueError(
                "name contains characters the certificate font cannot render"
            )
        if any(ord(c) < 32 for c in v):
            raise ValueError("name contains control characters")
        return v


class JobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200, description="Event or course name")
    issued_by: str = Field(min_length=1, max_length=200)
    issue_date: date = Field(default_factory=date.today)
    recipients: list[dict[str, Any]] = Field(min_length=1)


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    download_url: str | None = None


class FailureOut(BaseModel):
    position: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    title: str
    issued_by: str
    issue_date: str
    status: str
    total: int
    generated: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    completed_at: datetime | None
    # Preview of failures (capped). Full list: GET /jobs/{id}/certificates?status=FAILED
    failures: list[FailureOut] = []
    download_all_url: str | None = None

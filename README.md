# Bulk Certificate Generator

A backend API that accepts **one request containing many recipients**, generates a PDF certificate for each valid recipient from a single predefined template, and lets the client track progress and download the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default, any SQL database via `DATABASE_URL`), ReportLab (PDF), pytest.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run the application

```bash
uvicorn app.main:create_app --factory --reload
```

- **Web UI: http://127.0.0.1:8000** (create a job, watch progress, download results)
- Interactive API docs (try every endpoint in the browser): http://127.0.0.1:8000/docs

### Web UI

A single static page (`app/static/index.html`, plain HTML + JavaScript, no build step) served by the same FastAPI app. It lets you enter the event details, paste recipients as `Name, email` lines or load a CSV, submit the job, watch a live progress bar (it polls `GET /jobs/{id}`), see per-recipient results with failure reasons, filter by status, and download single PDFs or everything as a ZIP. The last job is remembered in the browser, and any job can be reopened by id. The UI only uses the public API described below, so everything it does can also be done with curl.

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `STORAGE_DIR` | `./storage` | Where generated PDFs are saved |
| `MAX_RECIPIENTS` | `1000` | Max recipients per request |

## Run the tests

```bash
pytest -v
```

Each test gets its own temporary database and storage folder.

## API usage

### 1. Submit a bulk request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Python Bootcamp 2026",
    "issued_by": "Acme Academy",
    "issue_date": "2026-10-01",
    "recipients": [
      {"name": "Greeshma Loka", "email": "greeshma@example.com"},
      {"name": "Arjun Rao",     "email": "arjun@example.com"},
      {"name": "Bad Row",       "email": "not-an-email"}
    ]
  }'
```

Returns **202 Accepted** immediately with a job (generation continues in the background):

```json
{ "id": "3f2a...", "status": "PENDING", "total": 3, "generated": 0, "failed": 1, "pending": 2, "progress_percent": 33.3, "...": "..." }
```

`issue_date` is optional (defaults to today).

### 2. Check progress / result

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

Returns the job `status`, counts (`total`, `generated`, `failed`, `pending`), `progress_percent`, and a `failures` list (position, name, email, reason; capped at 100).

Job statuses: `PENDING` → `PROCESSING` → `COMPLETED` (all succeeded) / `COMPLETED_WITH_ERRORS` (some failed) / `FAILED` (none succeeded).

Per-recipient results (paginated, filterable):

```bash
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=FAILED&limit=100&offset=0"
```

`status` is `PENDING`, `GENERATED` or `FAILED` (case-insensitive).

### 3. Retrieve generated certificates

```bash
# one certificate (use download_url from the listing)
curl -o cert.pdf http://127.0.0.1:8000/certificates/<certificate_id>/download

# every successfully generated certificate in a job, as a ZIP
curl -o certificates.zip http://127.0.0.1:8000/jobs/<job_id>/download
```

### Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs` | Submit a bulk request (202) |
| GET | `/jobs/{id}` | Status, counts, progress, failures |
| GET | `/jobs/{id}/certificates` | Per-recipient results (filter + pagination) |
| GET | `/certificates/{id}/download` | Download one PDF |
| GET | `/jobs/{id}/download` | Download all generated PDFs as a ZIP |
| GET | `/health` | Health check |
| GET | `/` | Web UI |

## Design decisions

**Processing model: asynchronous background processing.** `POST /jobs` validates and stores the request, returns `202` with a job id, then generates PDFs in a FastAPI background task. A request with hundreds of recipients can take a while; holding an HTTP connection open for it risks client/proxy timeouts and blocks the client. The client polls `GET /jobs/{id}`.
*Trade-off:* background tasks run inside the web process, so they are lost if it crashes. To limit the damage, jobs and certificates are persisted before work starts, and on startup the app resumes any job left in `PENDING`/`PROCESSING` (already-generated certificates are skipped). For production-scale throughput I would move `service.process_job` to a task queue (Celery/RQ/Arq with Redis). The service layer is separate from the HTTP layer so that change is small.

**Two-level validation.**
- *Request level* (missing title, empty recipient list, bad date, over the limit) → whole request rejected with `422`, nothing stored.
- *Recipient level* (empty name, invalid email, missing field, duplicate email in the job, characters the font cannot render) → only that recipient is recorded as `FAILED` with a clear reason; valid recipients proceed.

**Failure isolation.** Each certificate is generated inside its own `try/except` and committed on its own. One failure never stops the rest, and polling clients see live progress. Failed rows are stored (with the reason and the raw input) so the client can see exactly who failed and why, and can resubmit only those.

**Data model.** `jobs` (shared info + status) one-to-many `certificates` (one row per recipient: status, error, file path, original position, raw input). Counts are computed with a `GROUP BY` on certificates rather than stored counters, so they can never drift out of sync.

**Certificate generation.** `app/certificate.py` is a pure function (data in, PDF bytes out) drawn with ReportLab, so it is independent of the DB/HTTP and easy to test or replace. Long names/titles are auto-shrunk to fit. PDFs are stored on disk (`STORAGE_DIR/<job_id>/<certificate_id>.pdf`); the DB stores only the path.

**Other choices.** SQLite by default for zero-setup, but only SQLAlchemy is used so PostgreSQL works by changing `DATABASE_URL`. Application factory (`create_app`) lets tests build isolated instances. IDs are random UUIDs (not guessable sequential numbers).

## Known limitations / possible next steps

- Names are limited to Latin characters because the built-in Helvetica font is used. To support other scripts, register a Unicode `.ttf` font with ReportLab and relax the check in `schemas.py`.
- No authentication or rate limiting (out of scope; would be needed before exposing publicly).
- In-process background tasks (see above); a task queue would be the production path.
- No endpoint to retry failed certificates yet; today the client resubmits the failed recipients.
- Schema is created with `create_all`; a real deployment would use Alembic migrations.

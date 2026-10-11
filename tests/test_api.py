import io
import zipfile

from app import certificate, service
from app.models import Job
from app.schemas import JobCreate


# ---------- Frontend ----------

def test_frontend_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Bulk Certificate Generator" in r.text


# ---------- 1. Creating a generation job ----------

def test_create_job_returns_202_with_job_id(client, valid_payload):
    r = client.post("/jobs", json=valid_payload)
    assert r.status_code == 202
    body = r.json()
    assert body["id"]
    assert body["total"] == 3
    assert body["title"] == "Python Bootcamp 2026"


def test_one_request_creates_many_certificates(client, valid_payload):
    r = client.post("/jobs", json=valid_payload)
    job_id = r.json()["id"]
    listing = client.get(f"/jobs/{job_id}/certificates").json()
    assert listing["total"] == 3
    assert [c["recipient_name"] for c in listing["items"]] == [
        "Greeshma Loka", "Arjun Rao", "Meera Iyer",
    ]


# ---------- 2. Input validation ----------

def test_missing_title_is_rejected(client, valid_payload):
    del valid_payload["title"]
    assert client.post("/jobs", json=valid_payload).status_code == 422


def test_empty_recipient_list_is_rejected(client, valid_payload):
    valid_payload["recipients"] = []
    assert client.post("/jobs", json=valid_payload).status_code == 422


def test_too_many_recipients_is_rejected(client, valid_payload):
    valid_payload["recipients"] = [
        {"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(51)
    ]
    r = client.post("/jobs", json=valid_payload)
    assert r.status_code == 422
    assert "Too many" in r.json()["detail"]


def test_bad_date_is_rejected(client, valid_payload):
    valid_payload["issue_date"] = "not-a-date"
    assert client.post("/jobs", json=valid_payload).status_code == 422


def test_invalid_recipients_fail_individually_not_the_whole_job(client, valid_payload):
    valid_payload["recipients"] = [
        {"name": "Good Person", "email": "good@example.com"},
        {"name": "", "email": "noname@example.com"},          # empty name
        {"name": "Bad Email", "email": "not-an-email"},       # bad email
        {"name": "No Email"},                                 # missing email
        {"name": "Good Person 2", "email": "GOOD@example.com"},  # duplicate email
        {"name": "நிலா", "email": "nila@example.com"},        # unsupported glyphs
    ]
    r = client.post("/jobs", json=valid_payload)
    assert r.status_code == 202
    job = client.get(f"/jobs/{r.json()['id']}").json()
    assert job["generated"] == 1
    assert job["failed"] == 5
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    positions = [f["position"] for f in job["failures"]]
    assert positions == [1, 2, 3, 4, 5]
    errors = {f["position"]: f["error"] for f in job["failures"]}
    assert "Duplicate" in errors[4]
    assert "name" in errors[1]
    assert "email" in errors[3]


# ---------- 3. Certificate generation ----------

def test_generated_certificate_is_a_pdf_containing_recipient_details(
    client, valid_payload
):
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    cert = client.get(f"/jobs/{job_id}/certificates").json()["items"][0]
    r = client.get(cert["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert b"Greeshma Loka" in r.content
    assert b"Python Bootcamp 2026" in r.content
    assert b"Acme Academy" in r.content


def test_render_certificate_directly():
    pdf = certificate.render_certificate(
        recipient_name="A Very Long Name " * 4,
        title="T",
        issued_by="Org",
        issue_date="2026-01-01",
        certificate_id="abc",
    )
    assert pdf.startswith(b"%PDF")


# ---------- 4. Job status / progress ----------

def test_job_status_after_completion(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "COMPLETED"
    assert job["total"] == 3
    assert job["generated"] == 3
    assert job["failed"] == 0
    assert job["pending"] == 0
    assert job["progress_percent"] == 100.0
    assert job["completed_at"] is not None
    assert job["download_all_url"] == f"/jobs/{job_id}/download"


def test_job_status_before_processing_shows_pending(app, client, valid_payload):
    """Create the job via the service only (no background run) to observe
    the 'accepted but not started' state."""
    with app.state.session_factory() as session:
        job = service.create_job(session, JobCreate(**valid_payload))
        job_id = job.id
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "PENDING"
    assert body["pending"] == 3
    assert body["generated"] == 0
    assert body["progress_percent"] == 0.0
    assert body["download_all_url"] is None


def test_process_job_is_resumable(app, valid_payload):
    """Running the processor twice must not regenerate or duplicate anything."""
    sf = app.state.session_factory
    storage = app.state.settings.storage_dir
    with sf() as session:
        job_id = service.create_job(session, JobCreate(**valid_payload)).id
    service.process_job(sf, storage, job_id)
    service.process_job(sf, storage, job_id)
    with sf() as session:
        counts = service.status_counts(session, job_id)
        assert counts["GENERATED"] == 3
        assert session.get(Job, job_id).status == "COMPLETED"


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/doesnotexist").status_code == 404
    assert client.get("/jobs/doesnotexist/certificates").status_code == 404
    assert client.get("/jobs/doesnotexist/download").status_code == 404


# ---------- 5. Individual certificate failure ----------

def test_one_generation_failure_does_not_stop_the_others(
    client, valid_payload, monkeypatch
):
    real_render = certificate.render_certificate

    def flaky_render(**kwargs):
        if kwargs["recipient_name"] == "Arjun Rao":
            raise RuntimeError("renderer exploded")
        return real_render(**kwargs)

    monkeypatch.setattr(certificate, "render_certificate", flaky_render)

    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()

    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["generated"] == 2
    assert job["failed"] == 1
    assert job["failures"][0]["recipient_name"] == "Arjun Rao"
    assert "renderer exploded" in job["failures"][0]["error"]

    failed = client.get(f"/jobs/{job_id}/certificates?status=failed").json()
    assert failed["total"] == 1
    ok = client.get(f"/jobs/{job_id}/certificates?status=GENERATED").json()
    assert {c["recipient_name"] for c in ok["items"]} == {"Greeshma Loka", "Meera Iyer"}


def test_job_fails_when_every_certificate_fails(client, valid_payload, monkeypatch):
    def always_fail(**kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(certificate, "render_certificate", always_fail)
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "FAILED"
    assert job["failed"] == 3
    assert client.get(f"/jobs/{job_id}/download").status_code == 409


# ---------- 6. Retrieving generated certificates ----------

def test_download_single_certificate(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    items = client.get(f"/jobs/{job_id}/certificates").json()["items"]
    r = client.get(f"/certificates/{items[1]['id']}/download")
    assert r.status_code == 200
    assert "certificate_Arjun_Rao.pdf" in r.headers["content-disposition"]


def test_download_all_as_zip(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert names == [
            "0001_Greeshma_Loka.pdf",
            "0002_Arjun_Rao.pdf",
            "0003_Meera_Iyer.pdf",
        ]
        assert zf.read(names[0]).startswith(b"%PDF")


def test_failed_certificate_cannot_be_downloaded(client, valid_payload):
    valid_payload["recipients"].append({"name": "Bad", "email": "bad"})
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    failed = client.get(f"/jobs/{job_id}/certificates?status=FAILED").json()["items"]
    assert failed[0]["download_url"] is None
    assert client.get(f"/certificates/{failed[0]['id']}/download").status_code == 409


def test_unknown_certificate_returns_404(client):
    assert client.get("/certificates/nope/download").status_code == 404


def test_list_certificates_pagination_and_bad_filter(client, valid_payload):
    job_id = client.post("/jobs", json=valid_payload).json()["id"]
    page = client.get(f"/jobs/{job_id}/certificates?limit=2&offset=2").json()
    assert page["total"] == 3
    assert len(page["items"]) == 1
    assert client.get(f"/jobs/{job_id}/certificates?status=bogus").status_code == 422

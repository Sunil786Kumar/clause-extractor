from pathlib import Path

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import MAX_UPLOAD_BYTES, app, get_client
from extractor import extract_clauses
from tests.conftest import FakeClient, make_pdf


@pytest.fixture
def use_client():
    """Install a fake Anthropic client for the duration of a test."""
    def install(fake):
        app.dependency_overrides[get_client] = lambda: fake
    yield install
    app.dependency_overrides.clear()


API_KEY = "test-service-key"


@pytest.fixture(autouse=True)
def service_api_key(monkeypatch):
    """Configure the service key for every test; tests about a missing key unset it."""
    monkeypatch.setenv("SERVICE_API_KEY", API_KEY)


http = TestClient(app)


def upload(content: bytes, filename="contract.pdf", content_type="application/pdf", headers=None):
    if headers is None:
        headers = {"X-API-Key": API_KEY}
    return http.post("/extract", files={"file": (filename, content, content_type)}, headers=headers)


def test_health():
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_key_even_when_service_key_unset(monkeypatch):
    monkeypatch.delenv("SERVICE_API_KEY")

    response = http.get("/health")

    assert response.status_code == 200


def test_extract_rejects_missing_key(use_client, contract_pdf):
    client = FakeClient()
    use_client(client)

    response = upload(contract_pdf.read_bytes(), headers={})

    assert response.status_code == 401
    assert client.calls == []


def test_extract_rejects_wrong_key(use_client, contract_pdf):
    client = FakeClient()
    use_client(client)

    response = upload(contract_pdf.read_bytes(), headers={"X-API-Key": "wrong-key"})

    assert response.status_code == 401
    assert client.calls == []


def test_extract_rejects_non_ascii_key_with_401(use_client, contract_pdf):
    use_client(FakeClient())

    response = upload(contract_pdf.read_bytes(), headers={"X-API-Key": "clé".encode()})

    assert response.status_code == 401


@pytest.mark.parametrize("configured", [None, ""], ids=["unset", "empty"])
def test_extract_fails_closed_without_service_key(use_client, contract_pdf, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("SERVICE_API_KEY")
    else:
        monkeypatch.setenv("SERVICE_API_KEY", configured)
    client = FakeClient()
    use_client(client)

    # An empty header must not match an empty setting.
    for headers in ({}, {"X-API-Key": ""}, {"X-API-Key": API_KEY}):
        response = upload(contract_pdf.read_bytes(), headers=headers)
        assert response.status_code == 503
    assert client.calls == []


def test_openapi_declares_api_key_on_extract_only():
    schema = http.get("/openapi.json").json()

    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
    }
    assert schema["paths"]["/extract"]["post"]["security"] == [{"APIKeyHeader": []}]
    assert "security" not in schema["paths"]["/health"]["get"]


def test_extract_returns_result(use_client, contract_pdf):
    use_client(FakeClient(clauses=[{
        "type": "termination", "title": "Clause 2", "summary": "Either side can end it.",
        "page": 2, "quote": "Either party may end this agreement with 30 days notice.",
    }]))

    response = upload(contract_pdf.read_bytes())

    assert response.status_code == 200
    body = response.json()
    assert body["document"] == "contract.pdf"
    assert body["pages"] == 2
    assert body["clauses"][0]["quote_verified"] is True
    assert body["missing_types"] == ["liability", "payment", "renewal"]


def test_extract_rejects_non_pdf(use_client):
    use_client(FakeClient())

    response = upload(b"just some text", filename="notes.txt", content_type="text/plain")

    assert response.status_code == 400


def test_extract_maps_runtime_error_to_502(use_client, contract_pdf):
    use_client(FakeClient(error=RuntimeError("Claude ran out of tokens")))

    response = upload(contract_pdf.read_bytes())

    assert response.status_code == 502
    assert response.json() == {"detail": "Claude ran out of tokens"}


def test_extract_rejects_file_over_10_mb(use_client):
    client = FakeClient()
    use_client(client)

    response = upload(b"%PDF-" + b"0" * MAX_UPLOAD_BYTES)

    assert response.status_code == 413
    assert client.calls == []


def test_extract_maps_pdf_without_text_to_422(use_client):
    use_client(FakeClient())

    response = upload(make_pdf(["", ""]))

    assert response.status_code == 422
    assert response.json()["detail"].startswith("No text could be extracted.")


def test_extract_deletes_temp_file_when_extraction_fails(use_client, contract_pdf, monkeypatch):
    use_client(FakeClient(error=RuntimeError("Claude ran out of tokens")))
    seen_paths = []

    def recording_extract(pdf_path, client):
        seen_paths.append(Path(pdf_path))
        assert seen_paths[0].exists()
        return extract_clauses(pdf_path, client=client)

    monkeypatch.setattr(app_module, "extract_clauses", recording_extract)

    response = upload(contract_pdf.read_bytes())

    assert response.status_code == 502
    assert len(seen_paths) == 1
    assert not seen_paths[0].exists()


def test_extract_rejects_renamed_text_file(use_client):
    use_client(FakeClient())

    response = upload(b"just some text", filename="contract.pdf", content_type="application/pdf")

    assert response.status_code == 400


def test_extract_maps_invalid_claude_response_to_502(use_client, contract_pdf):
    use_client(FakeClient(tool_input={"clauses": [{"type": "payment"}]}))

    response = upload(contract_pdf.read_bytes())

    assert response.status_code == 502
    assert response.json() == {"detail": "The AI service returned an invalid response."}


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("error", [
    anthropic.APIConnectionError(request=REQUEST),
    anthropic.InternalServerError("Overloaded", response=httpx2.Response(529, request=REQUEST), body=None),
], ids=["connection", "server"])
def test_extract_maps_anthropic_api_error_to_502(use_client, contract_pdf, error):
    use_client(FakeClient(error=error))

    response = upload(contract_pdf.read_bytes())

    assert response.status_code == 502
    assert response.json() == {"detail": "The AI service is unavailable. Please try again."}

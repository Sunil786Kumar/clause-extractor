import pytest
from fastapi.testclient import TestClient

from app import app, get_client
from tests.conftest import FakeClient, make_pdf


@pytest.fixture
def use_client():
    """Install a fake Anthropic client for the duration of a test."""
    def install(fake):
        app.dependency_overrides[get_client] = lambda: fake
    yield install
    app.dependency_overrides.clear()


http = TestClient(app)


def upload(content: bytes, filename="contract.pdf", content_type="application/pdf"):
    return http.post("/extract", files={"file": (filename, content, content_type)})


def test_health():
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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

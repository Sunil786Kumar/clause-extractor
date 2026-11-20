"""Shared test helpers: tiny generated PDFs and a fake Anthropic client."""

import io
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas


def make_pdf(pages: list[str]) -> bytes:
    """Build a PDF with one page per string. An empty string gives a page with no text."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for text in pages:
        if text:
            pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


class FakeClient:
    """Stands in for Anthropic: messages.create returns a canned tool_use response, or raises."""

    def __init__(self, clauses=None, stop_reason="tool_use", tool_input=None, error=None):
        self.tool_input = tool_input if tool_input is not None else {"clauses": clauses or []}
        self.stop_reason = stop_reason
        self.error = error
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        block = SimpleNamespace(type="tool_use", name="save_clauses", input=self.tool_input)
        usage = SimpleNamespace(input_tokens=100, output_tokens=16000 if self.stop_reason == "max_tokens" else 50)
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason, usage=usage)


@pytest.fixture
def contract_pdf(tmp_path):
    path = tmp_path / "contract.pdf"
    path.write_bytes(make_pdf([
        "Clause 1 Payment. The tenant must pay rent of $500 every fortnight.",
        "Clause 2 Termination. Either party may end this agreement with 30 days notice.",
    ]))
    return path

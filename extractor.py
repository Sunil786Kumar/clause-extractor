"""Contract Clause Extractor: pull key clauses out of a contract PDF, with page references and verified quotes."""

import re
import sys
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pypdf import PdfReader

load_dotenv()

MODEL = "claude-haiku-5-5"
MAX_PAGES = 60  # whole-document approach: keep requests a sensible size

ClauseType = Literal["termination", "liability", "payment", "renewal"]
ALL_TYPES: list[str] = ["termination", "liability", "payment", "renewal"]


# --- 1. What Claude must return ---
class Clause(BaseModel):
    type: ClauseType
    title: str = Field(description="The clause's heading or number as written, e.g. 'Clause 6 Continuation of fixed term agreement'")
    summary: str = Field(description="One or two plain-English sentences explaining what the clause means")
    page: int = Field(description="Page number from the [Page N] marker where the clause appears")
    quote: str = Field(description="A short exact quote (one sentence or less) copied word for word from that page")


class Extraction(BaseModel):
    clauses: list[Clause]


# --- 2. What our service returns (Claude's output plus our own checks) ---
class CheckedClause(Clause):
    quote_verified: bool


class ExtractionResult(BaseModel):
    document: str
    pages: int
    model: str
    clauses: list[CheckedClause]
    missing_types: list[str]


SYSTEM = (
    "You extract key clauses from contracts. Find every clause about termination (ending the agreement, notice to end), "
    "liability (responsibility for loss, damage, indemnities, limits on liability), payment (amounts, timing, method), "
    "and renewal (extension, continuation or renewal of the term). "
    "Use only the document provided. For each clause give the page from its [Page N] marker and a short quote copied "
    "exactly from that page. Do not invent clauses. If a type isn't in the document, leave it out."
)


# --- 3. Reading the PDF ---
def read_pages(pdf_path: str) -> list[str]:
    """Return the text of each page, with whitespace tidied."""
    reader = PdfReader(pdf_path)
    return [re.sub(r"\s+", " ", page.extract_text() or "").strip() for page in reader.pages]


# --- 4. Checking quotes against the real page text ---
def normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_on_page(quote: str, page: int, pages: list[str]) -> bool:
    if not 1 <= page <= len(pages):
        return False
    return normalise(quote) in normalise(pages[page - 1])


# --- 5. The extraction itself ---
def extract_clauses(pdf_path: str, client: Anthropic | None = None) -> ExtractionResult:
    pages = read_pages(pdf_path)
    if not any(pages):
        raise ValueError("No text could be extracted. The PDF may be a scanned image, which needs OCR.")
    if len(pages) > MAX_PAGES:
        raise ValueError(f"Document has {len(pages)} pages; the limit is {MAX_PAGES}.")

    document = "\n\n".join(f"[Page {n}]\n{text}" for n, text in enumerate(pages, start=1))
    client = client or Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        tools=[{
            "name": "save_clauses",
            "description": "Save the clauses extracted from the contract.",
            "input_schema": Extraction.model_json_schema(),
        }],
        tool_choice={"type": "tool", "name": "save_clauses"},
        messages=[{"role": "user", "content": f"Contract:\n\n{document}"}],
    )

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Claude ran out of output tokens ({response.usage.output_tokens} used). "
            "Raise max_tokens or split the document."
        )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    extraction = Extraction.model_validate(tool_block.input)

    checked = [
        CheckedClause(**c.model_dump(), quote_verified=quote_on_page(c.quote, c.page, pages))
        for c in extraction.clauses
    ]
    found_types = {c.type for c in checked}
    return ExtractionResult(
        document=Path(pdf_path).name,
        pages=len(pages),
        model=MODEL,
        clauses=checked,
        missing_types=[t for t in ALL_TYPES if t not in found_types],
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run python extractor.py path/to/contract.pdf")
    result = extract_clauses(sys.argv[1])
    print(result.model_dump_json(indent=2))

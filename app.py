"""HTTP API for the Contract Clause Extractor."""

import os
import secrets
import tempfile
from pathlib import Path
from typing import Annotated

from anthropic import Anthropic, APIError
from fastapi import Depends, FastAPI, HTTPException, Security, UploadFile
from fastapi.security import APIKeyHeader
from pydantic import ValidationError

from extractor import ExtractionResult, extract_clauses

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

app = FastAPI(title="Contract Clause Extractor")

# auto_error=False so a missing header reaches require_api_key, which decides between 401 and 503.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_client() -> Anthropic:
    """The Anthropic client used for extraction. Tests override this dependency."""
    return Anthropic()


def require_api_key(api_key: Annotated[str | None, Security(api_key_header)]) -> None:
    """Reject requests without the right X-API-Key. Fails closed if SERVICE_API_KEY isn't configured."""
    # Read per request rather than at import. An empty value counts as unset.
    expected = os.environ.get("SERVICE_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="The service is not configured.")
    # Compare bytes: compare_digest rejects str containing non-ASCII characters.
    if api_key is None or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# A plain `def` so FastAPI runs the blocking extraction in its threadpool.
@app.post("/extract", dependencies=[Depends(require_api_key)])
def extract(file: UploadFile, client: Annotated[Anthropic, Depends(get_client)]) -> ExtractionResult:
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 10 MB.")
    if not contents.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="File is not a PDF.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
    try:
        result = extract_clauses(tmp.name, client=client)
    # ValidationError subclasses ValueError, so it must be caught first. Its details stay out of the response.
    except ValidationError:
        raise HTTPException(status_code=502, detail="The AI service returned an invalid response.")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except APIError:
        raise HTTPException(status_code=502, detail="The AI service is unavailable. Please try again.")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        os.unlink(tmp.name)

    # extract_clauses names the document after the file it read, which here is the temp file.
    return result.model_copy(update={"document": Path(file.filename or tmp.name).name})

"""HTTP API for the Contract Clause Extractor."""

import os
import tempfile
from pathlib import Path
from typing import Annotated

from anthropic import Anthropic, APIError
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from pydantic import ValidationError

from extractor import ExtractionResult, extract_clauses

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

app = FastAPI(title="Contract Clause Extractor")


def get_client() -> Anthropic:
    """The Anthropic client used for extraction. Tests override this dependency."""
    return Anthropic()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# A plain `def` so FastAPI runs the blocking extraction in its threadpool.
@app.post("/extract")
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

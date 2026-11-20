import pytest

from extractor import MODEL, extract_clauses, normalise, quote_on_page
from tests.conftest import FakeClient, make_pdf


def test_normalise_collapses_whitespace_and_lowercases():
    assert normalise("  The  Tenant\n must\tPAY  ") == "the tenant must pay"


def test_normalise_straightens_curly_quotes():
    assert normalise("‘it’s’ “done”") == "'it's' \"done\""


PAGES = ["The tenant must pay rent.", "Either party may end this agreement."]


def test_quote_on_page_matches_ignoring_case_and_spacing():
    assert quote_on_page("EITHER party  may end", 2, PAGES)


def test_quote_on_page_rejects_quote_from_another_page():
    assert not quote_on_page("Either party may end", 1, PAGES)


@pytest.mark.parametrize("page", [0, 3])
def test_quote_on_page_rejects_out_of_range_page(page):
    assert not quote_on_page("The tenant", page, PAGES)


def clause(type_, page, quote):
    return {"type": type_, "title": f"{type_} clause", "summary": "Summary.", "page": page, "quote": quote}


def test_extract_clauses_verifies_quotes_and_lists_missing_types(contract_pdf):
    client = FakeClient(clauses=[
        clause("payment", 1, "The tenant must pay rent of $500 every fortnight."),
        clause("termination", 1, "Either party may end this agreement"),  # real text, wrong page
    ])

    result = extract_clauses(str(contract_pdf), client=client)

    assert result.document == "contract.pdf"
    assert result.pages == 2
    assert result.model == MODEL
    assert [c.quote_verified for c in result.clauses] == [True, False]
    assert result.missing_types == ["liability", "renewal"]
    assert "[Page 2]" in client.calls[0]["messages"][0]["content"]


def test_extract_clauses_flags_invented_quote(contract_pdf):
    client = FakeClient(clauses=[clause("liability", 2, "The landlord is liable for all loss.")])

    result = extract_clauses(str(contract_pdf), client=client)

    assert result.clauses[0].quote_verified is False


def test_extract_clauses_raises_runtime_error_on_max_tokens(contract_pdf):
    client = FakeClient(stop_reason="max_tokens", tool_input={"clauses": [{"type": "payment"}]})

    with pytest.raises(RuntimeError):
        extract_clauses(str(contract_pdf), client=client)


def test_extract_clauses_rejects_pdf_without_text(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_pdf(["", ""]))
    client = FakeClient()

    with pytest.raises(ValueError, match="No text could be extracted"):
        extract_clauses(str(path), client=client)
    assert client.calls == []

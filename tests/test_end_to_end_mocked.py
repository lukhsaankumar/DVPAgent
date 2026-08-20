"""Full pipeline test with every external dependency mocked or local:
SQLite ingest -> query -> prompt -> mocked Gemini call -> Markdown -> DOCX.

No real Salesforce, Google API, ADC, or Supabase access -- the SQLite
database lives under tmp_path (see the sqlite_db fixture in conftest.py) and
the Gemini client is a fake (see tests/test_gemini_llm.py's FakeGenaiClient
pattern), so this never contacts a live service and never touches a
developer's real database.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from docx import Document

from dvp_meeting_prep import llm
from dvp_meeting_prep.ingest import ingest_rows
from dvp_meeting_prep.docx_export import markdown_to_docx_bytes
from dvp_meeting_prep.prompting import build_meeting_prep_prompt
from dvp_meeting_prep.query import fetch_all_sources_for_advisor


@pytest.fixture(autouse=True)
def _reset_gemini_client_cache():
    llm.get_gemini_client.cache_clear()
    yield
    llm.get_gemini_client.cache_clear()


@pytest.fixture(autouse=True)
def _mock_adc_credentials(monkeypatch):
    # get_gemini_client() resolves ADC itself (to attach a quota project)
    # before constructing the client -- never let that hit real ADC here.
    monkeypatch.setattr(llm, "_resolve_adc_credentials", lambda quota_project_id=None: (object(), "adc-project"))


class _FakeResponse:
    def __init__(self, text: str):
        self._text = text
        self.candidates = [MagicMock(finish_reason="STOP")]
        self.usage_metadata = {"total_token_count": 42}

    @property
    def text(self) -> str:
        return self._text


class _FakeModels:
    def __init__(self, response_text: str):
        self._response = _FakeResponse(response_text)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class _FakeGenaiClient:
    def __init__(self, response_text: str, **kwargs):
        self.kwargs = kwargs
        self.models = _FakeModels(response_text)
        self.closed = False

    def close(self):
        self.closed = True


def test_full_pipeline_sqlite_to_docx_with_mocked_gemini(sqlite_db, base_env, monkeypatch):
    ingest_rows(
        sqlite_db,
        "salesforce_data",
        [
            {
                "advisor_name": "Avery Benton",
                "advisor_number": "17018",
                "subject": "Quarterly check-in",
                "comments": "Discussed new business pipeline.",
                "status": "Completed",
            }
        ],
    )
    ingest_rows(
        sqlite_db,
        "tableau_data",
        [
            {
                "advisor_name": "Avery Benton",
                "advisor_name_number": "Avery Benton - 17018",
                "segment": "Retail",
                "fund_formatted": "Fund A",
                "measure_values": 12.5,
                "content_hash": "e2e-hash-1",
            }
        ],
    )

    source_results = fetch_all_sources_for_advisor(sqlite_db, "Avery Benton")
    assert any(source_results.values())  # data was actually found via SQLite, not empty

    prompt = build_meeting_prep_prompt("Avery Benton", source_results)
    assert "Avery Benton" in prompt

    fake_markdown = "# Meeting Prep: Avery Benton\n\n## Summary\n\nDiscussed new business pipeline.\n"

    def factory(**kwargs):
        client = _FakeGenaiClient(fake_markdown, **kwargs)
        factory.client = client
        return client

    monkeypatch.setattr(llm.genai, "Client", factory)

    markdown_response = llm.generate_meeting_prep(prompt)
    assert markdown_response == fake_markdown.strip()  # generate_meeting_prep() strips the raw response text
    assert factory.client.models.calls[0]["contents"] == prompt  # the real prompt was sent, unmodified

    with sqlite_db.write() as conn:
        conn.execute(
            "INSERT INTO meeting_prep_documents (advisor_name, prompt, response) VALUES (?, ?, ?)",
            ("Avery Benton", prompt, markdown_response),
        )
    with sqlite_db.read() as conn:
        audit_row = conn.execute("SELECT * FROM meeting_prep_documents WHERE advisor_name = ?", ("Avery Benton",)).fetchone()
    assert audit_row is not None

    docx_bytes = markdown_to_docx_bytes(markdown_response, title="DVP Meeting Prep: Avery Benton")
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 1000  # a real, non-trivial docx file, not an empty stub

    document = Document(io.BytesIO(docx_bytes))
    full_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Meeting Prep: Avery Benton" in full_text
    assert "Discussed new business pipeline." in full_text

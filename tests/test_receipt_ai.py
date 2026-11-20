"""Receipt AI unit tests (C8): the Gemini API is mocked, no network or API key needed.
Run: python -m pytest tests/test_receipt_ai.py -v
"""

import io
import json
from unittest.mock import patch

import pytest

from utils.receipt_ai import ReceiptAnalysisError, extract_receipt_data

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image"


class FakeResponse(io.BytesIO):
    """Stands in for the object returned by urllib.request.urlopen()."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def gemini_reply(result):
    # The real API wraps the JSON text inside candidates → content → parts
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]}
    return FakeResponse(json.dumps(body).encode("utf-8"))


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_request_asks_only_for_three_fields(api_key):
    reply = gemini_reply(
        {"title": "Lunch", "amount": 12.5, "description": "Lunch at Cafe A"}
    )

    with patch("urllib.request.urlopen", return_value=reply) as mock_urlopen:
        extract_receipt_data(PNG, "image/png")

    sent_request = mock_urlopen.call_args.args[0]
    payload = json.loads(sent_request.data)
    schema = payload["generationConfig"]["responseSchema"]

    assert set(schema["properties"]) == {"title", "amount", "description"}
    assert schema["required"] == ["title", "amount", "description"]


def test_result_contains_only_form_fields(api_key):
    # Even if the AI returns extra keys, only the three form fields are passed on
    reply = gemini_reply(
        {
            "title": "Lunch",
            "amount": 12.5,
            "description": "Lunch at Cafe A",
            "merchant": "Cafe A",
            "unexpected": "<script>",
        }
    )

    with patch("urllib.request.urlopen", return_value=reply):
        result = extract_receipt_data(PNG, "image/png")

    assert result == {
        "title": "Lunch",
        "amount": 12.5,
        "description": "Lunch at Cafe A",
    }


def test_missing_fields_get_safe_defaults(api_key):
    with patch("urllib.request.urlopen", return_value=gemini_reply({})):
        result = extract_receipt_data(PNG, "image/png")

    assert result == {"title": "", "amount": 0, "description": ""}


def test_missing_api_key_is_reported(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ReceiptAnalysisError, match="not configured"):
        extract_receipt_data(PNG, "image/png")


def test_unreadable_ai_response_is_reported(api_key):
    broken = FakeResponse(b'{"candidates": []}')

    with patch("urllib.request.urlopen", return_value=broken):
        with pytest.raises(ReceiptAnalysisError, match="could not be read"):
            extract_receipt_data(PNG, "image/png")

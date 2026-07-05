"""Unit tests for the response parsing in :mod:`app.utils.ai_server`.

Only the pure ``_parse_chat_response`` helper is exercised here — it requires
neither a running ``llama-server`` subprocess nor network access.
"""
import json

import pytest

from app.utils.ai_server import _parse_chat_response


def _make_response(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]})


def test_parse_valid_response():
    assert _parse_chat_response(_make_response("  hello world  ")) == "hello world"


def test_parse_malformed_json_raises():
    with pytest.raises(RuntimeError, match="malformed JSON"):
        _parse_chat_response("{not json")


def test_parse_missing_choices_raises():
    with pytest.raises(RuntimeError, match="no choices"):
        _parse_chat_response(json.dumps({"choices": []}))


def test_parse_no_choices_key_raises():
    with pytest.raises(RuntimeError, match="no choices"):
        _parse_chat_response(json.dumps({"error": "boom"}))


def test_parse_missing_content_raises():
    with pytest.raises(RuntimeError, match="missing message content"):
        _parse_chat_response(json.dumps({"choices": [{"message": {}}]}))


def test_parse_non_object_response_raises():
    with pytest.raises(RuntimeError):
        _parse_chat_response(json.dumps("just a string"))


def test_parse_non_string_content_raises():
    body = json.dumps({"choices": [{"message": {"content": 42}}]})
    with pytest.raises(RuntimeError, match="missing message content"):
        _parse_chat_response(body)

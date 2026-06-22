import json

import pytest

from scripts.bm25_debug_server import make_error, parse_json_body


def test_parse_json_body_requires_object():
    with pytest.raises(ValueError, match="请求体必须是 JSON 对象"):
        parse_json_body(b"[1, 2, 3]")


def test_parse_json_body_returns_object():
    assert parse_json_body(b'{"question": {"qid": "q1"}}') == {
        "question": {"qid": "q1"}
    }


def test_make_error_response_shape():
    assert make_error("bad input") == {"ok": False, "error": "bad input"}

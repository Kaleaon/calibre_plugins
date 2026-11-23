"""Offline tests for the Claude Library Fixer engine (claude/engine.py).

Anthropic is replaced by a scripted transport returning canned Messages API
answers, so nothing here touches the network or needs a real API key.
"""

import importlib.util
import io
import json
import sys
import urllib.error
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

spec = importlib.util.spec_from_file_location("claude_engine", Path(__file__).resolve().parents[1] / "claude/engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def book(i, **kw):
    base = {
        "id": i,
        "title": f"Book {i}",
        "authors": ["A B"],
        "series": "",
        "series_index": None,
        "tags": [],
        "languages": [],
        "publisher": "",
        "filenames": [],
    }
    base.update(kw)
    return base


def reply(answers, stop_reason="end_turn"):
    """An Anthropic Messages API answer carrying `answers` as its text."""
    return json.dumps(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": json.dumps(answers)}],
            "stop_reason": stop_reason,
        }
    ).encode()


def http_error(code, message="x", error_type="api_error", retry_after=None):
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    body = json.dumps({"type": "error", "error": {"type": error_type, "message": message}}).encode()
    return urllib.error.HTTPError("https://api.anthropic.com/v1/messages", code, "err", headers, io.BytesIO(body))


class Scripted:
    """A transport that returns or raises the next scripted item and records the requests."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        item = self.script.pop(0)
        res = item(body) if callable(item) else item
        if isinstance(res, Exception):
            raise res
        return res


# --------------------------------------------------------------------------- transport


def test_generate_posts_anthropic_messages_request():
    t = Scripted(reply([{"id": 1, "title": "Dune", "confidence": "high", "reason": "junk removed"}]))
    out = engine.generate("SECRET", "claude-sonnet-4-5", "prompt", transport=t)
    assert out[0]["title"] == "Dune"
    url, headers, body = t.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "SECRET"
    assert headers["anthropic-version"] == engine.ANTHROPIC_VERSION
    sent = json.loads(body)
    assert sent["model"] == "claude-sonnet-4-5"
    assert sent["max_tokens"] == engine.MAX_TOKENS
    assert "clean up the metadata" in sent["system"]
    assert "Return ONLY a JSON array" in sent["system"]
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][0]["content"] == [{"type": "text", "text": "prompt"}]
    assert "SECRET" not in json.dumps(sent)  # the key travels in the header only


def test_generate_tolerates_markdown_fences():
    fenced = json.dumps({"content": [{"type": "text", "text": "```json\n[]\n```"}], "stop_reason": "end_turn"}).encode()
    assert engine.generate("k", "m", "p", transport=Scripted(fenced)) == []


def test_generate_refusal_is_blocked():
    refused = json.dumps({"content": [], "stop_reason": "refusal"}).encode()
    try:
        engine.generate("k", "m", "p", transport=Scripted(refused))
    except engine.Blocked:
        pass
    else:
        raise AssertionError("refusal should raise Blocked")


def test_generate_bad_key_names_the_key():
    t = Scripted(http_error(401, "authentication_error: invalid x-api-key", "authentication_error"))
    try:
        engine.generate("BAD", "m", "p", transport=t, retries=0)
    except engine.ClaudeError as exc:
        assert "rejected the API key" in str(exc)
        assert "BAD" not in str(exc)
    else:
        raise AssertionError("401 should raise ClaudeError")


def test_generate_retries_on_429_with_retry_after():
    sleeps = []
    t = Scripted(
        http_error(429, "rate limited", "rate_limit_error", retry_after="2"),
        reply([]),
    )
    assert engine.generate("k", "m", "p", transport=t, sleep=sleeps.append) == []
    assert sleeps and sum(sleeps) >= 2


def test_list_models_paginates():
    page1 = json.dumps(
        {
            "data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-4-5"}],
            "has_more": True,
            "last_id": "claude-haiku-4-5",
        }
    ).encode()
    page2 = json.dumps({"data": [{"id": "claude-opus-4-5"}], "has_more": False, "last_id": "claude-opus-4-5"}).encode()
    t = Scripted(page1, page2)
    assert engine.list_models("k", transport=t) == ["claude-haiku-4-5", "claude-opus-4-5", "claude-sonnet-4-5"]
    assert "after_id=claude-haiku-4-5" in t.calls[1][0]


def test_validate_api_key():
    t = Scripted(json.dumps({"data": [{"id": "claude-sonnet-4-5"}]}).encode())
    assert engine.validate_api_key("k", transport=t) is True
    assert t.calls[0][0].endswith("/models?limit=1")
    try:
        engine.validate_api_key("  ")
    except engine.ClaudeError:
        pass
    else:
        raise AssertionError("empty key should raise")


def test_image_parts_use_anthropic_image_blocks():
    b = book(1, images=[{"label": "front cover", "mime": "image/png", "data": "QUJD"}])
    parts = engine.build_parts([b], hydrate=False)
    # build_parts hydrates by default path only when hydrate=True; with data present and hydrate=False
    # only the label is emitted, so exercise the hydrating path instead:
    parts = engine.build_parts([b])
    images = [p for p in parts if p.get("type") == "image"]
    assert images == [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}]
    assert all(p.get("type") in ("text", "image") for p in parts)


def test_fallback_models():
    assert engine.get_fallback_model("claude-opus-4-5") == "claude-sonnet-4-5"
    assert engine.get_fallback_model("claude-sonnet-4-5") == "claude-haiku-4-5"
    assert engine.get_fallback_model("claude-haiku-4-5") == "claude-haiku-4-5"
    assert engine.DEFAULT_MODEL == "claude-sonnet-4-5"
    assert engine.API_ROOT.startswith("https://api.anthropic.com")


# --------------------------------------------------------------------------- end to end


def test_plan_fixes_end_to_end():
    b = book(1, title="dune messiah [epub]", authors=["Herbert, Frank"])
    answer = [
        {
            "id": 1,
            "title": "Dune Messiah",
            "authors": ["Frank Herbert"],
            "series": "Dune",
            "series_index": 2,
            "confidence": "high",
            "reason": "cleaned",
        }
    ]
    out, skipped = engine.plan_fixes([b], "k", transport=Scripted(reply(answer)))
    assert skipped == []
    assert len(out) == 1
    changes = out[0]["changes"]
    assert changes["title"] == ("dune messiah [epub]", "Dune Messiah")
    assert changes["authors"] == (["Herbert, Frank"], ["Frank Herbert"])
    assert changes["series"] == ("", "Dune")


def test_plan_fixes_drops_unknown_ids_and_low_confidence():
    answers = [
        {"id": 99, "title": "Nope", "confidence": "high"},
        {"id": 1, "title": "Book One", "confidence": "low"},
    ]
    out, _ = engine.plan_fixes([book(1)], "k", transport=Scripted(reply(answers)))
    assert out == []


def test_sanitize_and_isbn_spot_checks():
    b = book(1, title="Dune Messiah (Dune #2)")
    got = engine.sanitize(b, {"series": "Dune", "series_index": 2}, engine.FIELDS)
    assert got["series"] == ("", "Dune")
    assert engine.clean_isbn("978-0-441-17271-9") == "9780441172719"
    assert not engine.clean_isbn("9780441172710")  # bad check digit is rejected
    assert engine.numbering_in_title("book 1 - the force awakens") == ("1", "the force awakens")

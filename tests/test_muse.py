"""Offline tests for the Muse Library Fixer engine (muse/engine.py).

Muse has no API: the engine hands a batch to Muse as a request file in an
exchange folder and waits for the answer file. These tests play Muse's part —
a thread that watches pending/ and writes done/ — plus the transport hook used
to script answers without files.
"""

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

spec = importlib.util.spec_from_file_location("muse_engine", Path(__file__).resolve().parents[1] / "muse/engine.py")
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


def scripted_transport(answers):
    """A transport that returns the answers as Muse's answer file content would look."""

    def transport(url, headers, body, timeout):
        transport.calls.append((url, headers, json.loads(body)))
        return json.dumps(answers).encode()

    transport.calls = []
    return transport


class FakeMuse(threading.Thread):
    """Watches an exchange folder like Muse does and answers every request with `answers`."""

    def __init__(self, folder, answers):
        super().__init__(daemon=True)
        self.folder = Path(folder)
        self.answers = answers
        self.requests = []

    def run(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            for req_path in sorted((self.folder / "pending").glob("*.json")):
                payload = json.loads(req_path.read_text(encoding="utf-8"))
                self.requests.append(payload)
                done = self.folder / "done" / req_path.name
                if not done.exists():
                    done.write_text(json.dumps(self.answers(payload)), encoding="utf-8")
                return
            time.sleep(0.05)


# --------------------------------------------------------------------------- the exchange


def test_exchange_round_trip(tmp_path):
    folder = str(tmp_path / ".muse-fixer")
    muse = FakeMuse(folder, lambda payload: [{"id": 1, "title": "Dune", "confidence": "high", "reason": "ok"}])
    muse.start()
    out = engine.generate(None, "muse", "Books to check: []", exchange_dir=folder, wait_seconds=30, poll_seconds=0.2)
    assert out[0]["title"] == "Dune"
    muse.join(timeout=5)
    payload = muse.requests[0]
    assert payload["kind"] == "muse-library-fixer-request"
    assert payload["model"] == "muse"
    assert "clean up the metadata" in payload["system"]
    assert payload["answer_file"] == f"done/{payload['id']}.json"
    # the request was consumed once Muse answered
    assert not list((Path(folder) / "pending").glob("*.json"))


def test_exchange_timeout_keeps_the_request(tmp_path):
    folder = str(tmp_path / ".muse-fixer")
    try:
        engine.generate(None, "muse", "prompt", exchange_dir=folder, wait_seconds=1, poll_seconds=0.2)
    except engine.QuotaExhausted as exc:
        assert "did not answer" in str(exc)
    else:
        raise AssertionError("no answer within the wait should raise QuotaExhausted")
    pending = list((Path(folder) / "pending").glob("*.json"))
    assert len(pending) == 1, "the request stays in pending for Muse to answer later"


def test_exchange_cancel(tmp_path):
    folder = str(tmp_path / ".muse-fixer")
    try:
        engine.generate(
            None, "muse", "prompt", exchange_dir=folder, wait_seconds=60, poll_seconds=0.1, cancelled=lambda: True
        )
    except engine.MuseError as exc:
        assert "Cancelled" in str(exc)
    else:
        raise AssertionError("cancel should raise")


def test_exchange_blocked_and_error_answers(tmp_path):
    folder = str(tmp_path / ".muse-fixer")
    muse = FakeMuse(folder, lambda payload: {"blocked": "cannot process this batch"})
    muse.start()
    try:
        engine.generate(None, "muse", "prompt", exchange_dir=folder, wait_seconds=30, poll_seconds=0.2)
    except engine.Blocked:
        pass
    else:
        raise AssertionError("a blocked answer should raise Blocked")


def test_check_exchange_creates_folders(tmp_path):
    folder = engine.check_exchange(str(tmp_path / "x"))
    assert (Path(folder) / "pending").is_dir()
    assert (Path(folder) / "done").is_dir()
    assert engine.list_models(None) == ["muse"]
    assert engine.DEFAULT_MODEL == "muse"
    assert engine.get_fallback_model("anything") == "muse"
    assert engine.is_rate_limit_error(Exception("429")) is False
    assert engine.is_service_unavailable_error(Exception("503")) is False


# --------------------------------------------------------------------------- end to end


def test_plan_fixes_over_the_exchange(tmp_path):
    folder = str(tmp_path / ".muse-fixer")
    b = book(1, title="dune messiah [epub]", authors=["Herbert, Frank"])
    answers = [
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
    muse = FakeMuse(folder, lambda payload: answers)
    muse.start()
    out, skipped = engine.plan_fixes([b], None, exchange_dir=folder, wait_seconds=30, poll_seconds=0.2)
    assert skipped == []
    assert len(out) == 1
    changes = out[0]["changes"]
    assert changes["title"] == ("dune messiah [epub]", "Dune Messiah")
    assert changes["series"] == ("", "Dune")


def test_plan_fixes_with_scripted_transport():
    b = book(1, title="dune messiah [epub]", authors=["Herbert, Frank"])
    t = scripted_transport(
        [{"id": 1, "title": "Dune Messiah", "authors": ["Frank Herbert"], "confidence": "high", "reason": "cleaned"}]
    )
    out, skipped = engine.plan_fixes([b], None, transport=t)
    assert skipped == []
    assert out[0]["changes"]["title"] == ("dune messiah [epub]", "Dune Messiah")
    url, headers, body = t.calls[0]
    assert url == "muse://exchange"
    assert body["content"][0]["type"] == "text"
    assert "Books to check:" in body["content"][0]["text"]


def test_image_parts_in_exchange_payload():
    b = book(1, images=[{"label": "front cover", "mime": "image/png", "data": "QUJD"}])
    parts = engine.build_parts([b])
    images = [x for x in parts if x.get("type") == "image"]
    assert images == [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}]


def test_sanitize_and_isbn_spot_checks():
    b = book(1, title="Dune Messiah (Dune #2)")
    got = engine.sanitize(b, {"series": "Dune", "series_index": 2}, engine.FIELDS)
    assert got["series"] == ("", "Dune")
    assert engine.clean_isbn("978-0-441-17271-9") == "9780441172719"
    assert not engine.clean_isbn("9780441172710")  # bad check digit is rejected
    assert engine.numbering_in_title("book 1 - the force awakens") == ("1", "the force awakens")

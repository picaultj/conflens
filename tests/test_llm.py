import pytest

from conference_analyzer import llm


def test_extract_json_plain():
    assert llm._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert llm._extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_prose():
    assert llm._extract_json('Sure! {"a": 2} done') == {"a": 2}


def test_extract_json_invalid_raises():
    with pytest.raises(llm.LLMError):
        llm._extract_json("not json at all")


class _RateLimit(Exception):
    status_code = 429


class _Auth(Exception):
    status_code = 401


def test_is_transient():
    assert llm._is_transient(_RateLimit()) is True
    assert llm._is_transient(_Auth()) is False
    assert llm._is_transient(Exception("Connection reset by peer")) is True
    assert llm._is_transient(Exception("bad request: invalid schema")) is False


def test_retry_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)  # no real waiting
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RateLimit("rate limit")
        return "ok"

    assert llm._retry(flaky, max_attempts=4) == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_on_non_transient(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise _Auth("invalid key")

    with pytest.raises(_Auth):
        llm._retry(boom, max_attempts=4)
    assert calls["n"] == 1  # not retried


def test_openai_compat_falls_back_then_retries(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    seen = []

    class Msg:
        def __init__(self, c):
            self.message = type("M", (), {"content": c})

    class Resp:
        def __init__(self, c):
            self.choices = [Msg(c)]

    def create(**kw):
        seen.append(kw)
        if "response_format" in kw:
            raise Exception("response_format not supported")   # non-transient → next opts
        if kw.get("temperature") == 0:
            raise _RateLimit("rate limit")                     # transient → retried
        return Resp('{"ok": true}')

    out = llm._openai_compat_call(create, "m", [{"role": "user", "content": "hi"}], 100)
    assert out == '{"ok": true}'
    # response_format attempt (1), temperature attempt retried (>=2), then bare attempt
    assert len(seen) >= 3

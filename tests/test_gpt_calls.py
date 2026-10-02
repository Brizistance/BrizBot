import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2 as httpx
from openai import AuthenticationError, RateLimitError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import gpt


def _err(cls, status):
    req = httpx.Request("POST", "https://x")
    return cls("e", response=httpx.Response(status, request=req), body=None)


def _client(create=None, models=None):
    c = MagicMock()
    c.responses.create = create or AsyncMock()
    c.models.list = models or AsyncMock()
    return c


def run(coro):
    return asyncio.run(coro)


def test_complete_passes_cap_and_returns_text():
    resp = SimpleNamespace(output_text="hi", usage=SimpleNamespace(input_tokens=1, output_tokens=2))
    c = _client(create=AsyncMock(return_value=resp))
    with patch.object(gpt, "_client", return_value=c):
        assert run(gpt.complete("yo", "sk-x")) == "hi"
    kw = c.responses.create.call_args.kwargs
    assert kw["max_output_tokens"] == gpt.config.LLM_MAX_OUTPUT_TOKENS and kw["input"] == "yo"


def test_safe_complete_error_mapping():
    for exc, msg in [
        (_err(AuthenticationError, 401), gpt.MSG_BAD_KEY),
        (_err(RateLimitError, 429), gpt.MSG_NO_CREDIT),
        (RuntimeError("boom"), gpt.MSG_FAILED),
    ]:
        with patch.object(gpt, "_client", return_value=_client(create=AsyncMock(side_effect=exc))):
            assert run(gpt.safe_complete("q", "sk-x")) == msg
    empty = SimpleNamespace(output_text="", usage=None)
    with patch.object(gpt, "_client", return_value=_client(create=AsyncMock(return_value=empty))):
        assert run(gpt.safe_complete("q", "sk-x")) == gpt.MSG_EMPTY


def test_validate_key():
    with patch.object(gpt, "_client", return_value=_client()):
        assert run(gpt.validate_key("sk-x")) is True
    bad = _client(models=AsyncMock(side_effect=_err(AuthenticationError, 401)))
    with patch.object(gpt, "_client", return_value=bad):
        assert run(gpt.validate_key("sk-x")) is False


def test_parse_reminder_end_to_end():
    import time
    due = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3600))
    resp = SimpleNamespace(output_text=f'{{"task":"call mom","when_iso":"{due}"}}', usage=None)
    with patch.object(gpt, "_client", return_value=_client(create=AsyncMock(return_value=resp))):
        r = run(gpt.parse_reminder("call mom in an hour", "sk-x"))
    assert r and r.task == "call mom"
    with patch.object(gpt, "_client", return_value=_client(create=AsyncMock(side_effect=RuntimeError()))):
        assert run(gpt.parse_reminder("x", "sk-x")) is None

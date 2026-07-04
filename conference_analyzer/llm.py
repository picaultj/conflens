"""Provider-agnostic LLM layer.

Three backends share one interface (:meth:`LLMClient.structured`):

* **anthropic** — Claude models via the ``anthropic`` SDK with native structured
  outputs (``output_config.format``);
* **openai** — any OpenAI or OpenAI-compatible endpoint via the ``openai`` SDK; and
* **litellm** — the ``litellm`` SDK, which proxies 100+ providers and supports a
  custom ``api_base`` (e.g. a self-hosted LiteLLM endpoint).

For the OpenAI-compatible backends we request JSON-object responses and embed the
target schema in the prompt, then parse defensively — this is the most portable
approach across arbitrary proxied models. Anthropic uses its stricter native
schema enforcement.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Provider / model catalogue (suggestions shown in the UI; any string works)
# ---------------------------------------------------------------------------
PROVIDERS = ["anthropic", "openai", "litellm"]

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o-mini",
    "litellm": "",  # user supplies, e.g. "openai/gpt-4o" or a proxy model name
}

MODEL_SUGGESTIONS = {
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
    "litellm": ["openai/gpt-4o", "anthropic/claude-sonnet-4-6", "gpt-4o-mini"],
}

# Anthropic: the `effort` parameter is rejected (400) on models that don't
# support it — notably Haiku 4.5. Only send it for models that accept it.
_EFFORT_MODELS = {"claude-opus-4-8", "claude-sonnet-4-6"}


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Transient-error retry (rate limits, overload, 5xx, timeouts, connection)
# ---------------------------------------------------------------------------
_MAX_ATTEMPTS = 4
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
_TRANSIENT_HINTS = (
    "rate limit",
    "overloaded",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "connection",
    "econnreset",
    "too many requests",
)


def _is_transient(exc: Exception) -> bool:
    """Heuristically decide whether an LLM/provider error is worth retrying.

    Works across SDKs without importing them: checks a numeric status code if
    present, otherwise the exception class name / message.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in _TRANSIENT_STATUS:
        return True
    name = type(exc).__name__.lower()
    if any(k in name for k in ("ratelimit", "timeout", "connection", "overloaded", "apistatus")):
        # APIStatusError with a non-transient status is filtered above by the
        # status check returning False only when a status is present; here it
        # has no known status, so treat connection/timeout/ratelimit as transient.
        if "apistatus" in name and not isinstance(status, int):
            return False
        return True
    msg = str(exc).lower()
    return any(h in msg for h in _TRANSIENT_HINTS)


def _retry(fn: Callable[[], Any], *, max_attempts: int = _MAX_ATTEMPTS) -> Any:
    """Call ``fn`` with exponential backoff on transient errors."""
    last: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - provider exceptions vary
            last = e
            if attempt == max_attempts - 1 or not _is_transient(e):
                raise
            delay = min(1.5 * (2 ** attempt), 20.0) + random.uniform(0, 0.75)
            time.sleep(delay)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Helpers shared by the OpenAI-compatible backends
# ---------------------------------------------------------------------------
def _schema_hint(schema: dict[str, Any]) -> str:
    return (
        "Respond with ONLY a single JSON object — no prose, no markdown code "
        "fences — that conforms to this JSON Schema:\n"
        + json.dumps(schema)
    )


def _extract_json(text: str) -> Any:
    """Parse a JSON object from a model response, tolerating fences/prose."""
    if text is None:
        raise LLMError("Empty response from the model.")
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except json.JSONDecodeError as e:
                raise LLMError(f"Model did not return valid JSON: {e}") from e
        raise LLMError("Model did not return a JSON object.")


# ---------------------------------------------------------------------------
# Base + provider implementations
# ---------------------------------------------------------------------------
class LLMClient:
    """Abstract base. Subclasses implement :meth:`structured`."""

    model: str = ""

    def structured(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 8000,
        effort: str = "medium",
    ) -> Any:
        raise NotImplementedError


class AnthropicClient(LLMClient):
    def __init__(
        self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None
    ) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("The 'anthropic' package is not installed.") from e
        self._anthropic = anthropic
        self.model = model
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        try:
            self._client = anthropic.Anthropic(**kwargs)
        except Exception as e:
            raise LLMError(f"Could not initialise Anthropic client: {e}") from e

    def structured(self, system, user, schema, max_tokens=8000, effort="medium"):
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema}
        }
        if self.model in _EFFORT_MODELS:
            output_config["effort"] = effort
        def _create():
            return self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )

        try:
            resp = _retry(_create)
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Network error talking to Anthropic: {e}") from e

        if resp.stop_reason == "refusal":
            raise LLMError("The model declined to respond to this request.")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return _extract_json(text)


class OpenAIClient(LLMClient):
    """OpenAI or any OpenAI-compatible endpoint (via ``base_url``)."""

    def __init__(
        self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError(
                "The 'openai' package is not installed. Install it with "
                "`uv sync --extra openai`."
            ) from e
        self.model = model
        kwargs: dict[str, Any] = {
            "api_key": api_key or os.environ.get("OPENAI_API_KEY") or "missing"
        }
        if base_url:
            kwargs["base_url"] = base_url
        try:
            self._client = OpenAI(**kwargs)
        except Exception as e:
            raise LLMError(f"Could not initialise OpenAI client: {e}") from e

    def structured(self, system, user, schema, max_tokens=8000, effort="medium"):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user + "\n\n" + _schema_hint(schema)},
        ]
        content = _openai_compat_call(
            self._client.chat.completions.create, self.model, messages, max_tokens
        )
        return _extract_json(content)


class LiteLLMClient(LLMClient):
    """Unified access to many providers via the litellm SDK + a custom endpoint."""

    def __init__(
        self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None
    ) -> None:
        try:
            import litellm
        except ImportError as e:
            raise LLMError(
                "The 'litellm' package is not installed. Install it with "
                "`uv sync --extra litellm`."
            ) from e
        if not model:
            raise LLMError("A model name is required for the LiteLLM provider.")
        self._litellm = litellm
        self.model = model
        self._api_key = api_key
        self._base_url = base_url

    def structured(self, system, user, schema, max_tokens=8000, effort="medium"):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user + "\n\n" + _schema_hint(schema)},
        ]
        extra: dict[str, Any] = {}
        if self._base_url:
            extra["api_base"] = self._base_url
        if self._api_key:
            extra["api_key"] = self._api_key
        content = _openai_compat_call(
            self._litellm.completion, self.model, messages, max_tokens, **extra
        )
        return _extract_json(content)


def _openai_compat_call(create_fn, model, messages, max_tokens, **extra) -> str:
    """Call an OpenAI-style ``create`` with graceful fallbacks.

    Tries JSON-object mode + ``temperature=0`` first, then progressively drops
    parameters that some models/endpoints reject (reasoning models disallow
    ``temperature``; some proxies disallow ``response_format``).
    """
    attempts = [
        {"response_format": {"type": "json_object"}, "temperature": 0},
        {"temperature": 0},
        {},
    ]
    last_err: Optional[Exception] = None
    for opts in attempts:
        def _create():
            resp = create_fn(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                **opts,
                **extra,
            )
            return resp.choices[0].message.content

        try:
            # Retry transient errors (rate limit / 5xx / timeout) for these opts;
            # a non-transient error (e.g. param rejected) falls through to the
            # next, less-demanding attempt.
            return _retry(_create)
        except Exception as e:  # noqa: BLE001 - provider exceptions vary widely
            last_err = e
    raise LLMError(f"LLM request failed: {last_err}")


# ---------------------------------------------------------------------------
# Factory + key detection
# ---------------------------------------------------------------------------
def make_client(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMClient:
    api_key = (api_key or "").strip() or None
    base_url = (base_url or "").strip() or None
    if provider == "anthropic":
        return AnthropicClient(model, api_key, base_url)
    if provider == "openai":
        return OpenAIClient(model, api_key, base_url)
    if provider == "litellm":
        return LiteLLMClient(model, api_key, base_url)
    raise LLMError(f"Unknown provider: {provider}")


def env_key_for(provider: str) -> Optional[str]:
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY")
    if provider == "litellm":
        return os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return None

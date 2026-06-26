"""Thin wrapper around the Anthropic SDK for structured-output calls."""

from __future__ import annotations

import json
from typing import Any, Optional

import anthropic

DEFAULT_MODEL = "claude-opus-4-8"

# Models offered in the UI. Opus is the most capable; Haiku is the cheapest and
# is a good fit for the high-volume, low-difficulty classification pass.
AVAILABLE_MODELS = [
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

# The `effort` parameter is rejected (400) on models that don't support it —
# notably Haiku 4.5. Only send it for models that accept it.
_EFFORT_MODELS = {"claude-opus-4-8", "claude-sonnet-4-6"}


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Wraps ``anthropic.Anthropic`` and returns parsed JSON via structured output."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        try:
            self._client = anthropic.Anthropic()
        except Exception as e:  # missing key, etc.
            raise LLMError(f"Could not initialise Anthropic client: {e}") from e

    def structured(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 8000,
        effort: str = "medium",
    ) -> Any:
        """Run one request constrained to ``schema`` and return the parsed object."""
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema}
        }
        if self.model in _EFFORT_MODELS:
            output_config["effort"] = effort
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Network error talking to Anthropic: {e}") from e

        if resp.stop_reason == "refusal":
            raise LLMError("The model declined to respond to this request.")

        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise LLMError("Empty response from the model.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model did not return valid JSON: {e}") from e


def has_api_key() -> bool:
    import os

    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )

"""AI provider client — OpenAI + Anthropic over raw httpx, plus token accounting.

Single entry point for every AI call in the app (agency analysis, lead scoring,
email writing). Providers are called with plain httpx (no vendor SDKs) so tests
can mock the HTTP layer with respx. API keys are per-user, decrypted at call
time by the caller (deps.get_decrypted_keys) — never read from global config.

Token usage from every call should be recorded via record_ai_usage() so the
usage_counters cost guardrails (PRD §1) stay accurate.
"""
import json
import uuid
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

AI_TIMEOUT_SECONDS = 60.0

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


async def ai_completion(
    prompt: str,
    *,
    system: str = "",
    provider: str = "anthropic",
    api_key: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> tuple[str, int]:
    """Run a single completion against the given provider.

    Returns (text, total_tokens). Raises ValueError for a bad provider or
    missing key, httpx.HTTPStatusError for API errors, httpx.TimeoutException
    on timeout — callers catch.
    """
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"Unknown AI provider: {provider!r} (expected 'openai' or 'anthropic')")
    if not api_key:
        raise ValueError(f"No API key provided for AI provider {provider!r}")

    resolved_model = model or DEFAULT_MODELS[provider]

    if provider == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
        payload: dict = {
            "model": resolved_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        url = ANTHROPIC_URL
    else:  # openai
        headers = {"Authorization": f"Bearer {api_key}"}
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": resolved_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        url = OPENAI_URL

    async with httpx.AsyncClient(timeout=AI_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()

    if provider == "anthropic":
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        total_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
    else:
        text = data["choices"][0]["message"]["content"] or ""
        total_tokens = int(data.get("usage", {}).get("total_tokens", 0))

    return text, total_tokens


def _strip_markdown_fences(text: str) -> str:
    """Remove a wrapping ```/```json fence if the model added one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    # Drop opening fence line (``` or ```json) and a trailing ``` line if present.
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def ai_json(
    prompt: str,
    *,
    system: str = "",
    provider: str = "anthropic",
    api_key: str,
    model: str | None = None,
    max_tokens: int = 2000,
) -> tuple[dict, int]:
    """Completion that must return a JSON object.

    Strips markdown fences, json.loads. On parse failure retries ONCE with
    "Return ONLY valid JSON." appended to the prompt, then raises ValueError.
    Returns (parsed_dict, total_tokens_across_attempts).
    """
    total_tokens = 0
    attempt_prompt = prompt
    last_error: Exception | None = None

    for attempt in range(2):
        text, tokens = await ai_completion(
            attempt_prompt,
            system=system,
            provider=provider,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
        )
        total_tokens += tokens
        try:
            parsed = json.loads(_strip_markdown_fences(text))
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
            return parsed, total_tokens
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            attempt_prompt = f"{prompt}\n\nReturn ONLY valid JSON."

    raise ValueError(f"AI did not return valid JSON after retry: {last_error}")


def record_ai_usage(db: "Session", user_id: uuid.UUID, tokens: int) -> None:
    """Upsert the usage_counters row for the current 'YYYY-MM' period, add ai_tokens."""
    from datetime import datetime

    from models import UsageCounter  # local import: keep this module importable without DB

    if tokens <= 0:
        return

    period = datetime.utcnow().strftime("%Y-%m")
    row = (
        db.query(UsageCounter)
        .filter(UsageCounter.user_id == user_id, UsageCounter.period == period)
        .first()
    )
    if row is None:
        row = UsageCounter(user_id=user_id, period=period, ai_tokens=0)
        db.add(row)
    row.ai_tokens = (row.ai_tokens or 0) + tokens
    db.commit()

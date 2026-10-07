"""A small client for any OpenAI-compatible chat endpoint, with a disk cache, retries and call spacing.

Configuration (environment variables; nothing is stored in files):
    AI_API_KEY    the key; without it every caller uses its no-model path
    AI_BASE_URL   default: Gemini's OpenAI-compatible endpoint
    AI_MODEL      default: gemini-flash-lite-latest
    AI_MIN_INTERVAL_S   minimum seconds between live calls (default 2.5; the quota is shared)

Every response is cached on disk by a hash of (model, messages, tools), together with its token usage and the
latency of the live call, so evaluations can be re-run offline and report what the live run measured.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-flash-lite-latest"
RETRY_WAITS = (5, 15, 30, 60)


class NoModel(RuntimeError):
    """Raised when no API key is configured (or offline mode found no cached answer)."""


@dataclass
class ChatResult:
    message: dict[str, Any]  # {"content": str | None, "tool_calls": [...]}
    prompt_tokens: int
    completion_tokens: int
    latency_s: float  # of the live call that produced it
    cached: bool
    model: str

    @property
    def content(self) -> str:
        return self.message.get("content") or ""

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return self.message.get("tool_calls") or []


@dataclass
class LLM:
    model: str = field(default_factory=lambda: os.getenv("AI_MODEL", DEFAULT_MODEL))
    base_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", DEFAULT_BASE_URL))
    cache_dir: Path = field(default_factory=lambda: paths.CACHE / "llm")
    offline: bool = False  # cache only: never call the endpoint
    min_interval_s: float = field(default_factory=lambda: float(os.getenv("AI_MIN_INTERVAL_S", "2.5")))
    live_calls: int = 0
    cached_calls: int = 0
    _last_call: float = 0.0

    @property
    def available(self) -> bool:
        return self.offline or bool(os.getenv("AI_API_KEY"))

    def _key(self, messages: list[dict], tools: list[dict] | None, temperature: float, json_mode: bool = False) -> str:
        blob = json.dumps(
            {"model": self.model, "messages": messages, "tools": tools, "t": temperature, "json": json_mode},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> ChatResult:
        key = self._key(messages, tools, temperature, json_mode)
        path = self.cache_dir / f"{key[:2]}" / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.cached_calls += 1
            return ChatResult(
                data["message"],
                data["prompt_tokens"],
                data["completion_tokens"],
                data["latency_s"],
                True,
                data["model"],
            )
        if self.offline:
            raise NoModel("offline mode: no cached response for this prompt")
        api_key = os.getenv("AI_API_KEY")
        if not api_key:
            raise NoModel("AI_API_KEY is not set")
        result = self._live(messages, tools, temperature, api_key, json_mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "model": result.model,
                    "message": result.message,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "latency_s": result.latency_s,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return result

    def _live(
        self, messages: list[dict], tools: list[dict] | None, temperature: float, api_key: str, json_mode: bool
    ) -> ChatResult:
        import openai

        client = openai.OpenAI(api_key=api_key, base_url=self.base_url, timeout=90, max_retries=0)
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt, wait in enumerate((0, *RETRY_WAITS)):
            if wait:
                time.sleep(wait)
            gap = self.min_interval_s - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            self._last_call = time.monotonic()
            started = time.perf_counter()
            try:
                resp = client.chat.completions.create(**kwargs)
            except (
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
            ) as e:
                last_error = e
                continue
            latency = time.perf_counter() - started
            self.live_calls += 1
            msg = resp.choices[0].message
            message: dict[str, Any] = {"content": msg.content}
            if msg.tool_calls:
                # Extra fields (Gemini's thought signatures) must be sent back with the call, so keep them.
                message["tool_calls"] = [
                    {
                        "id": tc.id or f"call_{attempt}_{i}",
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                        **(getattr(tc, "model_extra", None) or {}),
                    }
                    for i, tc in enumerate(msg.tool_calls)
                ]
            usage = resp.usage
            return ChatResult(
                message,
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
                round(latency, 3),
                False,
                resp.model or self.model,
            )
        raise RuntimeError(f"model call failed after retries: {last_error}")


# Published list prices for the default model, USD per million tokens. Used only to estimate cost from the
# token counts; change them here if the model or its pricing changes.
PRICE_PER_M_INPUT = float(os.getenv("AI_PRICE_INPUT_PER_M", "0.10"))
PRICE_PER_M_OUTPUT = float(os.getenv("AI_PRICE_OUTPUT_PER_M", "0.40"))


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens / 1e6 * PRICE_PER_M_INPUT + completion_tokens / 1e6 * PRICE_PER_M_OUTPUT


EMBED_MODEL = os.getenv("AI_EMBED_MODEL", "gemini-embedding-001")


def embedder(cache_dir: Path | None = None, offline: bool = False):
    """A cached text-embedding function for the optional hybrid retrieval (None when no key and no cache)."""
    cache = cache_dir or paths.CACHE / "embeddings"
    api_key = os.getenv("AI_API_KEY")

    def embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float] | None] = []
        missing: list[int] = []
        for i, t in enumerate(texts):
            key = hashlib.sha256(f"{EMBED_MODEL}|{t}".encode()).hexdigest()[:32]
            path = cache / key[:2] / f"{key}.json"
            if path.exists():
                out.append(json.loads(path.read_text(encoding="utf-8")))
            else:
                out.append(None)
                missing.append(i)
        if missing:
            if offline or not api_key:
                raise NoModel("embeddings not cached and no AI_API_KEY")
            import openai

            client = openai.OpenAI(api_key=api_key, base_url=os.getenv("AI_BASE_URL", DEFAULT_BASE_URL))
            for start in range(0, len(missing), 50):
                batch = missing[start : start + 50]
                for attempt, wait in enumerate((0, *RETRY_WAITS)):
                    time.sleep(wait or 2.5)
                    try:
                        resp = client.embeddings.create(model=EMBED_MODEL, input=[texts[i] for i in batch])
                        break
                    except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
                        if attempt == len(RETRY_WAITS):
                            raise
                for i, item in zip(batch, resp.data, strict=True):
                    key = hashlib.sha256(f"{EMBED_MODEL}|{texts[i]}".encode()).hexdigest()[:32]
                    path = cache / key[:2] / f"{key}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(item.embedding), encoding="utf-8")
                    out[i] = item.embedding
        return [v for v in out if v is not None]

    return embed

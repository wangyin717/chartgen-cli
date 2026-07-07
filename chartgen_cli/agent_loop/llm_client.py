"""Thin LLM client: 两种模式自动切换。

服务器模式（默认）：POST 到 SERVER_URL/api/aigc/llm/call，靠 ACCESS_TOKEN 鉴权。
BYOK 模式：设置 LLM_API_KEY 后自动启用，直连各 provider API。
  LLM_PROVIDER=deepseek|glm|gpt|openai|claude  （必填）
  LLM_API_KEY=sk-xxx                            （必填，触发 BYOK）
  LLM_MODEL=deepseek-chat                       （必填）
  LLM_BASE_URL=https://...                      （可选，覆盖内置默认值）
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_SERVER_URL: str = ""
_X_TENANT: str = ""

_PROVIDER_BASE_URL = {
    "deepseek": "https://api.deepseek.com",
    "glm":      "https://open.bigmodel.cn/api/paas/v4",
    "gpt":      None,
    "openai":   None,
    "claude":   None,
}


def init(server_url: str, access_token: str) -> None:
    global _SERVER_URL, _X_TENANT
    _SERVER_URL = server_url.rstrip("/")
    _X_TENANT = access_token


def _init_from_env() -> None:
    global _SERVER_URL, _X_TENANT
    if not _SERVER_URL:
        _SERVER_URL = os.environ.get("SERVER_URL", "").rstrip("/")
    if not _X_TENANT:
        _X_TENANT = os.environ.get("ACCESS_TOKEN", "")


@dataclass
class LLMResponse:
    text: str
    stop_reason: str = "end_turn"
    tool_calls: list = field(default_factory=list)
    usage: Optional[dict] = None


# ── 服务器模式 ────────────────────────────────────────────────────────────────

def _post(model_key: str, messages: list, tools: list, session_id: str = "", round_id: str = "") -> LLMResponse:
    _init_from_env()
    if not _SERVER_URL:
        raise RuntimeError("SERVER_URL not configured — set it in ~/.chartgen/config")

    url = f"{_SERVER_URL}/api/aigc/llm/call"
    headers = {"Access-Token": _X_TENANT, "Content-Type": "application/json"}
    payload = {
        "model_key": model_key,
        "messages": messages,
        "tools": tools,
        "llm_recorder": "cli",
        "session_id": session_id,
        "round_id": round_id,
    }

    logger.debug("[llm_client] POST %s model_key=%s messages=%d", url, model_key, len(messages))
    resp = requests.post(url, json=payload, headers=headers, timeout=600)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", body) if isinstance(body, dict) and "data" in body else body
    return LLMResponse(
        text=data.get("text", ""),
        stop_reason=data.get("stop_reason", "end_turn"),
        tool_calls=data.get("tool_calls") or [],
        usage=data.get("usage"),
    )


# ── BYOK 模式 ─────────────────────────────────────────────────────────────────

def _byok_openai(messages: list, tools: list) -> LLMResponse:
    from openai import OpenAI
    provider  = os.environ["LLM_PROVIDER"].lower()
    api_key   = os.environ["LLM_API_KEY"]
    model     = os.environ["LLM_MODEL"]
    base_url  = (os.environ.get("LLM_BASE_URL", "").split("#")[0].strip()) or _PROVIDER_BASE_URL.get(provider)

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools

    logger.debug("[llm_client/byok] provider=%s model=%s messages=%d", provider, model, len(messages))
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    msg = choice.message

    raw_calls = msg.tool_calls or []
    tool_calls = [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in raw_calls
    ]

    stop_reason = "tool_use" if tool_calls else "end_turn"
    usage = None
    if resp.usage:
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
        }
        extra = resp.usage.model_extra or {}
        if "prompt_cache_hit_tokens" in extra:
            usage["prompt_cache_hit_tokens"] = extra["prompt_cache_hit_tokens"]
        if "prompt_cache_miss_tokens" in extra:
            usage["prompt_cache_miss_tokens"] = extra["prompt_cache_miss_tokens"]

    return LLMResponse(
        text=msg.content or "",
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        usage=usage,
    )


def _byok_claude(messages: list, tools: list) -> LLMResponse:
    import anthropic
    api_key = os.environ["LLM_API_KEY"]
    model   = os.environ["LLM_MODEL"]

    client = anthropic.Anthropic(api_key=api_key)

    system_content = ""
    claude_messages = []
    for m in messages:
        if m["role"] == "system":
            system_content = m["content"]
        else:
            claude_messages.append(m)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 8192,
        "messages": claude_messages,
    }
    if system_content:
        kwargs["system"] = system_content
    if tools:
        kwargs["tools"] = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {}),
            }
            for t in tools
        ]

    logger.debug("[llm_client/byok/claude] model=%s messages=%d", model, len(claude_messages))
    resp = client.messages.create(**kwargs)

    text = ""
    tool_calls = []
    for block in resp.content:
        if block.type == "text":
            text = block.text
        elif block.type == "tool_use":
            import json
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": json.dumps(block.input, ensure_ascii=False),
                },
            })

    stop_reason = "tool_use" if tool_calls else "end_turn"
    usage = {
        "prompt_tokens": resp.usage.input_tokens,
        "completion_tokens": resp.usage.output_tokens,
    } if resp.usage else None

    return LLMResponse(text=text, stop_reason=stop_reason, tool_calls=tool_calls, usage=usage)


def _call_byok(messages: list, tools: list) -> LLMResponse:
    provider = os.environ.get("LLM_PROVIDER", "").lower()
    if not provider:
        raise RuntimeError("LLM_PROVIDER not set — required when LLM_API_KEY is configured")
    if provider == "claude":
        return _byok_claude(messages, tools)
    if provider in _PROVIDER_BASE_URL:
        return _byok_openai(messages, tools)
    raise RuntimeError(f"Unknown LLM_PROVIDER={provider!r}, supported: deepseek, glm, gpt/openai, claude")


# ── 统一入口 ──────────────────────────────────────────────────────────────────

async def call(model_key: str, messages: list, tools: Optional[list] = None, session_id: str = "", round_id: str = "") -> LLMResponse:
    """有 LLM_API_KEY 走 BYOK，否则走服务器模式。model_key 在 BYOK 模式下忽略。"""
    if os.environ.get("LLM_API_KEY"):
        return await asyncio.to_thread(_call_byok, messages, tools or [])
    return await asyncio.to_thread(_post, model_key, messages, tools or [], session_id, round_id)

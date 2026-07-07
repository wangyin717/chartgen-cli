"""ReAct loop：LLM → tool_calls → observe → 循环直到 end_turn。

Plan mode 通过 tool_filter + user_prefix 参数实现，不需要独立函数。
System prompt 完全静态（只有日期每天变一次），最大化 KV cache 命中率。
"""
import asyncio
import hashlib
import json
import logging
import queue
import threading
from typing import Generator, Optional

logger = logging.getLogger(__name__)

from chartgen_cli.agent_loop import llm_client
from chartgen_cli.tools.tools import TOOL_SCHEMAS, dispatch, get_thinking
from chartgen_cli.tools import memory as _memory
from chartgen_cli.session import Session
from chartgen_cli.agent_loop.prompt import get_system_prompt, PLAN_USER_PREFIX

# ── 循环控制 ──────────────────────────────────────────────────────────────────
MAX_STEPS = 20
_LOOP_WARN = 3
_LOOP_STOP = 6
_SENTINEL = object()

# ── ANSI 颜色 ─────────────────────────────────────────────────────────────────
_GRAY  = "\033[38;5;245m"
_RESET = "\033[0m"
_BOLD  = "\033[1m"

# ── chunk 类型前缀（main.py 用来区分渲染方式）────────────────────────────────
TOOL_PREFIX   = "\x00T\x00"
THINK_PREFIX  = "\x00K\x00"
RESULT_PREFIX = "\x00R\x00"
FINAL_PREFIX  = "\x00F\x00"
USAGE_PREFIX  = "\x00U\x00"


_C  = "\033[0m"   # reset
_CC = "\033[34m"  # blue  — input
_CG = "\033[32m"  # green — cache hit
_CY = "\033[33m"  # yellow — cache write
_CM = "\033[95m"  # purple — output


def _fk(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_usage(usage: dict) -> str:
    """三种 usage 格式并存，按字段探测路由：
    - OpenAI 风格（glm/gpt via _byok_openai）：prompt_tokens / completion_tokens /
      prompt_tokens_details.cached_tokens
    - DeepSeek 风格（deepseek via _byok_openai，KV cache 用专属字段）：prompt_tokens /
      completion_tokens / prompt_cache_hit_tokens（不走 prompt_tokens_details）
      https://api-docs.deepseek.com/zh-cn/guides/kv_cache
    - Claude 风格（claude via _byok_claude，或服务器模式转发 Claude）：input_tokens /
      cache_read_input_tokens / output_tokens
    """
    if "input_tokens" in usage:
        net = usage.get("input_tokens", 0)
        cached = usage.get("cache_read_input_tokens", 0)
        completion = usage.get("output_tokens", 0)
    elif "prompt_cache_hit_tokens" in usage:
        prompt = usage.get("prompt_tokens", 0)
        cached = usage.get("prompt_cache_hit_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        net = prompt - cached
    else:
        prompt = usage.get("prompt_tokens", 0)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        net = prompt - cached
    return (
        f"{_CC}↑ {_fk(net)} tokens{_C}"
        f"({_CG}cached {_fk(cached)} tokens{_C})"
        f" · {_CM}↓ {_fk(completion)} tokens{_C}"
    )

# ── Plan mode ─────────────────────────────────────────────────────────────────
PLAN_TOOL_FILTER = {"ci", "bash_executor"}


# ── 循环检测 ──────────────────────────────────────────────────────────────────
def _hash_calls(tool_calls: list) -> str:
    """对一组 tool_calls 做确定性哈希，用于检测重复调用。"""
    blobs = sorted(
        json.dumps({
            "name": tc.get("function", {}).get("name", ""),
            "args": tc.get("function", {}).get("arguments", ""),
        }, sort_keys=True)
        for tc in tool_calls
    )
    return hashlib.md5("|".join(blobs).encode()).hexdigest()[:10]


# ── ReAct 主循环 ──────────────────────────────────────────────────────────────
def react_loop(
    user_query: str,
    history: list[dict],
    *,
    session: Session,
    on_history_update=None,
    stream: bool = True,
    tool_filter: Optional[set[str]] = None,
    user_prefix: str = "",
) -> Generator[str, None, list[dict]]:
    """
    执行一轮 ReAct loop，以 generator 形式实时 yield 输出片段。

    tool_filter: 限制可用工具名集合，None 表示允许全部工具。
    user_prefix: 拼接到 user message 最前面的指令（用于 plan mode 等），不影响 system prompt。
    on_history_update: 每轮结束后用新 history 调用的回调，用于持久化到磁盘。
    返回更新后的 history（通过 StopIteration.value）。
    """
    q: queue.Queue = queue.Queue()
    new_history: list[dict] = list(history)
    exc_holder: list = []

    active_schemas = (
        [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in tool_filter]
        if tool_filter is not None else TOOL_SCHEMAS
    )
    # plan mode 判断：传了 tool_filter 就是 plan mode，结束时不写 memory
    is_plan_mode = tool_filter is not None
    # user_prefix 直接拼到 user message 最前面（plan mode 指令、修改指令等），不进 system prompt
    full_user_query = (user_prefix + user_query) if user_prefix else user_query

    async def _run() -> None:
        import secrets
        from datetime import datetime as _dt
        round_id = f"round-{_dt.utcnow().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
        # system prompt 每天只变一次（日期），最大化 KV cache 命中率
        messages = [{"role": "system", "content": get_system_prompt(session)}, *history]
        messages.append({"role": "user", "content": full_user_query})

        loop_window: list[str] = []
        loop_warned: set[str] = set()

        def emit(text: str) -> None:
            q.put(text)

        logger.info("[React Loop Agent] start loop: query=%r plan_mode=%s", user_query[:80], is_plan_mode)

        for step in range(1, MAX_STEPS + 1):
            logger.debug("[React Loop Agent] step=%d", step)

            response = await llm_client.call(
                "PLANNER",
                messages,
                tools=active_schemas,
                session_id=session.session_id,
                round_id=round_id,
            )

            if response.usage:
                logger.debug("[React Loop Agent] usage=%s", response.usage)
                emit(USAGE_PREFIX + _fmt_usage(response.usage))

            if response.stop_reason == "end_turn":
                if response.text:
                    emit(FINAL_PREFIX + response.text)
                logger.info("[React Loop Agent] end_turn at step=%d", step)
                messages.append({"role": "assistant", "content": response.text or ""})
                # plan mode 不写 memory（plan 对话是中间态，不是最终答案）
                if not is_plan_mode:
                    _memory.record(user_query, response.text or "")
                break

            if response.stop_reason == "tool_use":
                tool_calls = response.tool_calls

                h = _hash_calls(tool_calls)
                loop_window.append(h)
                if len(loop_window) > 20:
                    loop_window.pop(0)
                count = loop_window.count(h)

                if count >= _LOOP_STOP and h not in loop_warned:
                    loop_warned.add(h)
                    logger.warning("[React Loop Agent] forced stop: hash=%s count=%d step=%d", h, count, step)
                    emit("\n[forced stop] repeated tool calls exceeded limit, generating final answer.\n")
                    messages.append({"role": "assistant", "content": response.text or "", "tool_calls": tool_calls})
                    messages.append({
                        "role": "tool",
                        # 用第一个 tool_call 的 id 闭合，避免 API 报 tool message 孤立错误
                        "tool_call_id": (tool_calls[0].get("id") or "") if tool_calls else "",
                        "content": "[FORCED STOP] Please provide final answer now.",
                    })
                    break

                if count >= _LOOP_WARN and h not in loop_warned:
                    loop_warned.add(h)
                    logger.warning("[React Loop Agent] loop detected: hash=%s count=%d step=%d", h, count, step)
                    emit("\n[warning] repeated tool calls detected, try a different approach.\n")

                if response.text:
                    emit(THINK_PREFIX + response.text)

                messages.append({"role": "assistant", "content": response.text or "", "tool_calls": tool_calls})

                for tc in tool_calls:
                    fn   = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    thinking_text = get_thinking(name, args).replace("\n", " ").replace("\r", "")
                    emit(TOOL_PREFIX + f"{name}({thinking_text})\n")
                    logger.info(f"[React Loop Agent] tool call: name={name} args={json.dumps(args, ensure_ascii=False)}")
                    result = await asyncio.to_thread(dispatch, name, args)
                    logger.info(f"[React Loop Agent] tool result: name={name} result={result}")
                    emit(RESULT_PREFIX + name + "\x01" + result)
                    messages.append({"role": "tool", "tool_call_id": tc.get("id") or "", "content": result})
                continue

            if response.text:
                emit(FINAL_PREFIX + response.text)
            messages.append({"role": "assistant", "content": response.text or ""})
            break

        else:
            logger.warning("[React Loop Agent] max steps reached: query=%r", user_query[:80])
            emit("\n[warning] max steps reached.\n")

        # history 去掉 system prompt，只保留 user/assistant/tool 轮次
        new_history.clear()
        new_history.extend(m for m in messages if m.get("role") != "system")
        if on_history_update:
            on_history_update(new_history)

    def _thread_main():
        try:
            asyncio.run(_run())
        except Exception as e:
            exc_holder.append(e)
        finally:
            q.put(_SENTINEL)

    t = threading.Thread(target=_thread_main, daemon=True)
    t.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield item

    t.join()

    if exc_holder:
        raise exc_holder[0]

    return new_history

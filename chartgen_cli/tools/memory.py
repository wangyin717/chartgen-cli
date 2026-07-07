"""Memory tool: 存储（LLM 提炼 + 追加写本地 md）+ 召回（LLM 生成 grep 命令 + subprocess 执行）。

存储：每轮 react_loop 结束后 fire-and-forget，写入 workspace/memory/YYYYMMDD.md。
召回：注册为 memory_retrieve tool，LLM 主动决策何时调用。
"""
import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import date, datetime

from chartgen_cli.agent_loop import llm_client

_MEMORY_DIR = os.path.join(os.path.dirname(__file__), "..", "workspace", "memory")
_MEMORY_DIR = os.path.normpath(_MEMORY_DIR)

# ── prompt 模板（内联，避免再依赖 swiftagent 的 yaml 路径）────────────────────

_EXTRACT_PROMPT = """\
# Task: Data Analysis Conversation Log Processing

Your task is to extract and record key information from the conversation between \
the user and the data analysis agent, then generate a concise memory log for future \
personalized analysis.

## Input
User query (user_query): {user_query}
Summary (summary): {summary}
Timestamp: {timestamp}

## What to Record
Focus on the following information types:
1. Analysis requests and goals, data scope, key metrics
2. Completed analysis outputs, chart/report conclusions, data processing operations
3. User preferences and habits (chart types, colors, formats) — only from explicit \
   statements in user_query, not inferred from summary
4. Issues encountered and how they were resolved
5. TODOs and follow-up directions

## Writing Principles
- Time normalization: convert relative expressions to absolute dates using today {today}
- Atomic split: one item = one fact
- Semantic completion: each item must be self-contained
- No double quotes; use single quotes when emphasis is needed
- Remove casual chat; keep exact numbers and conclusions; stay objective
- Output language must match the primary language of user_query and summary
- If there is nothing worth remembering, return nothing

## Output Format
```markdown
## {timestamp} - [Analysis Topic]
Brief description of the core analysis objective

### Analysis Output
- Output 1: key finding

### User Preferences
- Preference 1

### Follow-up
- TODO item (if any)
```

If a section has no relevant content, omit it. Keep it under 300 words.
"""

_GREP_PROMPT = """\
# Task: Query Rewriting and Grep Command Generation

Parse the user query, extract keywords, perform semantic expansion, and output a \
robust grep command to search local memory files.

Memory directory: {memory_dir}
Today: {today} (weekday: {weekday})

## 1. Determine time_range from the query
- Specific date(s): `YYYYMMDD` or `YYYYMMDD,YYYYMMDD`
- Date range: `YYYYMMDD-YYYYMMDD`
- Vague references ("last time", "before", "recently"): `all_memory`

## 2. Keyword expansion
- Synonyms, abbreviations, related terms
- Split compound concepts into minimal units
- Include the user's primary language in output

## 3. Grep command requirements
- Single-line shell command, runs directly via subprocess
- Use `grep -HrnEiI`
- Join keywords with `|` inside double quotes
- Must not error when files are absent (`|| true`)
- Pipe final output through `| head -n 20` to limit results
- File scope must follow time_range:

  - `all_memory`: search all `{memory_dir}/*.md`
  - `YYYYMMDD`: search only `{memory_dir}/YYYYMMDD.md`
  - `YYYYMMDD-YYYYMMDD`: search each date file in range

## Output (strict JSON, no markdown)
{{"keywords": [...], "grep_command": "..."}}

## Input
query: {query}
"""


# ── 存储 ──────────────────────────────────────────────────────────────────────

async def _extract_memory_async(user_query: str, summary: str) -> str:
    """调用 LLM 从 query+summary 里提炼记忆条目，返回 markdown 字符串。"""
    now = datetime.now()
    prompt = _EXTRACT_PROMPT.format(
        user_query=user_query,
        summary=summary,
        timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
        today=date.today().isoformat(),
    )
    resp = await llm_client.call("QUICK_SUMMARY", [{"role": "user", "content": prompt}])
    return resp.text.strip()


def _append_to_memory_file(content: str) -> None:
    """把 content 追加写入今天的 memory 文件。"""
    if not content:
        return
    os.makedirs(_MEMORY_DIR, exist_ok=True)
    file_path = os.path.join(_MEMORY_DIR, f"{date.today().strftime('%Y%m%d')}.md")
    try:
        existing = open(file_path, encoding="utf-8").read() if os.path.exists(file_path) else ""
    except OSError:
        existing = ""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(existing + ("\n\n" if existing else "") + content)


def record(user_query: str, summary: str) -> None:
    """fire-and-forget：在 daemon thread 里提炼并写入记忆，不阻塞调用方。"""
    import threading

    def _run():
        try:
            content = asyncio.run(_extract_memory_async(user_query, summary))
            _append_to_memory_file(content)
            logging.info("[Memory] recorded %d chars", len(content))
        except Exception:
            logging.warning("[Memory] record failed", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()


# ── 召回 ──────────────────────────────────────────────────────────────────────

async def _get_grep_command_async(query: str) -> str:
    """调用 LLM 生成 grep 命令，返回命令字符串。"""
    today = date.today()
    prompt = _GREP_PROMPT.format(
        memory_dir=_MEMORY_DIR,
        today=today.isoformat(),
        weekday=today.weekday(),
        query=query,
    )
    resp = await llm_client.call("QUICK_SUMMARY", [{"role": "user", "content": prompt}])
    raw = resp.text.strip()
    # 清理可能的 markdown 包裹
    if raw.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        raw = m.group(1) if m else raw
    try:
        return json.loads(raw).get("grep_command", "")
    except (json.JSONDecodeError, TypeError):
        return ""


def _run_grep(grep_command: str) -> list[str]:
    """本地执行 grep 命令，返回命中行列表。"""
    if not grep_command:
        return []
    try:
        result = subprocess.run(
            grep_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode in (0, 1):  # 1 = no match，合法
            return [line for line in result.stdout.strip().splitlines() if line]
        logging.warning("[Memory] grep error: %s", result.stderr[:200])
        return []
    except subprocess.TimeoutExpired:
        logging.warning("[Memory] grep timeout")
        return []


def _get_containing_block(grep_result: str, file_cache: dict[str, list[str]]) -> str | None:
    """从 grep 结果行（file:lineno:content）里提取完整的 ## 块。"""
    parts = grep_result.split(":")
    if len(parts) < 2:
        return None
    file_path = parts[0]
    if "long_memory" not in file_path:
        try:
            hit_idx = int(parts[1]) - 1  # grep 行号从 1 开始
        except (ValueError, IndexError):
            return None
        if file_path not in file_cache:
            try:
                with open(file_path, encoding="utf-8") as f:
                    file_cache[file_path] = f.readlines()
            except OSError:
                return None
        lines = file_cache[file_path]
        # 向上找 ## 开头的块起始
        start = hit_idx
        while start >= 0 and not lines[start].startswith("## "):
            start -= 1
        # 向下找下一个 ## 或文件末尾
        end = hit_idx + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        return "".join(lines[start:end]).strip()
    return grep_result  # long_memory 直接返回原行


def retrieve(query: str) -> str:
    """同步召回：生成 grep 命令 → 执行 → 提取块 → 返回拼接结果。"""
    grep_command = asyncio.run(_get_grep_command_async(query))
    logging.info("[Memory] grep_command: %s", grep_command)
    raw_results = _run_grep(grep_command)

    file_cache: dict[str, list[str]] = {}
    seen: set[str] = set()
    blocks: list[str] = []
    for result in raw_results:
        block = _get_containing_block(result, file_cache)
        if block:
            title = block.splitlines()[0].strip()
            if title not in seen:
                seen.add(title)
                blocks.append(block)

    if not blocks:
        return "No relevant memory found."

    prefix = (
        "Below is retrieved implicit memory. Integrate relevant background, "
        "preferences, or historical facts into your response.\n"
        f"Today: {date.today().isoformat()}\n\n"
        "Priority: today's entries > long_memory > older dated entries. "
        "Among same-day entries, later timestamp wins.\n\n"
    )
    return prefix + "\n\n---\n\n".join(blocks)


# ── Tool schema & dispatch ────────────────────────────────────────────────────

SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_retrieve",
        "description": (
            "Retrieve relevant implicit memory (user preferences, past analysis, decisions) "
            "for the current query. Call this when the user references past work, asks about "
            "preferences, or when personalization would improve the response."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "current_query": {
                    "type": "string",
                    "description": "The current user query to retrieve memory for.",
                }
            },
            "required": ["current_query"],
        },
    },
}


def thinking(args: dict) -> str:
    q = args.get("current_query", "")
    return f'"{q[:40]}..."' if len(q) > 40 else f'"{q}"'


def run(current_query: str) -> str:
    return retrieve(current_query)

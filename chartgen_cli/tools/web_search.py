import logging
import os
import re
import time

from perplexity import Perplexity

# 注：PERPLEXITY_API_KEY 由 main.py 启动时通过 config.load_config() 从
# ~/.chartgen/config 加载进 os.environ，这里不再自行 load_dotenv()
# （旧版本在此处隐式加载 CWD 下的 .env，容易和新的用户级配置文件产生歧义）。

logger = logging.getLogger(__name__)

_workspace_dir: str = ""


def set_workspace(path: str) -> None:
    global _workspace_dir
    _workspace_dir = path


def _slug(query: str) -> str:
    return re.sub(r"[\s/\\:*?\"<>|]+", "_", query).strip("_")[:60]


def _save_results(query: str, content: str) -> str | None:
    if not _workspace_dir:
        return None
    ws_dir = os.path.join(_workspace_dir, "websearch")
    os.makedirs(ws_dir, exist_ok=True)
    fname = f"{int(time.time())}_{_slug(query)}.md"
    path = os.path.join(ws_dir, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {query}\n\n{content}")
        return path
    except OSError:
        return None


def thinking(args: dict) -> str:
    return f"searching: {args.get('query', '')}"


def run(query: str) -> str:
    logger.info("[web_search] query=%r", query)
    if not os.environ.get("PERPLEXITY_API_KEY"):
        return (
            "[error] web_search 不可用：未配置 PERPLEXITY_API_KEY。"
            "请在 ~/.chartgen/config 中填入该 API key 后重试。"
        )
    try:
        client = Perplexity()
        search = client.search.create(query=[query], max_results=5)
        parts = []
        for result in search.results:
            parts.append(f"**{result.title}**\n{result.snippet}\n{result.url}")
        content = "\n\n".join(parts) if parts else "no results found"
        logger.info("[web_search] results=%d content_len=%d", len(parts), len(content))
    except Exception as e:
        logger.warning("[web_search] failed: %s", e, exc_info=True)
        return f"search failed: {e}"

    path = _save_results(query, content)
    if path:
        logger.info("[web_search] saved to %s", path)
        snippet = content[:300] + ("..." if len(content) > 300 else "")
        return f"[saved to {path}, use bash_executor tool to view full content]\n\n{snippet}"
    return content


SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information. Use whenever: (1) the user explicitly asks to search / for the latest info / to fetch a specific page, OR (2) you need external data (news, prices, stats, facts) that domain_data's fixed domains don't cover.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user's original search request or question. Preserve the user's original wording as much as possible; do not excessively rephrase."}
            },
            "required": ["query"],
        },
    },
}

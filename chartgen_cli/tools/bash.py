import logging
import subprocess

logger = logging.getLogger(__name__)

def thinking(args: dict) -> str:
    command = args.get("command", "")
    return "$ " + command.strip().splitlines()[0][:80]


def run(command: str) -> str:
    logger.info("[Bash] cmd: %s", command.strip().splitlines()[0][:120])
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
        )
        parts = []
        if r.stdout.strip():
            parts.append(r.stdout.strip())
        if r.stderr.strip():
            parts.append(f"[stderr]\n{r.stderr.strip()}")
        logger.info("[Bash] exit=%d", r.returncode)
        return "\n".join(parts) or "(no output)"
    except subprocess.TimeoutExpired:
        logger.warning("[Bash] timeout after 30s")
        return "[error] 执行超时（30s）"
    except Exception as e:
        logger.warning("[Bash] error: %s", e, exc_info=True)
        return f"[error] {e}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash_executor",
        "description": "在本地 shell 执行命令，返回 stdout/stderr。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"}
            },
            "required": ["command"],
        },
    },
}

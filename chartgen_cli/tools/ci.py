import contextlib
import io
import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

_workspace_dir: str = ""  # 由 agent.py 在启动时注入


def set_workspace(path: str) -> None:
    global _workspace_dir
    _workspace_dir = path


def thinking(args: dict) -> str:
    code = args.get("code", "")
    return "python: " + code.strip().splitlines()[0][:80]


# 后序注入：扫描局部变量，把 DataFrame 序列化到 workspace
_EPILOGUE = """
import pickle as _pkl, os as _os, pandas as _pd
_ws = {workspace_dir!r}
_saved = []
for _k, _v in list(locals().items()):
    if not _k.startswith('_') and isinstance(_v, _pd.DataFrame) and not _v.empty:
        _pkl.dump(_v, open(_os.path.join(_ws, _k + '.pkl'), 'wb'))
        _saved.append(f"{{_k}} ({{len(_v)}} rows x {{len(_v.columns)}} cols)")
if _saved:
    print("\\n[saved to workspace]", ", ".join(_saved))
"""


_OUTPUT_LIMIT = 2000


def _save_code(code: str) -> str | None:
    if not _workspace_dir:
        return None
    ci_dir = os.path.join(_workspace_dir, "ci")
    os.makedirs(ci_dir, exist_ok=True)
    path = os.path.join(ci_dir, f"{int(time.time() * 1000)}.py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        return path
    except OSError:
        return None


def _run_inline(code: str, timeout: int = 60) -> str:
    """在进程内 exec 代码（PyInstaller 打包后 sys.executable 指向自身，不能用 subprocess）。"""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    def target():
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(code, "<ci>", "exec"), {})  # noqa: S102
        except Exception:
            import traceback
            stderr_buf.write(traceback.format_exc())

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        logger.warning("[CI] inline timeout after %ds", timeout)
        return "[error] 执行超时（60s）"

    parts = []
    if stdout_buf.getvalue().strip():
        parts.append(stdout_buf.getvalue().strip())
    if stderr_buf.getvalue().strip():
        parts.append(f"[stderr]\n{stderr_buf.getvalue().strip()}")
    output = "\n".join(parts) or "(no output)"
    if len(output) > _OUTPUT_LIMIT:
        output = output[:_OUTPUT_LIMIT] + f"\n... (truncated, {len(output)} chars total. Save large output to a workspace file instead of printing.)"
    return output


def run(code: str) -> str:
    logger.info("[CI] exec: %s", code.strip().splitlines()[0][:120])
    _save_code(code)
    if not _workspace_dir:
        full_code = code
    else:
        full_code = code + "\n" + _EPILOGUE.format(workspace_dir=_workspace_dir)

    # PyInstaller 打包后 sys.frozen=True，sys.executable 指向 chartgen 自身，
    # 用 subprocess 会重新进入交互界面，改为进程内 exec。
    if getattr(sys, "frozen", False):
        result = _run_inline(full_code)
        logger.info("[CI] inline exec done, output=%d chars", len(result))
        return result

    try:
        r = subprocess.run(
            [sys.executable, "-c", full_code],
            capture_output=True, text=True, timeout=60,
        )
        parts = []
        if r.stdout.strip():
            parts.append(r.stdout.strip())
        if r.stderr.strip():
            parts.append(f"[stderr]\n{r.stderr.strip()}")
        output = "\n".join(parts) or "(no output)"
        logger.info("[CI] exit=%d stdout=%d chars stderr=%d chars",
                    r.returncode, len(r.stdout), len(r.stderr))
        if len(output) > _OUTPUT_LIMIT:
            output = output[:_OUTPUT_LIMIT] + f"\n... (truncated, {len(output)} chars total. Save large output to a workspace file instead of printing.)"
        return output
    except subprocess.TimeoutExpired:
        logger.warning("[CI] timeout after 60s")
        return "[error] 执行超时（60s）"
    except Exception as e:
        logger.warning("[CI] error: %s", e, exc_info=True)
        return f"[error] {e}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "ci",
        "description": (
                    "在本地执行 Python 代码，返回 stdout/stderr（最多 2000 字符）。"
                    "HTML、大型 JSON、DataFrame 等大输出请写入 workspace 文件而非 print，"
                    "避免输出内容撑大上下文。"
                ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要运行的 Python 代码"}
            },
            "required": ["code"],
        },
    },
}

#!/usr/bin/env python3
"""chartgen CLI 入口。"""
import os
import sys
import threading
import itertools
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as _Text

try:
    import readline
    _HISTORY_FILE = os.path.expanduser("~/.chartgen_history")
    try:
        readline.read_history_file(_HISTORY_FILE)
    except (FileNotFoundError, PermissionError, OSError):
        pass
    import atexit
    atexit.register(readline.write_history_file, _HISTORY_FILE)

except ImportError:
    pass

import logging
logging.root.handlers = []
logging.root.addHandler(logging.NullHandler())
logging.root.setLevel(logging.WARNING)  # 临时 guard，session 启动后换成 FileHandler
logging.getLogger("markdown_it").setLevel(logging.WARNING)

from chartgen_cli.agent_loop.agent import react_loop, TOOL_PREFIX, THINK_PREFIX, RESULT_PREFIX, FINAL_PREFIX, PLAN_USER_PREFIX, USAGE_PREFIX
from chartgen_cli.session import create_session, resume_session, list_sessions, update_last_active, load_history, save_history
from chartgen_cli.tools import ci as _ci, chart_visualization as _cv, generate_ppt as _gp, domain_data as _dd, generate_html as _gh, web_search as _ws
from chartgen_cli.tools.generate_ppt import _STDOUT_LOCK as _ppt_stdout_lock
from chartgen_cli.config import load_config, check_llm_configured

_console = Console()

# ANSI color codes
_R = "\033[0m"           # reset
_BOLD = "\033[1m"
_GREEN = "\033[38;5;82m"
_YELLOW = "\033[38;5;220m"
_GRAY = "\033[38;5;245m"
_BANNER = None  # replaced by _make_banner()


def _make_banner() -> Panel:
    cwd = os.getcwd().replace(os.path.expanduser("~"), "~")

    left = _Text()
    left.append("\n")
    left.append("     Welcome back!\n", style="bold")
    left.append("\n")
    left.append("       Chart", style="bold bright_blue")
    left.append("Gen\n", style="bold cyan")
    left.append("       ,___,\n", style="bright_blue")
    left.append("       (o,o)\n", style="cyan")
    left.append("       /)_)\n", style="bright_blue")
    left.append('        " "\n', style="cyan")
    left.append("\n")
    left.append("  AI CLI assistant\n", style="dim")
    left.append("  powered by Claude\n", style="dim")
    left.append(f"  {cwd}\n", style="dim")

    right = _Text()
    right.append("\n")
    right.append("   Tips for getting started\n", style="bold bright_blue")
    right.append("   Run /help to show commands\n")
    right.append("   Run /plan <task> to draft\n")
    right.append("   an execution plan\n")
    right.append("\n")
    right.append("   " + "─" * 40 + "\n", style="bright_blue")
    right.append("\n")
    right.append("   Tools\n", style="bold bright_blue")
    for line in (
        ("ci", "bash_executor", "web_search"),
        ("chart_visualization", "generate_ppt"),
        ("domain_data", "generate_html"),
    ):
        right.append("   ")
        for tool in line:
            right.append(f"{tool}  ", style="bold green")
        right.append("\n")
    right.append("\n\n")
    right.append("   Commands\n", style="bold bright_blue")
    right.append("   ")
    for cmd in ("/clear", "/help", "/exit", "/plan"):
        right.append(f"{cmd}  ", style="bold yellow")
    right.append("\n")

    body = Table.grid(expand=True)
    body.add_column(width=26)
    body.add_column(width=3)
    body.add_column(ratio=1)
    body.add_row(left, _Text(" │ \n" * 16, style="bright_blue"), right)

    return Panel(
        body,
        title="[bold bright_blue]Chart[/bold bright_blue][bold cyan]Gen[/bold cyan]",
        border_style="bright_blue",
        padding=(0, 1),
    )

_COMMANDS = {
    "/exit": "exit",
    "/quit": "exit",
    "/clear": "clear conversation history",
    "/copy":  "copy last response to clipboard",
    "/help":  "show help",
    "/plan":  "generate execution plan before running, usage: /plan <query>",
}


def _copy_to_clipboard(text: str) -> None:
    """跨平台剪贴板拷贝：macOS 用 pbcopy，Linux 用 xclip/xsel（v1 不支持原生 Windows，见 install.sh）。"""
    import platform
    import subprocess

    system = platform.system()
    if system == "Darwin":
        cmd = ["pbcopy"]
    elif system == "Linux":
        if _which("xclip"):
            cmd = ["xclip", "-selection", "clipboard"]
        elif _which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
        else:
            print(f"  {_GRAY}(no xclip/xsel found, skip clipboard copy){_R}")
            return
    else:
        print(f"  {_GRAY}(clipboard copy not supported on {system}){_R}")
        return
    subprocess.run(cmd, input=text.encode(), check=False)


def _which(binary: str) -> bool:
    import shutil
    return shutil.which(binary) is not None


class _Spinner:
    """在后台线程里跑一个行内 spinner，支持 freeze_with_text 原地替换文字。"""
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._current_char = self._FRAMES[0]
        self._start_time = None
        self._token_info: str = ""

    def _run(self):
        for ch in itertools.cycle(self._FRAMES):
            if self._stop.is_set():
                break
            self._current_char = ch
            elapsed = int(time.time() - self._start_time) if self._start_time else 0
            token_part = f" · {self._token_info}" if self._token_info else ""
            sys.stdout.write(f"\r{ch} thinking... ({elapsed}s{token_part})")
            sys.stdout.flush()
            time.sleep(0.08)

    def start(self):
        if not self._started:
            self._started = True
            self._start_time = time.time()
            self._thread.start()

    def set_tokens(self, token_info: str) -> None:
        """收到 usage 后更新 token 显示，spinner 继续转。"""
        self._token_info = token_info

    def stop(self, print_usage: bool = False):
        """停止 spinner 并清空行，幂等。"""
        self._stop_tool()
        if not self._stop.is_set():
            self._stop.set()
            self._thread.join()
        elapsed = int(time.time() - self._start_time) if self._start_time else 0
        if print_usage and self._token_info and elapsed >= 10:
            sys.stdout.write("\r" + " " * 48 + f"\r  [{elapsed}s · {self._token_info}]\n")
        else:
            sys.stdout.write("\r" + " " * 48 + "\r")
        sys.stdout.flush()

    def freeze_with_text(self, text: str) -> None:
        """停止 spinner，用当前帧字符 + text 原地替换 thinking... 行。"""
        ch = self._current_char
        if not self._stop.is_set():
            self._stop.set()
            self._thread.join()
        sys.stdout.write("\r" + " " * 48 + f"\r{ch} {text}\n")
        sys.stdout.flush()

    def print_line(self, text: str) -> None:
        """停大 spinner，在同一行原地转 -\\|/，工具名固定显示在后面。"""
        if not self._stop.is_set():
            self._stop.set()
            self._thread.join()
        # 只取第一行，截断到终端宽度，避免换行导致 \r 覆盖失效
        single_line = text.splitlines()[0] if text else text
        max_width = 100
        display = (single_line[:max_width] + "...") if len(single_line) > max_width else single_line
        self._tool_text = display
        self._tool_stop = threading.Event()

        def _tool_spin():
            pad = " " * (len(self._tool_text) + 8)
            for ch in itertools.cycle("-\\|/"):
                if self._tool_stop.is_set():
                    break
                with _ppt_stdout_lock:
                    sys.stdout.write(f"\r{_GRAY}└ {ch}{_R} {self._tool_text}")
                    sys.stdout.flush()
                time.sleep(0.12)
            # 结果到来：清行，打印静态行（无旋转字符）
            sys.stdout.write(f"\r{pad}\r{_GRAY}└{_R} {self._tool_text}\n")
            sys.stdout.flush()

        self._tool_thread = threading.Thread(target=_tool_spin, daemon=True)
        self._tool_thread.start()

    def _stop_tool(self) -> None:
        """停掉 tool spinner（如果有），打印最终静态工具名行。"""
        t = getattr(self, "_tool_stop", None)
        if t and not t.is_set():
            t.set()
            self._tool_thread.join()

    def restart(self, print_usage: bool = False) -> None:
        elapsed = int(time.time() - self._start_time) if self._start_time else 0
        self._stop_tool()
        if not self._stop.is_set():
            self._stop.set()
            self._thread.join()
        if print_usage and self._token_info and elapsed >= 10:
            sys.stdout.write("\r" + " " * 48 + f"\r  [{elapsed}s · {self._token_info}]\n")
        else:
            sys.stdout.write("\r" + " " * 48 + "\r")
        sys.stdout.flush()
        self.__init__()
        self.start()


class _KeyboardListener:
    """在 agent 执行期间监听 ESC 键，检测到后设置 cancel_event。仅在 TTY 环境下激活。"""

    def __init__(self, cancel_event: threading.Event):
        self._cancel_event = cancel_event
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._active = sys.stdin.isatty()

    def start(self):
        if self._active:
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._active and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _run(self):
        import tty
        import termios
        import select
        fd = sys.stdin.fileno()
        old_settings = None
        try:
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            while not self._stop.is_set():
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    ch = os.read(fd, 1)
                    if ch == b'\x1b':
                        self._cancel_event.set()
                        break
        except Exception:
            pass
        finally:
            if old_settings is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass


def _cmd_resume(args: list[str]) -> None:
    """处理 `chartgen resume [id]`：无参数时打印列表，有参数时进入指定 session。"""
    if not args:
        entries = list_sessions(limit=10)
        if not entries:
            print("No sessions found.")
            return
        print(f"\n  {'ID':<10} {'Created':<22} {'Last Active':<22} Directory")
        print(f"  {'-'*10} {'-'*20} {'-'*20} {'-'*30}")
        for e in entries:
            cwd = e.get("cwd", "")
            # 折叠 home 目录
            cwd = cwd.replace(os.path.expanduser("~"), "~")
            print(f"  {e['id']:<10} {e.get('created_at',''):<22} {e.get('last_active_at',''):<22} {cwd}")
        print(f"\n  Usage: chartgen resume <id>")
        return

    session_id = args[0]
    session = resume_session(session_id)
    if session is None:
        print(f"[error] Session not found: {session_id}")
        return
    _run_session(session, resumed=True)


def _run_session(session, *, resumed: bool = False) -> None:
    """启动交互循环，退出时更新 last_active_at。"""
    # 日志落在项目级 chartgen_workspace/ 下，而非安装位置（site-packages 通常只读/多用户共享）
    _log_dir = os.path.join(os.getcwd(), "chartgen_workspace", "log")
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, f"{session.session_id}.log")
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.root.addHandler(_fh)
    logging.root.setLevel(logging.DEBUG)

    _ci.set_workspace(session.workspace_dir)
    _cv.set_dirs(session.workspace_dir, session.outputs_dir)
    _gp.set_dirs(session.workspace_dir, session.outputs_dir)
    _dd.set_workspace(session.workspace_dir)
    _gh.set_dirs(session.workspace_dir, session.outputs_dir)
    _ws.set_workspace(session.workspace_dir)

    history: list[dict] = load_history(session)

    _console.print(_make_banner())
    status = "resumed" if resumed else "new"
    rounds = len([m for m in history if m.get("role") == "user"])
    rounds_info = f"  {rounds} turns in history" if rounds else ""
    if os.environ.get("LLM_API_KEY"):
        _model_info = f"  model: {os.environ.get('LLM_MODEL', '?')}  ({os.environ.get('LLM_PROVIDER', '?')})"
    else:
        _server = os.environ.get("SERVER_URL", "").rstrip("/")
        _model_info = f"  mode: server  ({_server})"
    _console.print(f"  [dim]session {session.session_id}  [{status}]{rounds_info}[/dim]")
    _console.print(f"  [dim]{_model_info}[/dim]\n")

    try:
        _chat_loop(session, history)
    finally:
        # 退出时更新 last_active_at（无论正常退出还是异常）
        update_last_active(session.session_id)


def _plan_confirm_menu() -> tuple[str, str]:
    """y/e/q 确认菜单。返回 ("y", "") | ("n", "") | ("e", modification_text)"""
    print(f"  {_YELLOW}[y]{_R} Execute  {_YELLOW}[e]{_R} Edit plan  {_YELLOW}[q]{_R} Cancel")
    try:
        choice = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "n", ""

    if choice == "y":
        return "y", ""
    elif choice == "e":
        try:
            modification = input("edit> ").strip()
        except (EOFError, KeyboardInterrupt):
            return "n", ""
        if not modification:
            return "n", ""
        return "e", modification
    else:
        return "n", ""


def _run_plan(session, history: list[dict], plan_query: str) -> None:
    """
    plan loop：每轮生成计划并累积 history。
    - 首轮：user = [进入Plan Mode] + query
    - 修改：user = [修改计划] + modification（上一轮 plan 回复已在 history 里）
    - 确认：user = [离开Plan Mode] + plan_text，带完整 plan history 进 react
    """
    plan_history = list(history)
    is_first_round = True

    while True:
        plan_history_snapshot = list(plan_history)
        cancel_event = threading.Event()
        listener = _KeyboardListener(cancel_event)
        spinner = _Spinner()
        final_buf: list[str] = []

        if is_first_round:
            current_query = plan_query
            current_prefix = PLAN_USER_PREFIX
            is_first_round = False
        # current_query / current_prefix 在修改分支里设置

        def _on_plan_history_update(h: list[dict]) -> None:
            nonlocal plan_history
            plan_history = h
            save_history(session, h)

        spinner.start()
        listener.start()
        try:
            gen = react_loop(
                current_query,
                plan_history,
                session=session,
                on_history_update=_on_plan_history_update,
                user_prefix=current_prefix,
                cancel_event=cancel_event,
            )
            while True:
                try:
                    chunk = next(gen)
                    if cancel_event.is_set():
                        break
                    if chunk.startswith(TOOL_PREFIX):
                        spinner.print_line(chunk[len(TOOL_PREFIX):].rstrip("\n"))
                    elif chunk.startswith(THINK_PREFIX):
                        text = chunk[len(THINK_PREFIX):].strip().splitlines()[0]
                        spinner.freeze_with_text(text)
                    elif chunk.startswith(RESULT_PREFIX):
                        spinner.stop()
                        body = chunk[len(RESULT_PREFIX):]
                        tool_name, _, result = body.partition("\x01")
                        lines = result.strip().splitlines()
                        for line in lines[:1]:
                            sys.stdout.write(f"{_GRAY}  ↳ {line}{_R}\n")
                        if len(lines) > 1:
                            sys.stdout.write(f"{_GRAY}  ↳ ... ({len(lines) - 1} more lines){_R}\n")
                        sys.stdout.flush()
                        spinner.restart(print_usage=True)
                    elif chunk.startswith(FINAL_PREFIX):
                        spinner.stop()
                        final_buf.append(chunk[len(FINAL_PREFIX):])
                    elif chunk.startswith(USAGE_PREFIX):
                        spinner.set_tokens(chunk[len(USAGE_PREFIX):])
                    else:
                        spinner.stop()
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                except StopIteration:
                    plan_text = "".join(final_buf)
                    break
        except Exception as e:
            print(f"\n[error] {e}")
            return
        finally:
            listener.stop()
            spinner.stop()

        if cancel_event.is_set():
            sys.stdout.write(f"\r{' ' * 48}\r^C  Cancelled.\n")
            sys.stdout.flush()
            plan_history = plan_history_snapshot
            save_history(session, plan_history_snapshot)
            print()
            return

        if final_buf:
            print()
            _console.print(Markdown("".join(final_buf)))
        print()

        # confirm with user
        action, modification = _plan_confirm_menu()
        if action == "y":
            approved_input = f"[Leave Plan Mode, execute the following plan]\n{plan_text or ''.join(final_buf)}"
            print()
            _run_react(session, plan_history, approved_input)
            return
        elif action == "e":
            current_query = f"[Modify plan, still in Plan Mode, output updated plan for user confirmation, do not execute]\n{modification}"
            current_prefix = ""
            continue
        else:
            # 追加退出标记，让模型知道这段 plan 被用户放弃
            # save_history(session, plan_history)
            _run_react(session, plan_history, "[Exit Plan Mode] User cancelled the plan.")
            print(f"\n  {_GRAY}Exited plan mode.{_R}\n")
            return


def _run_react(session, history: list[dict], user_input: str) -> list[dict]:
    """单次 react loop 执行，渲染输出，返回更新后的 history。取消时回滚并返回原始 history。"""
    history_snapshot = list(history)
    cancel_event = threading.Event()
    listener = _KeyboardListener(cancel_event)
    spinner = _Spinner()
    final_buf: list[str] = []

    def _render_result(tool_name: str, result: str) -> None:
        lines = result.strip().splitlines()
        for line in lines[:1]:
            sys.stdout.write(f"{_GRAY}  ↳ {line}{_R}\n")
        if len(lines) > 1:
            sys.stdout.write(f"{_GRAY}  ↳ ... ({len(lines) - 1} more lines){_R}\n")
        sys.stdout.flush()

    spinner.start()
    listener.start()
    try:
        gen = react_loop(
            user_input,
            history,
            session=session,
            on_history_update=lambda h: save_history(session, h),
            cancel_event=cancel_event,
        )
        new_history = None
        while True:
            try:
                chunk = next(gen)
                if cancel_event.is_set():
                    break
                if chunk.startswith(TOOL_PREFIX):
                    spinner.print_line(chunk[len(TOOL_PREFIX):].rstrip("\n"))
                elif chunk.startswith(THINK_PREFIX):
                    text = chunk[len(THINK_PREFIX):].strip().splitlines()[0]
                    spinner.freeze_with_text(text)
                elif chunk.startswith(RESULT_PREFIX):
                    spinner.stop()
                    body = chunk[len(RESULT_PREFIX):]
                    tool_name, _, result = body.partition("\x01")
                    _render_result(tool_name, result)
                    spinner.restart(print_usage=True)
                elif chunk.startswith(FINAL_PREFIX):
                    spinner.stop()
                    final_buf.append(chunk[len(FINAL_PREFIX):])
                elif chunk.startswith(USAGE_PREFIX):
                    spinner.set_tokens(chunk[len(USAGE_PREFIX):])
                else:
                    spinner.stop()
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            except StopIteration as e:
                if not cancel_event.is_set():
                    new_history = e.value
                break
        spinner.stop()
        if cancel_event.is_set():
            sys.stdout.write(f"\r{' ' * 48}\r^C  Cancelled.\n")
            sys.stdout.flush()
            save_history(session, history_snapshot)
            print()
            print()
            return history_snapshot
        if final_buf:
            print()
            _console.print(Markdown("".join(final_buf)))
        if new_history is not None:
            history.clear()
            history.extend(new_history)
    except Exception as e:
        spinner.stop()
        print(f"\n[error] {e}")
    finally:
        listener.stop()
    print()
    print()
    return history


def _chat_loop(session, history: list[dict]) -> None:
    """主对话循环，history 从磁盘加载，每轮结束后持久化。"""

    last_response: list[str] = []  # 上一轮 LLM 回复的原始 MD 文本

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.split()[0].lower()
            if cmd in ("/exit", "/quit"):
                break
            elif cmd == "/clear":
                history = []
                continue
            elif cmd == "/copy":
                if last_response:
                    _copy_to_clipboard("".join(last_response))
                    print(f"  {_GRAY}Copied to clipboard.{_R}")
                else:
                    print(f"  {_GRAY}Nothing to copy yet.{_R}")
                continue
            elif cmd == "/help":
                for c, desc in _COMMANDS.items():
                    print(f"  {c:<10} {desc}")
                continue
            elif cmd == "/plan":
                plan_query = user_input[len("/plan"):].strip()
                if not plan_query:
                    print("Usage: /plan <query>")
                    continue
                _run_plan(session, history, plan_query)
                continue
            else:
                print(f"Unknown command: {cmd}")
                continue

        history_snapshot = list(history)
        cancel_event = threading.Event()
        listener = _KeyboardListener(cancel_event)
        spinner = _Spinner()

        def _render_result(tool_name: str, result: str) -> None:
            lines = result.strip().splitlines()
            preview = lines[:1]
            for line in preview:
                sys.stdout.write(f"{_GRAY}  ↳ {line}{_R}\n")
            if len(lines) > 1:
                sys.stdout.write(f"{_GRAY}  ↳ ... ({len(lines) - 1} more lines){_R}\n")
            sys.stdout.flush()

        spinner.start()
        listener.start()
        try:
            gen = react_loop(
                user_input,
                history,
                session=session,
                on_history_update=lambda h: save_history(session, h),
                cancel_event=cancel_event,
            )
            new_history = None
            final_buf: list[str] = []
            while True:
                try:
                    chunk = next(gen)
                    if cancel_event.is_set():
                        break
                    if chunk.startswith(TOOL_PREFIX):
                        spinner.print_line(chunk[len(TOOL_PREFIX):].rstrip("\n"))
                    elif chunk.startswith(THINK_PREFIX):
                        # 思考文字：原地替换 spinner 行，格式 "⠹ 好的，我来..."
                        text = chunk[len(THINK_PREFIX):].strip().splitlines()[0]  # 取首行，行内显示
                        spinner.freeze_with_text(text)
                        # 不重启，下一个 TOOL_PREFIX 会接着打印在下一行
                    elif chunk.startswith(RESULT_PREFIX):
                        # 工具结果：停 spinner，折叠显示，重启等待下一步
                        spinner.stop()
                        body = chunk[len(RESULT_PREFIX):]
                        tool_name, _, result = body.partition("\x01")
                        _render_result(tool_name, result)
                        spinner.restart(print_usage=True)
                    elif chunk.startswith(FINAL_PREFIX):
                        # 最终回复：rich 渲染 Markdown
                        spinner.stop()
                        final_buf.append(chunk[len(FINAL_PREFIX):])
                    elif chunk.startswith(USAGE_PREFIX):
                        spinner.set_tokens(chunk[len(USAGE_PREFIX):])
                    else:
                        # 兜底：直接输出
                        spinner.stop()
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                except StopIteration as e:
                    if not cancel_event.is_set():
                        new_history = e.value
                    break
            spinner.stop()
            if cancel_event.is_set():
                sys.stdout.write(f"\r{' ' * 48}\r^C  Cancelled.\n")
                sys.stdout.flush()
                history = history_snapshot
                save_history(session, history_snapshot)
            else:
                if final_buf:
                    print()
                    _console.print(Markdown("".join(final_buf)))
                    last_response.clear()
                    last_response.extend(final_buf)
                if new_history is not None:
                    history = new_history
        except Exception as e:
            spinner.stop()
            print(f"\n[error] {e}")
        finally:
            listener.stop()
        print()
        print()


def _install_playwright() -> None:
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        import subprocess
        driver_executable = compute_driver_executable()
        cmd = [str(x) for x in driver_executable] + ["install", "chromium"]
        result = subprocess.run(cmd, env=get_driver_env())
        sys.exit(result.returncode)
    except Exception as e:
        print(f"playwright install failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """入口：解析命令行参数，支持 `chartgen resume [id]`。"""
    args = sys.argv[1:]

    if args and args[0] == "_playwright_install":
        _install_playwright()
        return

    load_config()
    error = check_llm_configured()
    if error:
        print(error)
        sys.exit(1)

    if args and args[0] == "resume":
        _cmd_resume(args[1:])
        return

    # 默认：每次启动创建新 session
    session = create_session()
    _run_session(session, resumed=False)


if __name__ == "__main__":
    main()

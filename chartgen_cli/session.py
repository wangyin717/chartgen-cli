"""Session 管理：每次启动创建新 session，支持按短 ID 恢复历史 session。

目录结构（相对当前工作目录，"项目级"数据 —— 类似 .venv/.git，跟着用户在哪个目录下运行 chartgen 走）：
  ./chartgen_workspace/sessions.json                   全局索引
  ./chartgen_workspace/sessions/<id>/workspace/        pkl 等中间产物
  ./chartgen_workspace/sessions/<id>/outputs/          生成的 HTML 图表
  ./chartgen_workspace/sessions/<id>/context/          对话上下文
    chat_history.json                                  完整多轮消息历史

注：API key 等用户级配置不在这里，走 ~/.chartgen/config（见 config.py）。
"""
import json
import os
import secrets
import datetime
from dataclasses import dataclass, asdict
from typing import Optional

_WORKSPACE_ROOT_NAME = "chartgen_workspace"


def _sessions_root() -> str:
    """CWD 下的 sessions 根目录。不是模块常量 —— 每次调用取当前 os.getcwd()，
    避免进程内 os.chdir() 或测试场景下常量在 import 时被过早固化。"""
    return os.path.join(os.getcwd(), _WORKSPACE_ROOT_NAME, "sessions")


def _index_file() -> str:
    return os.path.join(_sessions_root(), "sessions.json")


@dataclass
class Session:
    session_id: str
    workspace_dir: str
    outputs_dir: str
    context_dir: str


def _new_short_id() -> str:
    """生成 8 位 hex 短 ID，碰撞概率极低（2^32 空间）。"""
    return secrets.token_hex(4)


def _load_index() -> list[dict]:
    """读取全局索引，文件不存在时返回空列表。"""
    index_file = _index_file()
    if not os.path.exists(index_file):
        return []
    try:
        with open(index_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(entries: list[dict]) -> None:
    index_file = _index_file()
    os.makedirs(os.path.dirname(index_file), exist_ok=True)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _init_session_dirs(session_id: str) -> tuple[str, str, str]:
    """创建 workspace / outputs / context 目录，返回三个路径。"""
    session_root = os.path.join(_sessions_root(), session_id)
    workspace_dir = os.path.join(session_root, "workspace")
    outputs_dir = os.path.join(session_root, "outputs")
    context_dir = os.path.join(session_root, "context")
    os.makedirs(workspace_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)
    os.makedirs(context_dir, exist_ok=True)
    return workspace_dir, outputs_dir, context_dir


def create_session() -> Session:
    """每次启动时创建新 session，写入全局索引。"""
    # 生成不重复的短 ID
    entries = _load_index()
    existing_ids = {e["id"] for e in entries}
    session_id = _new_short_id()
    while session_id in existing_ids:
        session_id = _new_short_id()

    workspace_dir, outputs_dir, context_dir = _init_session_dirs(session_id)

    entry = {
        "id": session_id,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "last_active_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "cwd": os.getcwd(),
    }
    entries.append(entry)
    _save_index(entries)

    return Session(session_id=session_id, workspace_dir=workspace_dir, outputs_dir=outputs_dir, context_dir=context_dir)


def resume_session(session_id: str) -> Optional[Session]:
    """按 ID 恢复历史 session，ID 不存在时返回 None。"""
    entries = _load_index()
    if not any(e["id"] == session_id for e in entries):
        return None
    workspace_dir, outputs_dir, context_dir = _init_session_dirs(session_id)
    return Session(session_id=session_id, workspace_dir=workspace_dir, outputs_dir=outputs_dir, context_dir=context_dir)


def update_last_active(session_id: str) -> None:
    """退出时更新 last_active_at，供列表排序用。"""
    entries = _load_index()
    for entry in entries:
        if entry["id"] == session_id:
            entry["last_active_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            break
    _save_index(entries)


def list_sessions(limit: int = 10) -> list[dict]:
    """返回最近 N 条 session，按 last_active_at 倒序。"""
    entries = _load_index()
    entries.sort(key=lambda e: e.get("last_active_at", ""), reverse=True)
    return entries[:limit]


def list_workspace_files(session: Session) -> list[str]:
    """返回 workspace 目录下的文件名列表（不含路径）。"""
    try:
        return sorted(os.listdir(session.workspace_dir))
    except OSError:
        return []


def load_history(session: Session) -> list[dict]:
    """从 context/chat_history.json 读取完整多轮消息历史。"""
    path = os.path.join(session.context_dir, "chat_history.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_history(session: Session, history: list[dict]) -> None:
    """将完整多轮消息历史写入 context/chat_history.json。"""
    path = os.path.join(session.context_dir, "chat_history.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

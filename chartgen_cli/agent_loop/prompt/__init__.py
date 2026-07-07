from datetime import date
from pathlib import Path
import yaml

from chartgen_cli.session import Session

_DIR = Path(__file__).parent


def get_system_prompt(session: Session) -> str:
    template = yaml.safe_load((_DIR / "system_prompt.yaml").read_text(encoding="utf-8"))["system"]
    return template.format(
        today=date.today().isoformat(),
        workspace_dir=session.workspace_dir,
        outputs_dir=session.outputs_dir,
    )


def get_plan_user_prefix() -> str:
    return yaml.safe_load((_DIR / "plan_prompt.yaml").read_text(encoding="utf-8"))["prefix"]


# 模块加载时读一次，避免每次调用都读文件
PLAN_USER_PREFIX = get_plan_user_prefix()

__all__ = ["get_system_prompt", "PLAN_USER_PREFIX"]

"""独立分发版的配置加载。

设计（见 grill-me 交互确认的方案）：
- 配置是用户级的：~/.chartgen/config（.env 格式），一次配置、所有项目目录通用。
- 数据是项目级的：./chartgen_workspace/（见 session.py），随 CWD 走。
- 仅支持 BYOK 模式：用户自带 LLM_PROVIDER/LLM_API_KEY/LLM_MODEL，不再有服务端网关模式。
"""
import os

from dotenv import load_dotenv

CONFIG_PATH = os.path.expanduser("~/.chartgen/config")

_CONFIG_TEMPLATE = """\
# chartgen 配置文件
#
# ── 模式一：ChartGen 托管服务（推荐，无需自备 LLM key）────────────────────────
# 前往 https://chartgen.ai 注册并获取 Access Token，填入下方后取消注释：
# SERVER_URL=https://api.chartgen.ai
# ACCESS_TOKEN=your-access-token-here

# ── 模式二：自带 API Key（BYOK）────────────────────────────────────────────────
# 设置 LLM_API_KEY 后自动切换为 BYOK 模式，优先级高于模式一。
#
# DeepSeek
# LLM_PROVIDER=deepseek
# LLM_API_KEY=sk-xxx
# LLM_MODEL=deepseek-chat

# 智谱 GLM
# LLM_PROVIDER=glm
# LLM_API_KEY=xxx
# LLM_MODEL=glm-4.6

# OpenAI
# LLM_PROVIDER=openai
# LLM_API_KEY=sk-xxx
# LLM_MODEL=gpt-4o

# Claude (Anthropic)
# LLM_PROVIDER=claude
# LLM_API_KEY=sk-ant-xxx
# LLM_MODEL=claude-sonnet-4-6

# ── 可选：第三方数据源（不填则对应功能自动降级/不可用）──────────────────────────
# GOOGLE_TRENDS_API=
# GUGUDATA_API=
# TUSHARE_API=
# PERPLEXITY_API_KEY=
"""


def ensure_config_file() -> None:
    """首次运行时若配置文件不存在，写入模板（不覆盖已有文件）。"""
    if os.path.exists(CONFIG_PATH):
        return
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(_CONFIG_TEMPLATE)


def load_config() -> None:
    """加载 ~/.chartgen/config 到 os.environ（不覆盖已存在的同名环境变量）。"""
    ensure_config_file()
    load_dotenv(CONFIG_PATH, override=False)


def check_llm_configured() -> str | None:
    """检查是否有可用的 LLM 配置，缺失时返回引导文案；可用则返回 None。

    优先级：BYOK（LLM_API_KEY 存在）> 服务器模式（SERVER_URL + ACCESS_TOKEN）。
    """
    if os.environ.get("LLM_API_KEY"):
        missing = [k for k in ("LLM_PROVIDER", "LLM_MODEL") if not os.environ.get(k)]
        if not missing:
            return None
        return (
            f"[error] 设置了 LLM_API_KEY 但缺少：{', '.join(missing)}。\n"
            f"请编辑配置文件：{CONFIG_PATH}"
        )
    if os.environ.get("SERVER_URL") and os.environ.get("ACCESS_TOKEN"):
        return None
    return (
        f"[error] 未检测到可用的 LLM 配置。\n"
        f"请编辑配置文件并选择以下任一方式：\n"
        f"  • 模式一（托管服务）：填写 SERVER_URL + ACCESS_TOKEN\n"
        f"  • 模式二（BYOK）：填写 LLM_PROVIDER + LLM_API_KEY + LLM_MODEL\n"
        f"  {CONFIG_PATH}"
    )

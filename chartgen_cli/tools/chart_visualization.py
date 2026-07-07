"""chart_visualization 工具：从 session workspace 读取 DataFrame，生成 HTML 图表。

核心链路（精简版，去掉所有服务端依赖）：
  DataFrame → prompt → LLM(CHART_CODE 模型) → python code → exec → html_code → 写文件 → 打印链接
"""
import asyncio
import logging
import os
import pickle
import re
import time
import pandas as pd

logger = logging.getLogger(__name__)

def _inject_fullscreen_style(html_code: str) -> str:
    if not html_code:
        return html_code
    style_tag = '    <style>\n        html, body { margin: 0; padding: 0; width: 100%; height: 100%; }\n    </style>\n'
    if '</head>' in html_code:
        return html_code.replace('</head>', f'{style_tag}</head>', 1)
    return html_code


# pyecharts render_embed() 默认 CDN（见 pyecharts/globals.py 的 _OnlineHost.DEFAULT_HOST）
_ECHARTS_CDN = "https://assets.pyecharts.org/assets/v6/echarts.min.js"
_ECHARTS_SRC_RE = re.compile(r'<script[^>]*src=["\'][^"\']*echarts[^"\']*["\']', re.IGNORECASE)


def _fix_html_structure(html_code: str) -> str:
    """确定性修复：补 DOCTYPE、补 echarts CDN <script> 引用。不依赖 LLM，不占重试预算。"""
    if not html_code:
        return html_code
    if not html_code.strip().lower().startswith("<!doctype"):
        html_code = "<!DOCTYPE html>\n" + html_code
    if not _ECHARTS_SRC_RE.search(html_code):
        script_tag = f'<script type="text/javascript" src="{_ECHARTS_CDN}"></script>\n'
        if "<head>" in html_code:
            html_code = html_code.replace("<head>", f"<head>\n{script_tag}", 1)
        else:
            html_code = script_tag + html_code
    return html_code

_workspace_dir: str = ""
_outputs_dir: str = ""


def set_dirs(workspace: str, outputs: str) -> None:
    global _workspace_dir, _outputs_dir
    _workspace_dir = workspace
    _outputs_dir = outputs


def thinking(args: dict) -> str:
    return f"chart: {args.get('data', '')} — {args.get('user_chat', '')[:60]}"


# ── LLM 调用 ──────────────────────────────────────────────────────────────────

async def _call_llm(prompt: str) -> str:
    """调用图表专用模型，返回原始文本响应。"""
    from chartgen_cli.agent_loop import llm_client
    resp = await llm_client.call("CHART_CODE", [{"role": "user", "content": prompt}])
    return resp.text


# ── Prompt 构建 ───────────────────────────────────────────────────────────────

def _build_prompt(df: pd.DataFrame, user_chat: str, prompt_template: str) -> str:
    """填充 chart_prompt.md 模板。"""
    # 计算各文本列的唯一值（供模型理解数据分布）
    unique_lines = []
    for col in df.select_dtypes(include="object").columns:
        unique_lines.append(f"- {col}: {df[col].dropna().unique().tolist()}")
    data_unique_values = "\n".join(unique_lines) if unique_lines else "None"

    return prompt_template.format(
        query=user_chat,
        data=df.head(6).to_csv(index=False),
        data_unique_values=data_unique_values,
        data_info=str(df.dtypes),
        colorScheme="#3C6EFF,#FF7A45,#FFC53D,#2FB8AC,#9254DE,#F759AB,#73D13D",
    )


def _load_prompt_template() -> str:
    """读取 chart_prompt.md（与本文件同级的 CLI 包目录）。"""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt", "chart_prompt.md")
    with open(os.path.normpath(prompt_path), "r", encoding="utf-8") as f:
        return f.read()


_EMPTY_OUTPUT_ERROR = (
    "Code executed successfully but produced no chart output (no html_code / html_code_N "
    "variable detected). Check whether you forgot to assign the pyecharts chart's "
    "render_embed() result to the html_code variable."
)


def _build_repair_prompt(user_chat: str, failed_code: str, error_message: str) -> str:
    """构建精简的修复 prompt：不复用 chart_prompt.md，只给失败代码 + 错误信息。"""
    return f"""Your previously generated pyecharts chart code failed to execute. Please fix it.

[Original user request]
{user_chat}

[Failed code]
```python
{failed_code}
```

[Execution error]
{error_message}

Only fix the issue that caused this error; try to preserve the original chart design and type as much as possible.
If it truly cannot be fixed under the current chart type, you may simplify to a simpler chart type.
Output only the complete fixed Python code, wrapped in a ```python code block. Do not include any explanatory text.
"""


# ── 代码执行 ──────────────────────────────────────────────────────────────────

def _extract_python(text: str) -> str:
    """从 LLM 响应中提取 ```python ... ``` 代码块。"""
    match = re.search(r"```python(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 没有代码块标记时直接返回（兜底）
    return text.strip()


def _exec_chart_code(code: str, df: pd.DataFrame) -> list[str]:
    """
    在本地执行 LLM 生成的图表代码，返回 html_code 列表。

    约定：LLM 生成的代码用 df = pd.read_csv("data.csv") 加载数据，
    这里把 df 直接注入命名空间替代文件读取。
    """
    # 把 read_csv 调用替换为使用注入的 df（LLM 生成的代码规范要求用 data.csv）
    code = re.sub(
        r"^.*df\s*=\s*pd\.read_csv\(.*?\)\s*$",
        "df = _injected_df.copy()",
        code,
        flags=re.MULTILINE,
    )
    # 修复 LLM 常见 camelCase 参数名错误
    _fixes = [
        ("axisTick_opts",   "axistick_opts"),
        ("axisLine_opts",   "axisline_opts"),
        ("axisLabel_opts",  "axislabel_opts"),
        ("splitLine_opts",  "splitline_opts"),
        ("splitArea_opts",  "splitarea_opts"),
        ("itemStyle_opts",  "itemstyle_opts"),
        ("lineStyle_opts",  "linestyle_opts"),
        ("areaStyle_opts",  "areastyle_opts"),
        ("labelOpts",       "label_opts"),
        ("tooltipOpts",     "tooltip_opts"),
    ]
    for wrong, right in _fixes:
        code = code.replace(wrong, right)
    # 预置常用 import，避免 LLM 遗漏时报 NameError
    preamble = "import pandas as pd\nimport numpy as np\n"
    namespace = {"_injected_df": df, "pd": pd, "np": __import__("numpy")}
    exec(preamble + code, namespace)  # noqa: S102

    # 收集 html_code / html_code_1 / html_code_2 ... 等所有图表输出
    html_codes = []
    if "html_code" in namespace and namespace["html_code"]:
        html_codes.append(namespace["html_code"])
    i = 1
    while f"html_code_{i}" in namespace:
        val = namespace[f"html_code_{i}"]
        if val:
            html_codes.append(val)
        i += 1
    return html_codes


# ── 工具入口 ──────────────────────────────────────────────────────────────────

def _osc8_link(path: str, label: str) -> str:
    """OSC 8 可点击链接，不支持的终端显示为普通文本。"""
    return f"\033]8;;file://{path}\033\\{label}\033]8;;\033\\"


_MAX_ATTEMPTS = 3  # 1 次首次生成 + 2 次修复重试


class _ChartGenError(Exception):
    """携带本轮已生成的代码（若有），供调用方落盘/下一轮 repair 使用。"""

    def __init__(self, message: str, code: str | None):
        super().__init__(message)
        self.code = code


def _generate_and_exec(
    df: pd.DataFrame,
    user_chat: str,
    template: str,
    prev_code: str | None,
    prev_error: str | None,
) -> list[str]:
    """
    单次尝试：构建 prompt（首次 or 修复）→ 调 LLM → 提取代码 → exec → 检查非空。
    失败一律 raise _ChartGenError（附带本轮代码，若已生成），由调用方捕获并决定是否重试。

    是否是"修复"由 prev_error 是否存在决定，而非 prev_code ——
    若上一轮是"LLM 调用本身失败"（没生成出代码），prev_code 仍可能是 None，
    但此时仍应走修复分支（带上错误信息），不能退回首次生成 prompt。
    """
    if prev_error is None:
        prompt = _build_prompt(df, user_chat, template)
    else:
        prompt = _build_repair_prompt(user_chat, prev_code or "", prev_error)

    try:
        llm_response = asyncio.run(_call_llm(prompt))
    except Exception as e:
        # LLM 调用本身失败（网络/超时等），本轮没有生成任何代码
        raise _ChartGenError(str(e), code=None) from e

    python_code = _extract_python(llm_response)

    try:
        html_codes = _exec_chart_code(python_code, df)
    except Exception as e:
        raise _ChartGenError(str(e), code=python_code) from e

    if not html_codes:
        raise _ChartGenError(_EMPTY_OUTPUT_ERROR, code=python_code)
    return html_codes


def run(data: str, user_chat: str) -> str:
    if not _workspace_dir:
        return "[error] session 未初始化"

    logger.info("[Chart Visualization] start: data=%s user_chat=%r", data, user_chat[:60])

    # 1. 加载 DataFrame
    pkl_path = os.path.join(_workspace_dir, data + ".pkl")
    if not os.path.exists(pkl_path):
        available = ", ".join(sorted(os.listdir(_workspace_dir))) or "（空）"
        logger.warning("[Chart Visualization] pkl not found: %s", pkl_path)
        return f"[error] 找不到 '{data}.pkl'，当前 workspace：{available}"
    try:
        df = pickle.load(open(pkl_path, "rb"))
    except Exception as e:
        logger.warning("[Chart Visualization] load pkl failed: %s", e)
        return f"[error] 加载 {data}.pkl 失败：{e}"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return f"[error] '{data}' 不是有效的 DataFrame 或数据为空"
    logger.info("[Chart Visualization] loaded df: shape=%s", df.shape)

    try:
        template = _load_prompt_template()
    except Exception as e:
        logger.warning("[Chart Visualization] load prompt template failed: %s", e)
        return f"[error] 加载 prompt 模板失败：{e}"

    # 2. 生成 + 执行，失败则用（最近一次代码 + 错误）重试，最多 _MAX_ATTEMPTS 次
    prev_code: str | None = None
    prev_error: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        logger.info("[Chart Visualization] attempt %d/%d", attempt, _MAX_ATTEMPTS)
        try:
            html_codes = _generate_and_exec(df, user_chat, template, prev_code, prev_error)
            break
        except _ChartGenError as e:
            prev_error = str(e)
            # 若本轮压根没生成出代码（如 LLM 调用失败），保留上一轮代码用于落盘/下一轮 repair
            prev_code = e.code if e.code is not None else prev_code
            logger.warning(
                "[Chart Visualization] attempt %d/%d failed: %s", attempt, _MAX_ATTEMPTS, e
            )
    else:
        code_path = os.path.join(_workspace_dir, f"chart_error_{int(time.time())}.py")
        with open(code_path, "w", encoding="utf-8") as _f:
            _f.write(prev_code or "")
        return (
            f"[error] 图表代码生成失败（已重试 {_MAX_ATTEMPTS} 次）：{prev_error}\n"
            f"最后一次失败代码已保存至：{code_path}"
        )

    # 3. 写 HTML 文件，返回可点击链接
    # render_embed() 生成的 html/body 无高度，复用服务端 inject_fullscreen_style 修复
    ts = int(time.time())
    links = []
    for i, html in enumerate(html_codes):
        html = _inject_fullscreen_style(html)
        html = _fix_html_structure(html)
        suffix = f"_{i + 1}" if len(html_codes) > 1 else ""
        fname = f"{data}_{ts}{suffix}.html"
        out_path = os.path.join(_outputs_dir, fname)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        links.append(_osc8_link(out_path, fname))
        logger.info("[Chart Visualization] saved: %s", out_path)

    link_str = "\n".join(f"  {lnk}" for lnk in links)
    return f"图表已生成（{len(links)} 个）：\n{link_str}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "chart_visualization",
        "description": (
            "根据 workspace 中已有的 DataFrame 生成交互式 HTML 图表，返回可点击的本地文件链接。"
            "data 参数为变量名，对应 workspace 中的 .pkl 文件（不含扩展名）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "string",
                    "description": "DataFrame 变量名，与 ci 工具执行后自动保存的 .pkl 文件名一致",
                },
                "user_chat": {
                    "type": "string",
                    "description": "图表需求描述，例如「按月份画销售额折线图」",
                },
            },
            "required": ["data", "user_chat"],
        },
    },
}

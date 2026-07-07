"""generate_ppt 工具：根据 markdown 内容生成本地 .pptx 文件。

核心链路（CLI 精简版，无服务端依赖，无 print 噪音）：
  query + report_markdown
    → outline → design_plans → slides (MarkdownToPPTConverter，无流式)
    → export_slides_to_pptx → .pptx 文件 → OSC8 可点击链接
"""
import asyncio
import logging
import os
import threading

logger = logging.getLogger(__name__)
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

_outputs_dir: str = ""

# Shared lock between _filtered_print and _Spinner._tool_spin so their stdout
# writes never interleave. main.py imports this lock and holds it on each
# spinner write; _filtered_print holds it while clearing the line + printing.
_STDOUT_LOCK = threading.Lock()

MAX_CONCURRENT = 3
PPT_SLIDE_WIDTH = 1280
PPT_SLIDE_HEIGHT = 720


def set_dirs(workspace: str, outputs: str) -> None:
    global _outputs_dir
    _outputs_dir = outputs


def thinking(args: dict) -> str:
    return f"ppt: {args.get('query', '')[:60]}"


def _osc8_link(path: str, label: str) -> str:
    return f"\033]8;;file://{path}\033\\{label}\033]8;;\033\\"


async def _generate(content: str, query: str, out_dir: str, title: str,
                    progress_log: list, language: str = "Chinese") -> str:
    """核心生成逻辑，全程无 print。"""
    from chartgen_cli.plugin.generate_ppt.generator import LLMRecorder
    from chartgen_cli.plugin.generate_ppt.gen_ppt import (
        MarkdownToPPTConverter,
        _apply_slide_fit,
        wrap_slide_html,
    )
    from playwright.async_api import async_playwright
    from pptx import Presentation
    from pptx.util import Inches

    # ── 噪音压制 ──────────────────────────────────────────────────────────────
    # 1. 命名空间 logger（LLMRecorder JSON、generate_ppt 插件进度）→ WARNING
    _noisy_loggers = [
        "chartgen_cli.plugin.generate_ppt.generator",
        "chartgen_cli.plugin.generate_ppt",
    ]
    _saved_levels = {}
    for _name in _noisy_loggers:
        _lg = logging.getLogger(_name)
        _saved_levels[_name] = _lg.level
        _lg.setLevel(logging.WARNING)

    # 2. root logger 的 [PPT] warning（布局溢出等）→ 过滤掉
    class _PptFilter(logging.Filter):
        def filter(self, record):
            return "[PPT]" not in (record.getMessage())

    _root_logger = logging.getLogger()
    _ppt_filter = _PptFilter()
    _root_logger.addFilter(_ppt_filter)

    import builtins as _builtins
    import re as _re
    import sys as _sys

    _real_print = _builtins.print

    _GRAY = "\033[38;5;245m"
    _RESET = "\033[0m"

    def _filtered_print(*args, **kwargs):
        text = " ".join(str(a) for a in args).strip()
        if _re.search(r"\[1/3\]|\[2/3\]|\[3/3\]", text):
            with _STDOUT_LOCK:
                _sys.stdout.write("\r" + " " * 120 + "\r")
                _real_print(f"{_GRAY}✻ {text}{_RESET}", flush=True)
            progress_log.append(text)
        elif _re.search(r"\[\d+/\d+\] (designing|generating)", text):
            with _STDOUT_LOCK:
                _sys.stdout.write("\r" + " " * 120 + "\r")
                _real_print(f"{_GRAY}    {text}{_RESET}", flush=True)
            progress_log.append(text)

    try:
        # 3. builtins.print → 只透传进度行给 progress_cb
        with patch("builtins.print", side_effect=_filtered_print):
            session_id = str(uuid.uuid4())
            round_id = str(uuid.uuid4())
            llm_recorder = LLMRecorder(header={}, session_id=session_id, round_id=round_id, module_type="ppt_generate")

            converter = MarkdownToPPTConverter(llm_recorder, artifact_id=None, header={})
            converter.detected_language = language
            logger.info("[generate_ppt] language: %s", language)

            # 阶段 1：大纲
            await converter.generate_outline(content, user_query=query)
            outline = converter.outline
            logger.info("[generate_ppt] outline done: %d slides", len(outline["slides"]))

            # 阶段 2：全局色彩 + 设计方案
            global_style_spec = await converter.ppt_generator.generate_global_style_spec(outline)
            design_plans = await converter.generate_design_plans(global_style_spec=global_style_spec)
            logger.info("[generate_ppt] design plans done")

            # 阶段 3：生成 HTML slides（非流式）
            slides = await converter.generate_slides(design_plans, global_style_spec=global_style_spec)
            logger.info("[generate_ppt] slides done: %d", len(slides))
    finally:
        _root_logger.removeFilter(_ppt_filter)
        for _name, _lvl in _saved_levels.items():
            logging.getLogger(_name).setLevel(_lvl)

    # 阶段 4：Playwright 截图 → pptx（同样在 redirect_stdout 外，print 已无）
    completed = [s for s in slides if s and s.get("status") != "generating"]
    if not completed:
        raise ValueError("没有已完成的幻灯片可导出")

    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(5.625)

    screenshots: List[Optional[bytes]] = [None] * len(completed)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def render(idx: int, slide: dict) -> None:
            async with sem:
                page = await browser.new_page(
                    viewport={"width": PPT_SLIDE_WIDTH, "height": PPT_SLIDE_HEIGHT},
                    device_scale_factor=3,
                )
                try:
                    html = wrap_slide_html(slide)
                    await page.set_content(html, wait_until="networkidle")
                    await _apply_slide_fit(page)
                    screenshots[idx] = await page.screenshot(type="png", full_page=False)
                    logger.info("[generate_ppt] rendered slide %d/%d", idx + 1, len(completed))
                finally:
                    await page.close()

        await asyncio.gather(*[render(i, s) for i, s in enumerate(completed)])
        await browser.close()

    for shot in screenshots:
        if shot is None:
            continue
        slide_obj = prs.slides.add_slide(prs.slide_layouts[6])
        slide_obj.shapes.add_picture(BytesIO(shot), Inches(0), Inches(0), prs.slide_width, prs.slide_height)

    safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_", ".")).strip() or "presentation"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    pptx_path = os.path.join(out_dir, f"{safe_title}.pptx")
    prs.save(pptx_path)
    logger.info("[generate_ppt] saved: %s", pptx_path)
    return pptx_path


def run(query: str, report_markdown: str = "", language: str = "Chinese") -> str:
    if not _outputs_dir:
        return "[error] session 未初始化"

    content = report_markdown.strip() or query
    _first_line = content.strip().split('\n')[0].strip() if content.strip() else ""
    title = _first_line[2:].strip() if _first_line.startswith('# ') else (query[:40] or "presentation")
    out_dir = os.path.join(_outputs_dir, f"ppt_{int(time.time())}")

    progress_log: list = []
    try:
        pptx_path = asyncio.run(_generate(content, query, out_dir, title, progress_log, language=language))
    except Exception as e:
        logger.warning("[generate_ppt] 失败: %s", e, exc_info=True)
        return f"[error] PPT 生成失败：{e}"

    fname = os.path.basename(pptx_path)
    link = _osc8_link(pptx_path, fname)
    progress_str = ("\n" + "\n".join(progress_log)) if progress_log else ""
    return f"PPT 已生成：{progress_str}\n{link}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_ppt",
        "description": (
            "在完成所需分析、数据准备或总结后，立即生成本地 .pptx 文件并返回可点击链接。"
            "仅在用户明确要求生成 PPT 时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short summary of the requested PPT topic, audience, and report scope.",
                },
                "report_markdown": {
                    "type": "string",
                    "description": "Optional prepared markdown report, slide outline, or key conclusions to use as the main PPT content. Use this for synthesized findings, not large raw datasets.",
                },
                "language": {
                    "type": "string",
                    "description": "PPT output language, e.g. 'Chinese' or 'English'. Match the user's primary language.",
                },
            },
            "required": ["query"],
        },
    },
}

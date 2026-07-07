#!/usr/bin/env python3
"""
Markdown to PPT Generator - 完整流程脚本

功能：从 Markdown 文件生成 PPT（HTML 和 PPTX 格式）

用法：
    python markdown_to_ppt.py sample_data.md --slides 8 --output output/my_presentation

参数：
    markdown_file: Markdown 文件路径
    --slides: 幻灯片数量（默认：8）
    --output: 输出目录（默认：output/presentation）
    --skip-pptx: 跳过 PPTX 导出（仅生成 HTML）
"""
import logging
import os
import asyncio
import json
import re
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from datetime import datetime

from chartgen_cli.plugin.generate_ppt.generator import PPTGenerator, LLMRecorder
from chartgen_cli.plugin.generate_ppt.font_utils import get_optimal_font_settings

# 导入 PPTX 导出相关
from playwright.async_api import async_playwright
from pptx import Presentation
from pptx.util import Inches
from io import BytesIO
from typing import Optional, Dict, List, Any, Union, Tuple

MAX_CONCURRENT_SLIDES = 3
PPT_SLIDE_WIDTH = 1280
PPT_SLIDE_HEIGHT = 720
PPT_CONTENT_SAFE_HEIGHT = 620
PPT_LAYOUT_REPAIR_ATTEMPTS = 2

_PPT_FIT_JS = r"""() => {
    const SLIDE_WIDTH = 1280;
    const SLIDE_HEIGHT = 720;
    const CONTENT_SAFE_HEIGHT = 620;

    document.documentElement.style.margin = "0";
    document.documentElement.style.padding = "0";
    document.documentElement.style.width = `${SLIDE_WIDTH}px`;
    document.documentElement.style.height = `${SLIDE_HEIGHT}px`;
    document.documentElement.style.overflow = "hidden";

    document.body.style.margin = "0";
    document.body.style.padding = "0";
    document.body.style.width = `${SLIDE_WIDTH}px`;
    document.body.style.height = `${SLIDE_HEIGHT}px`;
    document.body.style.overflow = "hidden";

    const normalizeSlide = (slide) => {
        slide.style.width = `${SLIDE_WIDTH}px`;
        slide.style.height = `${SLIDE_HEIGHT}px`;
        slide.style.maxWidth = `${SLIDE_WIDTH}px`;
        slide.style.maxHeight = `${SLIDE_HEIGHT}px`;
        if (!slide.style.position) {
            slide.style.position = "relative";
        }
        slide.style.overflow = "hidden";
        slide.style.boxSizing = "border-box";
    };

    const fitLayer = (layer, fallbackHeight) => {
        layer.style.transform = "";
        layer.style.transformOrigin = "top center";

        const availableWidth = layer.clientWidth || SLIDE_WIDTH;
        const availableHeight = layer.clientHeight || fallbackHeight || SLIDE_HEIGHT;
        const requiredWidth = layer.scrollWidth || availableWidth;
        const requiredHeight = layer.scrollHeight || availableHeight;
        const scale = Math.min(1, availableWidth / requiredWidth, availableHeight / requiredHeight);

        if (Number.isFinite(scale) && scale > 0 && scale < 1) {
            layer.style.transform = `scale(${scale})`;
        }
    };

    const slides = Array.from(document.querySelectorAll(".slide-container"));
    const fallback = document.body.firstElementChild ? [document.body.firstElementChild] : [];
    const targets = slides.length ? slides : fallback;

    targets.forEach((slide) => {
        normalizeSlide(slide);
        const layers = Array.from(slide.querySelectorAll(".content-layer"));
        if (layers.length) {
            layers.forEach((layer) => {
                if (!layer.style.maxHeight) {
                    layer.style.maxHeight = `${CONTENT_SAFE_HEIGHT}px`;
                }
                layer.style.overflow = "hidden";
                fitLayer(layer, CONTENT_SAFE_HEIGHT);
            });
            return;
        }
        fitLayer(slide, SLIDE_HEIGHT);
    });
}"""

_PPT_LAYOUT_VALIDATION_JS = r"""() => {
    const SLIDE_WIDTH = 1280;
    const SLIDE_HEIGHT = 720;
    const TOLERANCE = 2;

    const slide = document.querySelector(".slide-container") || document.body.firstElementChild;
    if (!slide) {
        return {
            ok: false,
            issues: ["missing_slide_container"],
            layers: [],
            outside_elements: []
        };
    }

    const layers = Array.from(slide.querySelectorAll(".content-layer"));
    [slide, ...layers].forEach((el) => {
        el.style.transform = "";
        el.style.zoom = "";
    });

    const describeElement = (el, slideRect) => {
        const rect = el.getBoundingClientRect();
        const text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
        return {
            tag: el.tagName.toLowerCase(),
            class_name: typeof el.className === "string" ? el.className : "",
            text: text.slice(0, 80),
            left: Math.round(rect.left - slideRect.left),
            top: Math.round(rect.top - slideRect.top),
            right: Math.round(rect.right - slideRect.left),
            bottom: Math.round(rect.bottom - slideRect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
        };
    };

    const isVisibleElement = (el) => {
        if (["SCRIPT", "STYLE", "LINK", "META"].includes(el.tagName)) {
            return false;
        }
        const style = window.getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
            return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const slideRect = slide.getBoundingClientRect();
    const issues = [];
    if (Math.abs(slideRect.width - SLIDE_WIDTH) > TOLERANCE) {
        issues.push(`slide_width_${Math.round(slideRect.width)}px`);
    }
    if (Math.abs(slideRect.height - SLIDE_HEIGHT) > TOLERANCE) {
        issues.push(`slide_height_${Math.round(slideRect.height)}px`);
    }

    const layerReports = layers.map((layer, index) => {
        const rect = layer.getBoundingClientRect();
        const overflowY = Math.max(0, layer.scrollHeight - layer.clientHeight);
        const overflowX = Math.max(0, layer.scrollWidth - layer.clientWidth);
        if (overflowY > TOLERANCE) {
            issues.push(`content_layer_${index}_overflow_y_${overflowY}px`);
        }
        if (overflowX > TOLERANCE) {
            issues.push(`content_layer_${index}_overflow_x_${overflowX}px`);
        }
        return {
            index,
            client_width: layer.clientWidth,
            client_height: layer.clientHeight,
            scroll_width: layer.scrollWidth,
            scroll_height: layer.scrollHeight,
            overflow_x: overflowX,
            overflow_y: overflowY,
            top: Math.round(rect.top - slideRect.top),
            bottom: Math.round(rect.bottom - slideRect.top)
        };
    });

    if (!layers.length) {
        issues.push("missing_content_layer");
    }

    const outsideElements = Array.from(slide.querySelectorAll("*"))
        .filter(isVisibleElement)
        .filter((el) => {
            const rect = el.getBoundingClientRect();
            return (
                rect.left < slideRect.left - TOLERANCE ||
                rect.top < slideRect.top - TOLERANCE ||
                rect.right > slideRect.right + TOLERANCE ||
                rect.bottom > slideRect.bottom + TOLERANCE
            );
        })
        .slice(0, 8)
        .map((el) => describeElement(el, slideRect));

    if (outsideElements.length) {
        issues.push(`outside_visible_elements_${outsideElements.length}`);
    }

    const clippedContainers = Array.from(slide.querySelectorAll("*"))
        .filter(isVisibleElement)
        .filter((el) => {
            if (el === slide || layers.includes(el)) {
                return false;
            }
            const style = window.getComputedStyle(el);
            const clipsX = !["visible", "clip"].includes(style.overflowX);
            const clipsY = !["visible", "clip"].includes(style.overflowY);
            return (
                (clipsY && el.scrollHeight - el.clientHeight > TOLERANCE) ||
                (clipsX && el.scrollWidth - el.clientWidth > TOLERANCE)
            );
        })
        .slice(0, 8)
        .map((el) => ({
            ...describeElement(el, slideRect),
            client_width: el.clientWidth,
            client_height: el.clientHeight,
            scroll_width: el.scrollWidth,
            scroll_height: el.scrollHeight,
            overflow_x: Math.max(0, el.scrollWidth - el.clientWidth),
            overflow_y: Math.max(0, el.scrollHeight - el.clientHeight)
        }));

    if (clippedContainers.length) {
        issues.push(`clipped_internal_containers_${clippedContainers.length}`);
    }

    return {
        ok: issues.length === 0,
        issues,
        slide: {
            width: Math.round(slideRect.width),
            height: Math.round(slideRect.height)
        },
        layers: layerReports,
        outside_elements: outsideElements,
        clipped_containers: clippedContainers
    };
}"""

_PPT_AUTO_FIT_SCRIPT = (
    '<script data-swiftagent-ppt-fit="true">\n'
    '(() => {\n'
    f'  const fitSlides = {_PPT_FIT_JS};\n'
    '  const run = () => window.requestAnimationFrame(fitSlides);\n'
    '  if (document.fonts && document.fonts.ready) {\n'
    '    document.fonts.ready.then(run).catch(run);\n'
    '  }\n'
    '  window.addEventListener("load", run);\n'
    '  run();\n'
    '})();\n'
    '</script>'
)


def _strip_html_code_fence(html_content: str) -> str:
    html_content = (html_content or "").strip()
    html_content = re.sub(r"^\s*```(?:html)?\s*", "", html_content, count=1, flags=re.IGNORECASE)
    html_content = re.sub(r"\s*```\s*$", "", html_content, count=1)
    return html_content.strip()


def _extract_slide_fragment(html_content: str) -> str:
    html_content = _strip_html_code_fence(html_content)
    html_content = re.sub(
        r"<script\b[^>]*data-swiftagent-ppt-fit=[\"']true[\"'][^>]*>.*?</script>",
        "",
        html_content,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()

    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", html_content, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        html_content = body_match.group(1).strip()
    return html_content


def _ensure_slide_container(html_content: str) -> str:
    if "slide-container" in html_content:
        return html_content
    return f"""<div class="slide-container" style="width: {PPT_SLIDE_WIDTH}px; height: {PPT_SLIDE_HEIGHT}px; position: relative; overflow: hidden; background: #FFFFFF;">
    <div class="content-layer" style="position: relative; z-index: 10; padding: 60px 80px; height: 100%; max-height: {PPT_CONTENT_SAFE_HEIGHT}px; box-sizing: border-box; overflow: hidden;">
        {html_content}
    </div>
</div>"""


def _sanitize_streaming_slide_preview(html_content: str) -> str:
    """流式预览使用未完成 HTML，先移除模型常见的 markdown fence。"""
    return _strip_html_code_fence(html_content)


async def _apply_slide_fit(page) -> None:
    await page.evaluate("() => document.fonts ? document.fonts.ready.then(() => undefined) : undefined")
    await page.evaluate(_PPT_FIT_JS)


async def _start_slide_validation_browser():
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch()
        return playwright, browser
    except Exception:
        logging.warning("[PPT] slide layout validation browser unavailable; skipping preflight", exc_info=True)
        return None, None


async def _close_slide_validation_browser(playwright, browser) -> None:
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            logging.debug("[PPT] failed to close validation browser", exc_info=True)
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception:
            logging.debug("[PPT] failed to stop validation playwright", exc_info=True)


async def _validate_slide_layout(browser, html: str) -> Dict[str, Any]:
    if browser is None:
        return {"ok": True, "skipped": True, "issues": ["validation_browser_unavailable"]}

    page = await browser.new_page(
        viewport={'width': PPT_SLIDE_WIDTH, 'height': PPT_SLIDE_HEIGHT},
        device_scale_factor=1,
    )
    try:
        await page.set_content(html, wait_until='networkidle')
        await page.evaluate("() => document.fonts ? document.fonts.ready.then(() => undefined) : undefined")
        report = await page.evaluate(_PPT_LAYOUT_VALIDATION_JS)
        if isinstance(report, dict):
            return report
        return {"ok": False, "issues": ["invalid_validation_report"], "raw_report": report}
    except Exception as exc:
        logging.warning("[PPT] slide layout validation failed; continuing without repair", exc_info=True)
        return {"ok": True, "skipped": True, "issues": ["validation_exception"], "error": str(exc)}
    finally:
        await page.close()


def _format_slide_hint(slide_count: Union[int, Tuple[int, int], None], max_slides: Optional[int]) -> str:
    """根据 slides/max_slides 参数生成可读的页数提示文案"""
    if max_slides is not None:
        return f"不超过 {max_slides} 页"
    if slide_count is None:
        return "LLM 智能决定"
    if isinstance(slide_count, tuple):
        return f"目标 {slide_count[0]}-{slide_count[1]} 页"
    return f"目标 {slide_count} 页"


class MarkdownToPPTConverter:
    """Markdown 转 PPT 转换器"""

    def __init__(self, llm_recorder=None, artifact_id=None, header=None):
        self.ppt_generator = PPTGenerator(llm_recorder)
        self.outline = None
        self.slides = []
        self.llm_recorder = llm_recorder
        self.detected_language = ""
        self.artifact_info = None

    async def load_markdown(self, markdown_path: str) -> str:
        """加载 Markdown 文件"""
        markdown_file = Path(markdown_path)
        if not markdown_file.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {markdown_path}")

        print(f"📄 读取 Markdown 文件: {markdown_file.name}")
        with open(markdown_file, 'r', encoding='utf-8') as f:
            content = f.read()

        print(f"   文件大小: {len(content)} 字符")
        return content

    async def generate_outline(self, markdown_content: str, slide_count: Union[int, Tuple[int, int], None] = 8, user_query: str = "",
                               max_slides: Optional[int] = None) -> dict:
        """生成大纲"""
        print(f"\n📋 [1/3] Generating outline ({_format_slide_hint(slide_count, max_slides)})...")

        outline = await self.ppt_generator.generate_outline_from_markdown(
            markdown_content=markdown_content,
            slide_count=slide_count,
            max_slides=max_slides,
            user_query=user_query,
            llm_recorder=self.llm_recorder,
            detected_language=self.detected_language
        )

        self.outline = outline

        print(f"✅ 大纲生成完成")
        print(f"   标题: {outline['title']}")
        print(f"   幻灯片数: {len(outline['slides'])} 页")
        print(f"   主题: {outline['metadata'].get('theme', 'N/A')}")

        return outline

    async def generate_design_plans(self, global_style_spec: Optional[Dict[str, Any]] = None) -> list:
        """为所有幻灯片生成设计方案（并行）"""
        if not self.outline:
            raise ValueError("请先生成大纲")

        print(f"\n🎨 [2/3] Generating design plans (concurrency: {MAX_CONCURRENT_SLIDES})...")

        total = len(self.outline['slides'])
        design_plans = [None] * total  # 预分配列表保持顺序
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SLIDES)

        async def generate_single_design(idx: int):
            async with semaphore:
                slide_info = self.outline['slides'][idx]
                print(f"     [{idx + 1}/{total}] designing slide {slide_info['id']}: {slide_info['title']}")

                design_plan = await self.ppt_generator.generate_design_plan(
                    outline=self.outline,
                    slide_index=idx,
                    global_style_spec=global_style_spec,
                    detected_language=self.detected_language,
                )

                design_plans[idx] = design_plan
                print(f"   [{idx + 1}/{total}] ✓ 完成设计幻灯片 {slide_info['id']}")
                return design_plan

        # 并行生成所有设计方案
        await asyncio.gather(*[generate_single_design(idx) for idx in range(total)])

        print(f"✅ 设计方案生成完成")
        return design_plans

    async def _build_validated_slide_html(
            self,
            *,
            browser,
            raw_html: str,
            slide_info: Dict[str, Any],
            design_plan: Dict[str, Any],
            global_style_spec: Optional[Dict[str, Any]] = None,
    ) -> str:
        """包装并校验单页 HTML；真实浏览器溢出时让 LLM 重写，最后保留包装层兜底。"""
        candidate_html = raw_html
        last_report: Dict[str, Any] = {}

        for attempt in range(PPT_LAYOUT_REPAIR_ATTEMPTS + 1):
            wrapped_html = wrap_slide_html({'title': slide_info.get('title', 'Slide'), 'html': candidate_html})
            report = await _validate_slide_layout(browser, wrapped_html)
            if report.get("ok"):
                if attempt > 0:
                    print(f"   ✓ 幻灯片 {slide_info.get('id')} 布局修复通过（第 {attempt} 次重写后）")
                return wrapped_html

            last_report = report
            if attempt >= PPT_LAYOUT_REPAIR_ATTEMPTS:
                break

            print(f"   ⚠️ 幻灯片 {slide_info.get('id')} 检测到内容溢出，正在第 {attempt + 1} 次重写...")
            try:
                repaired_html = await self.ppt_generator.repair_slide_overflow(
                    slide_info=slide_info,
                    design_plan=design_plan,
                    html=_extract_slide_fragment(candidate_html),
                    validation_report=report,
                    global_style_spec=global_style_spec,
                    detected_language=self.detected_language,
                )
            except Exception:
                logging.warning(
                    "[PPT] slide overflow repair failed slide_id=%s",
                    slide_info.get('id'),
                    exc_info=True,
                )
                break
            if not repaired_html.strip():
                logging.warning("[PPT] slide overflow repair returned empty HTML slide_id=%s", slide_info.get('id'))
                break
            candidate_html = repaired_html

        logging.warning(
            "[PPT] slide layout still overflowed after repair attempts slide_id=%s report=%s",
            slide_info.get('id'),
            json.dumps(last_report, ensure_ascii=False)[:2000],
        )
        return wrap_slide_html({'title': slide_info.get('title', 'Slide'), 'html': candidate_html})

    async def generate_slides(self, design_plans: list, global_style_spec: Optional[Dict[str, Any]] = None) -> list:
        """生成所有幻灯片的 HTML（并行）"""
        if not self.outline:
            raise ValueError("请先生成大纲")

        print(f"\n🖼️ [3/3] Generating HTML slides (concurrency: {MAX_CONCURRENT_SLIDES})...")

        source_content = self.outline.get('metadata', {}).get('source_content', '')
        total_slides = len(self.outline['slides'])
        slides = [None] * total_slides  # 预分配列表保持顺序
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SLIDES)
        validation_playwright, validation_browser = await _start_slide_validation_browser()

        async def generate_single_slide(idx: int, slide_info: dict, design_plan: dict):
            async with semaphore:
                print(f"   [{idx + 1}/{total_slides}] generating slide {slide_info['id']}: {slide_info['title']}")

                html = await self.ppt_generator.generate_final_slide(
                        slide_info=slide_info,
                        design_plan=design_plan.get('thinking_process', {}),
                        total_slides=total_slides,
                        source_content=source_content,
                        global_style_spec=global_style_spec,
                        detected_language=self.detected_language,
                )
                html = await self._build_validated_slide_html(
                    browser=validation_browser,
                    raw_html=html,
                    slide_info=slide_info,
                    design_plan=design_plan,
                    global_style_spec=global_style_spec,
                )

                slides[idx] = {
                    'id': slide_info['id'],
                    'title': slide_info['title'],
                    'type': slide_info['type'],
                    'html': html,
                    'status': 'completed'
                }

                print(f"   [{idx + 1}/{total_slides}] ✓ 完成生成幻灯片 {slide_info['id']}")
                return slides[idx]

        # 并行生成所有幻灯片
        try:
            await asyncio.gather(*[
                generate_single_slide(idx, slide_info, design_plan)
                for idx, (slide_info, design_plan) in enumerate(zip(self.outline['slides'], design_plans))
            ])
        finally:
            await _close_slide_validation_browser(validation_playwright, validation_browser)

        self.slides = slides
        print(f"✅ HTML 幻灯片生成完成")
        return slides

    def save_html(self, output_dir: str):
        """保存 HTML 文件"""
        if not self.slides:
            raise ValueError("没有幻灯片可保存")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 生成完整的 HTML 文档
        html_content = self._generate_html_document()

        # 保存主文件
        html_file = output_path / "presentation.html"
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"\n💾 HTML 文件已保存: {html_file}")

        # 保存单独的幻灯片
        slides_dir = output_path / "slides"
        slides_dir.mkdir(exist_ok=True)

        for slide in self.slides:
            slide_file = slides_dir / f"slide_{slide['id']:02d}.html"
            with open(slide_file, 'w', encoding='utf-8') as f:
                f.write(self._wrap_single_slide(slide))

        print(f"   单独幻灯片已保存到: {slides_dir}")

        # 保存大纲 JSON
        outline_file = output_path / "outline.json"
        with open(outline_file, 'w', encoding='utf-8') as f:
            json.dump(self.outline, f, ensure_ascii=False, indent=2)

        print(f"   大纲 JSON 已保存: {outline_file}")

        return html_file

    async def export_pptx(self, output_dir: str):
        """导出为 PPTX 格式（并发渲染）"""
        if not self.slides:
            raise ValueError("没有幻灯片可导出")

        print(f"\n📊 导出 PPTX 格式（并发数：{MAX_CONCURRENT_SLIDES}）...")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 创建 PPTX (16:9 宽屏比例)
        prs = Presentation()
        prs.slide_width = Inches(10)  # 1280px / 96 DPI
        prs.slide_height = Inches(5.625)  # 720px / 96 DPI (10 × 9/16 = 5.625)

        # 使用 Playwright 并发渲染 HTML 为图片
        screenshots = [None] * len(self.slides)  # 预分配保持顺序
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SLIDES)

        async with async_playwright() as p:
            browser = await p.chromium.launch()

            async def render_single_slide(idx: int, slide: dict):
                async with semaphore:
                    print(f"   [{idx + 1}/{len(self.slides)}] 渲染幻灯片 {slide['id']}")

                    # 创建独立的页面实例，使用3倍像素密度提高清晰度
                    # deviceScaleFactor=3 生成 3840×2160 的高清截图
                    page = await browser.new_page(
                        viewport={'width': 1280, 'height': 720},
                        device_scale_factor=3
                    )
                    try:
                        # 渲染 HTML
                        html = self._wrap_single_slide(slide)
                        await page.set_content(html, wait_until='networkidle')

                        # 预览 HTML 与 PPTX 导出使用同一套尺寸收敛逻辑。
                        await _apply_slide_fit(page)

                        # 截图
                        screenshot = await page.screenshot(type='png', full_page=False)
                        screenshots[idx] = screenshot

                        print(f"   [{idx + 1}/{len(self.slides)}] ✓ 完成渲染幻灯片 {slide['id']}")
                    finally:
                        await page.close()

            # 并发渲染所有幻灯片
            await asyncio.gather(*[
                render_single_slide(idx, slide)
                for idx, slide in enumerate(self.slides)
            ])

            await browser.close()

        # 按顺序添加截图到 PPTX
        print(f"\n   组装 PPTX 文件...")
        for idx, screenshot in enumerate(screenshots):
            blank_slide_layout = prs.slide_layouts[6]  # 空白布局
            slide_obj = prs.slides.add_slide(blank_slide_layout)

            # 将截图添加为图片
            left = top = Inches(0)
            pic = slide_obj.shapes.add_picture(
                BytesIO(screenshot),
                left, top,
                width=prs.slide_width,
                height=prs.slide_height
            )

        # 保存 PPTX
        pptx_file = output_path / f"{self.outline['title']}.pptx"
        prs.save(str(pptx_file))

        print(f"✅ PPTX 文件已保存: {pptx_file}")
        return pptx_file

    def _generate_html_document(self) -> str:
        """生成完整的 HTML 文档"""
        slides_html_parts = []
        for slide in self.slides:
            html_content = _ensure_slide_container(_extract_slide_fragment(slide['html']))

            slides_html_parts.append(f'    <div class="slide-page">\n        {html_content}\n    </div>')

        slides_html = '\n'.join(slides_html_parts)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.outline['title']}</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;700;900&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        body {{
            margin: 0;
            padding: 0;
            overflow-x: hidden;
            background: transparent;
        }}
        .slide-page {{
            page-break-after: always;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .slide-container {{
            width: {PPT_SLIDE_WIDTH}px !important;
            height: {PPT_SLIDE_HEIGHT}px !important;
            max-width: {PPT_SLIDE_WIDTH}px !important;
            max-height: {PPT_SLIDE_HEIGHT}px !important;
            overflow: hidden !important;
            box-sizing: border-box;
        }}
    </style>
</head>
<body>
{slides_html}
</body>
</html>"""

    def _wrap_single_slide(self, slide: dict) -> str:
        """包装单张幻灯片为完整 HTML"""
        return wrap_slide_html(slide)



def wrap_slide_html(slide: dict, font_fallback: bool = True) -> str:
    font_config = get_optimal_font_settings()
    font_stack = font_config['font_family']
    css_props = font_config['css_properties']

    html_content = _ensure_slide_container(_extract_slide_fragment(slide.get('html', '')))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{slide.get('title', 'Slide')}</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    {'<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;700;900&display=swap" rel="stylesheet">' if font_fallback else ''}
    <link href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        * {{
            box-sizing: border-box;
            {'; '.join([f'{k}: {v}' for k, v in css_props.items()])};
        }}
        html {{
            margin: 0;
            padding: 0;
            width: {PPT_SLIDE_WIDTH}px;
            height: {PPT_SLIDE_HEIGHT}px;
            overflow: hidden;
        }}
        body {{
            margin: 0;
            padding: 0;
            width: {PPT_SLIDE_WIDTH}px;
            height: {PPT_SLIDE_HEIGHT}px;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
            background: transparent;
            font-family: {font_stack};
            font-size: 16px;
            line-height: 1.5;
        }}
        .slide-container {{
            width: {PPT_SLIDE_WIDTH}px !important;
            height: {PPT_SLIDE_HEIGHT}px !important;
            max-width: {PPT_SLIDE_WIDTH}px !important;
            max-height: {PPT_SLIDE_HEIGHT}px !important;
            overflow: hidden !important;
            box-sizing: border-box;
        }}
        .content-layer {{
            transform-origin: top center;
        }}
        div, p, span, h1, h2, h3, h4, h5, h6 {{
            font-family: {font_stack} !important;
            {'; '.join([f'{k}: {v}' for k, v in css_props.items() if k != 'font-family'])};
        }}
    </style>
</head>
<body>
    {html_content}
    {_PPT_AUTO_FIT_SCRIPT}
</body>
</html>"""

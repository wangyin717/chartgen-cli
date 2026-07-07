"""generate_html 工具：读取 workspace 中 LLM 生成的 HTML 文件，把本地图片路径替换为 base64，写到 outputs。"""
import base64
import logging
import os
import re
import shutil
import time

logger = logging.getLogger(__name__)

_workspace_dir: str = ""
_outputs_dir: str = ""


def set_dirs(workspace: str, outputs: str) -> None:
    global _workspace_dir, _outputs_dir
    _workspace_dir = workspace
    _outputs_dir = outputs


def _osc8_link(path: str, label: str) -> str:
    return f"\033]8;;file://{path}\033\\{label}\033]8;;\033\\"


def _embed_local_images(html: str) -> str:
    """把 HTML 中 <img src="本地绝对路径"> 替换为 base64 data URL。"""
    def _replace(m):
        path = m.group(1)
        if not os.path.isabs(path) or not os.path.exists(path):
            return m.group(0)
        try:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                    "gif": "gif", "webp": "webp"}.get(ext, "jpeg")
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return f'src="data:image/{mime};base64,{b64}"'
        except Exception:
            return m.group(0)

    return re.sub(r'src="([^"]+)"', _replace, html)


def run(html_file: str, output_name: str = "") -> str:
    if not _workspace_dir:
        return "[error] session 未初始化"

    # 支持绝对路径或相对 workspace 的路径
    path = html_file if os.path.isabs(html_file) else os.path.join(_workspace_dir, html_file)
    logger.info("[generate_html] source=%s", path)
    if not os.path.exists(path):
        return f"[error] 找不到文件: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        logger.warning("[generate_html] read failed: %s", e)
        return f"[error] 读取文件失败: {e}"

    original_len = len(html)
    html = _embed_local_images(html)
    logger.info("[generate_html] embedded images: html %d→%d chars", original_len, len(html))

    fname = output_name if output_name else f"report_{int(time.time())}.html"
    if not fname.endswith(".html"):
        fname += ".html"
    out_path = os.path.join(_outputs_dir, fname)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        logger.warning("[generate_html] write failed: %s", e)
        return f"[error] 写文件失败: {e}"

    logger.info("[generate_html] saved to %s", out_path)
    shutil.rmtree(os.path.join(_workspace_dir, "domain_images"), ignore_errors=True)
    if path != out_path:
        try:
            os.remove(path)
        except OSError:
            pass

    return f"HTML 报告已生成：\n  {_osc8_link(out_path, fname)}"


def thinking(args: dict) -> str:
    return f"embedding images → {args.get('output_name', 'report.html')}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_html",
        "description": (
            "将 LLM 在 ci 中生成并保存到 workspace 的 HTML 文件进行后处理："
            "把 <img src=\"本地路径\"> 替换为 base64 内嵌图片，输出为可独立打开的 HTML 文件到 outputs 目录。"
            "使用场景：LLM 用 ci 生成含本地图片路径的 HTML 后，调用此工具完成图片内嵌并保存最终报告。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "html_file": {
                    "type": "string",
                    "description": "workspace 中 HTML 文件的路径（绝对路径或相对 workspace 的文件名）",
                },
                "output_name": {
                    "type": "string",
                    "description": "输出文件名，如 'amazon_report.html'，默认自动生成",
                },
            },
            "required": ["html_file"],
        },
    },
}

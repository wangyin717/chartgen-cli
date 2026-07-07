"""
PPT生成引擎模块
"""
import json
import logging
from typing import AsyncGenerator, List, Dict, Any, Optional, Union, Tuple

from dataclasses import dataclass, field
from chartgen_cli.agent_loop import llm_client
from chartgen_cli.plugin.generate_ppt.ptompts import SLIDE_EDIT_PROMPT, MARKDOWN_TO_OUTLINE_PROMPT, OUTLINE_V2_PROMPT, DESIGN_PLAN_PROMPT, FINAL_SLIDE_PROMPT, IMAGE_GEN_PROMPT, GLOBAL_STYLE_SPEC_PROMPT, PPT_OVERFLOW_REPAIR_PROMPT
from chartgen_cli.plugin.generate_ppt.layouts import LAYOUT_LIBRARY_PROMPT


@dataclass
class LLMRecorder:
    header: dict = field(default_factory=dict)
    session_id: str = ""
    round_id: str = ""
    module_type: str = ""


async def _call_llm(messages: list, **_) -> str:
    resp = await llm_client.call("PPT", messages)
    return resp.text


MAX_MARKDOWN_LENGTH = 20000


class PPTGenerator:
    """PPT生成引擎"""

    def __init__(self, llm_recorder):
        self.llm_recorder = llm_recorder
    
    def _clone_llm_recorder(self, module_type_suffix: str = "") -> 'LLMRecorder':
        """
        创建llm_recorder的副本
        
        Args:
            module_type_suffix: 模块类型后缀，用于区分不同的调用场景
            
        Returns:
            新的LLMRecorder实例
        """
        import uuid
        new_module_type = f"{self.llm_recorder.module_type}{module_type_suffix}"
        return LLMRecorder(
            header=self.llm_recorder.header,
            session_id=self.llm_recorder.session_id,
            round_id=self.llm_recorder.round_id,
            module_type=self.llm_recorder.module_type
        )

    @staticmethod
    def _build_language_requirement(detected_language: str = "") -> str:
        """
        构建语言要求文本，用于注入到各阶段的 prompt 中
        
        Args:
            detected_language: 检测到的语言描述，如 'Chinese', 'English' 等
            
        Returns:
            语言要求文本段落
        """
        if not detected_language:
            return ""
        return f"""## 语言要求

**非常重要**：所有输出内容（标题、正文、要点、描述文字、thinking_process 等）必须使用 {detected_language} 语言。禁止混用其他语言。"""

    @staticmethod
    def _build_adjacent_layouts(outline: Dict[str, Any], slide_index: int) -> str:
        """
        构建相邻页面的布局信息，用于注入到设计方案 prompt 中
        """
        slides = outline.get('slides', [])
        lines = []
        
        if slide_index > 0:
            prev = slides[slide_index - 1]
            lines.append(f"- 上一页（第{prev.get('id')}页 「{prev.get('title', '')}」）布局：`{prev.get('layout_suggestion', 'unknown')}`")
        
        current = slides[slide_index]
        lines.append(f"- **当前页（第{current.get('id')}页 「{current.get('title', '')}」）建议布局：`{current.get('layout_suggestion', 'unknown')}`**")
        
        if slide_index < len(slides) - 1:
            nxt = slides[slide_index + 1]
            lines.append(f"- 下一页（第{nxt.get('id')}页 「{nxt.get('title', '')}」）布局：`{nxt.get('layout_suggestion', 'unknown')}`")
        
        lines.append("")
        lines.append("你必须确保当前页的 `layout_type` 与前后页不同。如果大纲建议的 layout_suggestion 与相邻页重复，请主动更换为其他布局类型。")
        
        return '\n'.join(lines)

    async def generate_global_style_spec(
            self,
            outline: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        生成全局色彩规范，确保所有幻灯片使用统一的调色板
        
        Args:
            outline: 大纲数据（包含 metadata.design_style）
            
        Returns:
            调色板字典，如:
            {
                "background": "#0f172a",
                "accent_primary": "#3b82f6",
                "accent_secondary": "#22d3ee",
                "text_primary": "#ffffff",
                "text_secondary": "#94a3b8",
                "card_bg": "#1e293b",
                "card_border": "#334155",
                "decoration": "rgba(34,211,238,0.15)",
                "top_bar": "#3b82f6",
                "overlay_bg": "rgba(2,11,28,0.7)"
            }
        """
        import logging
        logger = logging.getLogger(__name__)
        
        design_style = outline.get('metadata', {}).get('design_style', '简约白色背景+浅灰点缀+清新商务风')
        keywords = outline.get('metadata', {}).get('keywords', [])
        
        prompt = GLOBAL_STYLE_SPEC_PROMPT.format(
            design_style=design_style,
            ppt_title=outline.get('title', ''),
            keywords=', '.join(keywords) if keywords else '无',
        )
        
        response = await _call_llm([{"role": "user", "content": prompt}])
        logger.info(f"全局色彩规范 LLM 响应长度: {len(response)} 字符")
        
        # 解析 JSON
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                style_spec = json.loads(response[json_start:json_end])
                logger.info(f"✅ 全局色彩规范生成成功: background={style_spec.get('background')}, accent={style_spec.get('accent_primary')}")
                return style_spec
        except json.JSONDecodeError as e:
            logger.error(f"❌ 全局色彩规范 JSON 解析失败: {e}")
        
        # 根据 design_style 判断默认配色
        is_dark = any(kw in design_style for kw in ['深色', '科技', '深蓝', '暗色', 'dark', '渐变'])
        
        if is_dark:
            return {
                "background": "#0f172a",
                "accent_primary": "#3b82f6",
                "accent_secondary": "#22d3ee",
                "text_primary": "#ffffff",
                "text_secondary": "#94a3b8",
                "card_bg": "#1e293b",
                "card_border": "#334155",
                "decoration": "rgba(34,211,238,0.15)",
                "top_bar": "#3b82f6",
                "overlay_bg": "rgba(2,11,28,0.7)"
            }
        else:
            return {
                "background": "#FFFFFF",
                "accent_primary": "#3b82f6",
                "accent_secondary": "#6366f1",
                "text_primary": "#1e293b",
                "text_secondary": "#64748b",
                "card_bg": "#f8fafc",
                "card_border": "#e2e8f0",
                "decoration": "rgba(59,130,246,0.1)",
                "top_bar": "#3b82f6",
                "overlay_bg": "rgba(255,255,255,0.7)"
            }

    # ===== 三阶段生成方法 =====

    async def generate_outline_from_markdown(
            self,
            markdown_content: str,
            slide_count: Union[int, Tuple[int, int], None] = None,
            max_slides: Optional[int] = None,
            user_query: str = "",
            llm_recorder = None,
            detected_language: str = ""
    ) -> Dict[str, Any]:
        """
        从Markdown文档生成PPT大纲

        Args:
            markdown_content: Markdown文档内容
            slide_count: 幻灯片数量，支持 int、Tuple[int,int] 范围、None（LLM 自主决定）
            max_slides: 最大页数上限（与 slide_count 互斥，传此参数时 slide_count 不生效）
            user_query: 用户的原始查询（可能包含设计风格、颜色等要求）

        Returns:
            {
                "title": "PPT标题",
                "subtitle": "副标题",
                "metadata": {...},
                "slides": [...]
            }
        """
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"开始从Markdown生成大纲，内容长度: {len(markdown_content)}字符")
        logger.info(f"使用语言: {detected_language}")

        # 截断过长的内容（防止超出token限制）
        max_length = 50000  # 约5000个token
        if len(markdown_content) > max_length:
            logger.warning(f"Markdown内容过长({len(markdown_content)}字符)，截断为{max_length}字符")
            markdown_content = markdown_content[:max_length] + "\n\n... (内容已截断)"

        # 根据 slide_count 是否指定，构建不同的指令
        # 当 slide_count 有值时，强调「以参数为准，忽略 query 中的页数描述」
        priority_note = """
   - **重要**：上述页数要求为用户明确选择，必须严格遵守。即使用户查询（user_query）中提到了其他页数（如「5页」「20页」），也必须忽略，以本指令为准。"""
        if max_slides is not None:
            slide_count_instruction = f"""
   - 页数**不得超过** {max_slides} 页（硬性上限）
   - **用户 query 优先**：若用户明确要求生成 N 页，且 N ≤ {max_slides}，则按 N 页生成；若用户要求的页数 > {max_slides}，则按 {max_slides} 页生成
   - 若用户未指定页数，请根据内容丰富度自主决定，但不超过 {max_slides} 页
   - 保持每页信息密度适中"""
        elif slide_count is None:
            slide_count_instruction = """
   - 请根据内容的丰富度、复杂度和主题深度**自主决定**最合适的幻灯片数量
   - 推荐范围：6-15 页（内容简单时偏少，内容复杂时偏多）
   - 原则：每页聚焦一个核心观点，保持信息密度适中
   - 宁可多页清晰展示，不要单页信息过载"""
        elif isinstance(slide_count, tuple):
            min_p, max_p = slide_count
            slide_count_instruction = f"""
   - 目标生成 {min_p}-{max_p} 页
   - 请在此范围内根据内容丰富度决定具体页数
   - 保持每页信息密度适中{priority_note}"""
        else:
            slide_count_instruction = f"""
   - 目标生成 {slide_count} 页左右
   - 可根据内容丰富度适当调整（±2 页）
   - 保持每页信息密度适中{priority_note}"""

        prompt = MARKDOWN_TO_OUTLINE_PROMPT.format(
            user_query=user_query if user_query else "无特殊要求",
            markdown_content=markdown_content,
            detected_language=detected_language,
            slide_count_instruction=slide_count_instruction,
        )

        response = await _call_llm([{"role": "user", "content": prompt}])
        logger.info(f"LLM响应长度: {len(response)} 字符")
        logger.debug(f"LLM响应内容: {response[:500]}...")  # 只记录前500字符

        # 解析JSON
        json_start = -1
        json_end = -1
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                outline = json.loads(response[json_start:json_end])

                # 将精简版的 Markdown 内容添加到 metadata 中，供后续阶段参考
                # 保留前 10000 字符作为参考（约 2500 tokens）
                if 'metadata' not in outline:
                    outline['metadata'] = {}
                outline['metadata']['source_content'] = markdown_content[:MAX_MARKDOWN_LENGTH]
                if len(markdown_content) > MAX_MARKDOWN_LENGTH:
                    outline['metadata']['source_content'] += "\n\n...(更多内容省略)"

                # 验证并标准化 slides 中的 content_mode 字段
                if 'slides' in outline:
                    for slide in outline['slides']:
                        if 'content_mode' not in slide:
                            # 如果没有指定 content_mode，根据 layout_suggestion 推断
                            layout = slide.get('layout_suggestion', 'list')
                            if layout in ['list', 'timeline', 'process', 'action_plan']:
                                slide['content_mode'] = 'list'
                            elif layout in ['chart', 'table', 'big_number']:
                                slide['content_mode'] = 'data'
                            elif layout in ['left-right', 'comparison']:
                                slide['content_mode'] = 'mixed'
                            elif layout in ['cover', 'summary']:
                                slide['content_mode'] = 'paragraph'
                            else:
                                slide['content_mode'] = 'list'  # 默认值
                        
                        # 确保必需字段存在
                        if 'content_note' not in slide:
                            slide['content_note'] = ''

                logger.info(f"✅ Markdown大纲生成成功，共{len(outline.get('slides', []))}页")
                return outline
            else:
                logger.error("❌ 未在响应中找到有效的 JSON")
                logger.debug(f"完整响应: {response}")
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {e}")
            if json_start >= 0:
                logger.debug(f"尝试解析的内容: {response[max(0, json_start - 50):min(len(response), json_end + 50)]}")
        except Exception as e:
            logger.error(f"❌ 解析过程出现异常: {e}", exc_info=True)

        # 返回默认结构
        return {
            "title": "Markdown文档",
            "subtitle": "智能生成演示文稿",
            "metadata": {
                "author": "AI助手",
                "date": "2026.01",
                "keywords": ["文档", "分析"],
                "design_style": "现代简约风格"
            },
            "slides": [
                {"id": 1, "title": "封面", "type": "title", "key_points": [], "layout_suggestion": "cover"},
                {"id": 2, "title": "目录", "type": "toc", "key_points": [], "layout_suggestion": "list"},
            ]
        }

    async def generate_outline_v2(
            self,
            topic: str,
            requirements: Optional[str] = None,
            use_search: bool = False,
            slide_count: int = 6,
            llm_recorder = None,
            detected_language: str = ""
    ) -> Dict[str, Any]:
        """
        第一阶段：生成详细的PPT大纲

        Returns:
            {
                "title": "PPT标题",
                "subtitle": "副标题",
                "metadata": {
                    "author": "作者",
                    "date": "2026.01",
                    "keywords": ["关键词1", "关键词2"],
                    "design_style": "深蓝渐变背景+未来感线条+电子网格"
                },
                "slides": [...]
            }
        """
        import logging

        logger = logging.getLogger(__name__)

        context = ""
        # if use_search:
        #     try:
        #         logger.info(f"启用网络搜索功能，主题: {topic}")
        #         # 使用 search_with_content 获取详细内容
        #         results = await search_service.search_with_content(
        #             query=topic,
        #             max_results=3,  # 减少数量因为会抓取详细内容
        #             fetch_content=True,
        #             max_content_length=5000  # 每个网页最多2000字符
        #         )
        #
        #         if results:
        #             context = search_service.format_search_results(results)
        #             logger.info(f"✅ 搜索结果已整合，共 {len(results)} 条信息（含详细内容）")
        #         else:
        #             logger.warning("⚠️ 搜索未返回结果，将不使用搜索上下文继续生成")
        #             context = ""
        #     except Exception as e:
        #         logger.error(f"❌ 搜索过程出错，将不使用搜索结果继续生成: {e}")
        #         context = ""

        language_requirement = self._build_language_requirement(detected_language)
        prompt = OUTLINE_V2_PROMPT.format(
            topic=topic,
            requirements=requirements or "无特殊要求",
            search_context=context if context else "未使用网络搜索",
            slide_count=slide_count,
            language_requirement=language_requirement,
        )

        response = await _call_llm([{"role": "user", "content": prompt}])

        # 解析JSON
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                outline = json.loads(response[json_start:json_end])
                return outline
        except json.JSONDecodeError:
            pass

        # 返回默认结构
        return {
            "title": topic,
            "subtitle": "智能生成演示文稿",
            "metadata": {
                "author": "AI助手",
                "date": "2026.01",
                "keywords": [topic],
                "design_style": "现代科技风格"
            },
            "slides": [
                {"id": 1, "title": "封面", "type": "title", "key_points": [], "layout_suggestion": "cover"},
                {"id": 2, "title": "目录", "type": "toc", "key_points": [], "layout_suggestion": "list"},
            ]
        }

    async def generate_design_plan(
            self,
            outline: Dict[str, Any],
            slide_index: int,
            global_style_spec: Optional[Dict[str, Any]] = None,
            detected_language: str = "",
    ) -> Dict[str, Any]:
        """
        第二阶段：为单张幻灯片生成详细的设计思路

        Args:
            outline: 大纲数据
            slide_index: 幻灯片索引
            global_style_spec: 全局色彩规范（由 generate_global_style_spec 生成）

        Returns:
            {
                "slide_id": 3,
                "thinking_process": {
                    "analyzing_request": "...",
                    "content_structure": "...",
                    ...
                },
                "design_specifications": {...}
            }
        """

        slide = outline.get('slides', [])[slide_index]

        # 从 metadata 中提取参考内容（如果有）
        source_content = outline.get('metadata', {}).get('source_content', '')
        if not source_content:
            source_content = "无参考资料"

        # 获取全局设计风格
        design_style = outline.get('metadata', {}).get('design_style', '简约白色背景+浅灰点缀+清新商务风')
        
        # 构建全局色彩规范文本
        global_color_spec_text = self._format_global_color_spec(global_style_spec)
        
        language_requirement = self._build_language_requirement(detected_language)
        adjacent_layouts = self._build_adjacent_layouts(outline, slide_index)
        prompt = DESIGN_PLAN_PROMPT.format(
            ppt_title=outline.get('title', ''),
            ppt_metadata=json.dumps(outline.get('metadata', {}), ensure_ascii=False),
            design_style=design_style,
            global_color_spec=global_color_spec_text,
            layout_library=LAYOUT_LIBRARY_PROMPT,
            source_content=source_content,
            slide_info=json.dumps(slide, ensure_ascii=False),
            all_slides=json.dumps(outline.get('slides', []), ensure_ascii=False),
            language_requirement=language_requirement,
            adjacent_layouts=adjacent_layouts,
        )

        response = await _call_llm([{"role": "user", "content": prompt}])

        # 解析JSON
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                design_plan = json.loads(response[json_start:json_end])
                return design_plan
        except json.JSONDecodeError:
            pass

        # 返回默认结构
        return {
            "slide_id": slide.get('id'),
            "thinking_process": {
                "analyzing_request": "分析幻灯片需求",
                "design_choices": "选择合适的设计方案"
            },
            "design_specifications": {
                "layout_type": slide.get('layout_suggestion', 'default'),
                "key_elements": []
            }
        }

    @staticmethod
    def _format_global_color_spec(global_style_spec: Optional[Dict[str, Any]] = None) -> str:
        """
        将全局色彩规范格式化为 prompt 中可注入的文本
        """
        if not global_style_spec:
            return "未提供全局色彩规范，请根据设计风格自行选择协调的配色。"
        
        lines = []
        key_labels = {
            'background': '主背景色（所有页面统一）',
            'accent_primary': '主强调色（标题装饰线、图标、重要元素）',
            'accent_secondary': '辅助强调色（次要高亮）',
            'text_primary': '主文字色',
            'text_secondary': '辅助文字色、页码色',
            'card_bg': '卡片/模块背景色',
            'card_border': '卡片/模块边框色',
            'decoration': '装饰性元素颜色（网格线、分隔线）',
            'top_bar': '顶部装饰条颜色',
            'overlay_bg': '背景图遮罩色',
        }
        for key, label in key_labels.items():
            value = global_style_spec.get(key, '')
            if value:
                lines.append(f"- **{key}**: `{value}` — {label}")
        
        return '\n'.join(lines)

    async def generate_final_slide(
            self,
            slide_info: Dict[str, Any],
            design_plan: Dict[str, Any],
            total_slides: int = 1,
            source_content: str = "",
            global_style_spec: Optional[Dict[str, Any]] = None,
            detected_language: str = "",
    ) -> str:
        """
        第三阶段：基于设计方案生成最终的HTML（流式）

        Args:
            slide_info: 幻灯片信息
            design_plan: 设计思路
            total_slides: 幻灯片总数
            source_content: 参考资料内容（可选）
            global_style_spec: 全局色彩规范

        Yields:
            HTML内容片段
        """
        import logging

        logger = logging.getLogger(__name__)

        slide_id = slide_info.get('id', 1)

        # 0. 尝试生成背景图片
        background_image_url = ""
        actual_image_url = ""  # 存储实际的图片URL（可能是base64）
        use_placeholder = False  # 标记是否使用了占位符

        # 如果没有传入 source_content，尝试从 slide_info 获取
        if not source_content:
            source_content = "无参考资料"

        # 从全局色彩规范提取动态颜色值
        bg_color = (global_style_spec or {}).get('background', '#0f172a')
        overlay_bg = (global_style_spec or {}).get('overlay_bg', 'rgba(2,11,28,0.7)')
        decoration_color = (global_style_spec or {}).get('decoration', 'rgba(34,211,238,0.15)')
        text_secondary_color = (global_style_spec or {}).get('text_secondary', '#94a3b8')
        global_color_spec_text = self._format_global_color_spec(global_style_spec)

        language_requirement = self._build_language_requirement(detected_language)
        prompt = FINAL_SLIDE_PROMPT.format(
            slide_info=json.dumps(slide_info, ensure_ascii=False),
            design_plan=json.dumps(design_plan, ensure_ascii=False),
            global_color_spec=global_color_spec_text,
            source_content=source_content,
            background_image_url=background_image_url,
            bg_color=bg_color,
            overlay_bg=overlay_bg,
            decoration_color=decoration_color,
            text_secondary_color=text_secondary_color,
            slide_id=slide_id,
            total_slides=total_slides,
            language_requirement=language_requirement,
        )

        messages = [{"role": "user", "content": prompt}]

        # 为最终幻灯片生成创建新的llm_recorder副本
        final_llm_recorder = self._clone_llm_recorder("_final_slide")
        
        return await _call_llm(messages)

    async def repair_slide_overflow(
            self,
            *,
            slide_info: Dict[str, Any],
            design_plan: Dict[str, Any],
            html: str,
            validation_report: Dict[str, Any],
            global_style_spec: Optional[Dict[str, Any]] = None,
            detected_language: str = "",
    ) -> str:
        """
        根据真实浏览器校验报告重写单页 HTML，避免依赖导出/预览阶段缩放。
        """
        global_color_spec_text = self._format_global_color_spec(global_style_spec)
        language_requirement = self._build_language_requirement(detected_language)
        prompt = PPT_OVERFLOW_REPAIR_PROMPT.format(
            slide_info=json.dumps(slide_info, ensure_ascii=False),
            design_plan=json.dumps(design_plan, ensure_ascii=False),
            global_color_spec=global_color_spec_text,
            validation_report=json.dumps(validation_report, ensure_ascii=False, indent=2),
            html=html,
            language_requirement=language_requirement,
        )

        response = await _call_llm([{"role": "user", "content": prompt}])
        return response.strip()


# 全局生成器实例
# ppt_generator = PPTGenerator()

"""
Prompt模板
"""
from chartgen_cli.plugin.generate_ppt.layouts import LAYOUT_LIBRARY_PROMPT, SVG_COMPONENTS

SLIDE_EDIT_PROMPT = """你是一个PPT编辑助手。请根据用户的指令修改幻灯片内容。

当前幻灯片HTML:
{slide_html}

用户指令: {instruction}

请返回修改后的完整HTML代码。只返回HTML，不要其他解释。"""

# ===== 全局色彩规范生成提示词 =====

GLOBAL_STYLE_SPEC_PROMPT = """你是一个专业的PPT视觉设计师。请根据以下设计风格描述，生成一套**具体的、统一的**调色板规范，供整个PPT的所有页面共享使用。

## 设计风格描述

{design_style}

## PPT信息

标题: {ppt_title}
关键词: {keywords}

## 任务

请生成一个**精确的**调色板 JSON。所有颜色值必须使用具体的十六进制颜色码。

```json
{{
    "background": "#XXXXXX（主背景色，所有页面必须统一使用此背景色）",
    "accent_primary": "#XXXXXX（主强调色，用于标题装饰线、图标、重要元素）",
    "accent_secondary": "#XXXXXX（辅助强调色，用于次要高亮）",
    "text_primary": "#XXXXXX（主文字色）",
    "text_secondary": "#XXXXXX（辅助文字色、页码色）",
    "card_bg": "#XXXXXX（卡片/模块背景色）",
    "card_border": "#XXXXXX（卡片/模块边框色）",
    "decoration": "#XXXXXX（装饰性元素颜色，如网格线、分隔线）",
    "top_bar": "#XXXXXX（顶部装饰条颜色，通常与 accent_primary 相同）",
    "overlay_bg": "rgba(X,X,X,0.7)（背景图遮罩色，需要与 background 协调）"
}}
```

## 重要规则

1. 颜色之间必须有足够的对比度（文字在背景上清晰可读）
2. 如果风格描述中包含"白色"、"浅色"、"简约"，background 必须是浅色（如 #FFFFFF、#F8FAFC）
3. 如果风格描述中包含"深色"、"科技"、"深蓝"，background 必须是深色（如 #0f172a、#1e293b）
4. accent_primary 和 accent_secondary 之间应有色调关联但可区分
5. card_bg 应与 background 有细微差异，以产生层次感

**只返回 JSON，不要任何额外说明。**
"""

# ===== 三阶段生成提示词 =====

OUTLINE_V2_PROMPT = """你是一个顶级的PPT策划专家和内容架构师。请为以下主题生成一个**详细、专业**的PPT大纲。

主题: {topic}
需求: {requirements}
参考资料: {search_context}
幻灯片数量: 约{slide_count}页

请以JSON格式返回大纲，格式如下:

```json
{{
    "title": "PPT主标题",
    "subtitle": "副标题或介绍语",
    "metadata": {{
        "author": "**仅当需求中明确提到身份时才填写**，否则留空字符串",
        "date": "**仅当需求中明确提到当前日期/ppt演讲时间时才填写**，否则留空字符串",
        "keywords": ["关键词1", "关键词2", "关键词3"],
        "design_style": "简约白色背景+浅灰点缀+清新商务风"
    }},
    "slides": [
        {{
            "id": 1,
            "title": "幻灯片标题",
            "type": "title|toc|content|summary",
            "key_points": [
                "要点1：具体描述",
                "要点2：具体描述",
                "要点3：具体描述"
            ],
            "layout_suggestion": "left-right|card|comparison|list|cover"
        }}
    ]
}}
```

## 重要要求

1. **元数据生成规则**
   - **author（作者）字段**：
     - 仅当"需求"中明确提到身份时才填写（如"我是张三"、"公司：XX科技"）
     - 否则必须留空字符串 `""`
     - 不要根据主题自行推断或编造作者信息
   - **date（日期）字段**：
     - 仅当"需求"中明确提到当前日期/ppt演讲时间时才填写（如"2026年1月报告"、"Q1季度"）
     - 否则必须留空字符串 `""`
     - 不要自动填写当前日期
   - keywords: 提取3-5个核心关键词
   - design_style: 描述整体设计风格（颜色+元素+效果）
     - **重要**：如果用户需求中没有明确指定设计风格或颜色偏好，默认使用**浅色/白色背景**的简约风格
     - 默认风格示例："简约白色背景+浅灰点缀+清新商务风"、"纯白背景+淡蓝强调色+极简专业风"

2. **详细的幻灯片信息**
   - id: 从1开始的序号
   - title: 清晰、吸引人的标题
   - type: 准确分类（title/toc/content/summary）
   - key_points: 每页2-4个具体的要点，而不是模糊的描述
   - layout_suggestion: 建议的布局类型
     - `cover`: 封面页（背景图+大标题）
     - `left-right`: 左右分栏（文字+图片）
     - `card`: 卡片式布局（多个信息卡片）
     - `comparison`: 对比式（左右对比两个概念）
     - `list`: 列表式（编号列表或要点列表）

3. **内容深度**
   - 每个要点应该具体、有价值
   - 避免空洞的口号
   - 提供可执行的信息
   - **重要**：每页幻灯片最多4个要点，每个要点控制在30字以内

4. **内容约束（防止溢出）**
   - 目录页最多展示5-6个章节标题
   - 时间轴页最多展示4-5个时间节点
   - 对比页每侧最多3个要点
   - 卡片布局最多2x2=4个卡片
   - 列表布局最多5-6个条目

5. **逻辑结构**
   - 第1页：封面（type: "title", layout: "cover"）
   - 第2页：目录（type: "toc", layout: "list"）
   - 第3-N-1页：内容页（type: "content", layout根据内容选择）
   - 第N页：总结（type: "summary", layout: "card"或"list"）

6. **布局多样性（极其重要）**
   - **相邻的内容页绝对不得使用相同的 `layout_suggestion`**
   - 组合示例：cover → list → card → left-right → comparison → list → card
   - 如果连续多页内容类型相似，交替使用 card/left-right/comparison/list
   - 这是硬性要求，绝不允许连续两页以上使用相同布局

{language_requirement}

**只返回JSON，不要任何额外说明。**
"""

DESIGN_PLAN_PROMPT = """你是一个专业的PPT视觉设计师和交互设计师。请为以下幻灯片生成**详细的设计思路和规划**。

## PPT信息

标题: {ppt_title}
元数据: {ppt_metadata}

## 🔴 全局风格一致性要求（最高优先级）

**极其重要**：本PPT的全局设计风格已在大纲中确定为：`{design_style}`

### 🎨 全局调色板规范（必须严格使用以下色值）

{global_color_spec}

**以上色值是经过统一生成的全局调色板，你必须在 `design_specifications.color_scheme` 中使用这些精确的色值，禁止自行更改。**

你**必须严格遵守**以下一致性规则：

1. **背景色必须使用全局调色板中的 `background` 值**
   - **禁止**使用任何其他背景色
   - **禁止**在同一PPT中混用深色和浅色背景

2. **强调色必须使用全局调色板中的 `accent_primary` 和 `accent_secondary`**
   - 标题装饰线、图标颜色、边框高亮等必须使用这些统一色值

3. **装饰元素必须统一**：
   - 如果使用SVG网格装饰，所有页面都使用相同样式的网格
   - 如果使用顶部装饰条，所有页面都使用 `top_bar` 颜色
   - 卡片样式使用 `card_bg` 和 `card_border` 色值

4. **参考已有幻灯片设计**：
   - 查看"所有幻灯片"列表，确保与其他页面风格协调

{layout_library}

## 参考资料（如果有）

{source_content}

**说明**：如果上面提供了参考资料，请在设计时参考其中的具体数据、观点和细节，让幻灯片内容更加丰富准确。

## 当前幻灯片

幻灯片信息: {slide_info}

## 上下文

所有幻灯片: {all_slides}

## 🟡 布局多样性要求（必须遵守）

{adjacent_layouts}

**你必须选择与相邻页面不同的布局类型。**如果前后页都用了卡片布局，你必须选择 left-right、comparison、list 等其他布局。

## 任务

请像一个真正的设计师一样，**完整地思考和规划这张幻灯片的设计**。如果有参考资料，请从中提取相关的具体内容来丰富这一页。然后以JSON格式返回：

```json
{{
    "slide_id": 3,
    "thinking_process": {{
        "analyzing_request": "我正在分析这张幻灯片的需求...\n核心内容是XXX，需要对比展示YYY和ZZZ...",

        "content_structure": "我决定采用以下内容结构：\n- 标题：XXX\n- 左栏：YYY的特点\n- 右栏：ZZZ的特点\n...",

        "layout_design": "我选择双栏布局，因为...\n左栏使用蓝色调，右栏使用青色调...\n中间添加箭头连接器...",

        "code_implementation": "我将使用Flexbox实现左右布局...\n避免使用CSS渐变，改用纯色背景和半透明遮罩...\n使用SVG绘制简单的示意图...",

        "design_choices": "我选择深色背景是因为...\n字体大小：标题48px，正文16px...\n颜色对比度确保文字清晰可读...",

        "style_consistency_check": "我确认本页设计与全局风格 '{design_style}' 一致：\n- 背景色与其他页面相同\n- 强调色使用统一的色调\n- 装饰元素风格保持一致",

        "finalization": "最终确认：\n- 所有元素对齐\n- 颜色搭配协调\n- 内容完整\n- 没有使用渐变\n- ✅ 风格与全局设计一致"
    }},

    "design_specifications": {{
        "layout_type": "dual-column",
        "color_scheme": {{
            "background": "#0f172a",
            "left_accent": "#60a5fa",
            "right_accent": "#22d3ee",
            "text_primary": "#ffffff",
            "text_secondary": "#94a3b8"
        }},
        "typography": {{
            "title_size": "48px",
            "heading_size": "24px",
            "body_size": "16px",
            "font_family": "'Noto Sans SC', sans-serif"
        }},
        "key_elements": [
            "SVG grid background",
            "Icon boxes with Font Awesome icons",
            "Comparison arrow in center",
            "Dual-column content cards",
            "Simple concept visualization"
        ],
        "visual_hierarchy": [
            "1. Title at top",
            "2. Subtitle below title",
            "3. Two side-by-side content columns",
            "4. Page number at bottom right"
        ]
    }}
}}
```

## 思考过程要求

你的 `thinking_process` 应该：

1. **analyzing_request**: 深入分析这张幻灯片的目标和内容需求
2. **content_structure**: 确定内容如何组织和呈现
3. **layout_design**: 选择布局类型，解释为什么选择这个布局
4. **code_implementation**: 思考具体的HTML/CSS实现方法
5. **design_choices**: 解释设计决策（颜色、字体、间距等）
6. **finalization**: 最终检查和确认

## 设计规范要求

每个部分都要**具体、详细**，就像你真的在设计这张幻灯片一样。

## ⚠️ 布局约束（必须遵守）

1. **尺寸限制**：
   - 容器固定为 1280x720px
   - content-layer 的 padding 为 60px 80px
   - 实际可用内容区域约为 1120x600px

2. **元素数量限制**：
   - 列表/要点：最多 5 条
   - 卡片布局：最多 2x2 = 4 个
   - 时间轴：最多 4 个节点
   - 目录章节：最多 6 个

3. **防止元素重叠**：
   - 所有内容必须在 content-layer 内部
   - 禁止在底部添加与页码重叠的装饰文字
   - 相邻元素之间保持至少 20px 间距
   - 如果使用绝对定位，必须仔细计算坐标，确保不与其他元素重叠

4. **垂直空间分配**：
   - 标题区：约 100-120px
   - 主体内容区：约 400-450px
   - 底部留白：至少 50px（为页码预留空间）

5. **设计检查清单**：
   在 finalization 部分必须确认：
   - [ ] 内容总高度不超过 600px
   - [ ] 所有元素在 content-layer 内
   - [ ] 元素间距合理，无重叠
   - [ ] 底部预留了页码空间
   - [ ] 字体大小符合可读性要求

{language_requirement}

**只返回JSON，不要其他解释。**
"""

IMAGE_GEN_PROMPT = """你是一个专业的AI绘画提示词专家。请根据以下幻灯片信息，生成一个详细的英文绘画提示词（Prompt），用于生成高质量的背景图片。

## 幻灯片信息
标题: {title}
关键要点: {key_points}
设计风格: {design_style}

## 要求
1. 提示词必须是英文
2. 描述一个高品质、意境优美的背景画面
3. 画面必须简洁、留白足够，适合作为PPT背景（不要过于杂乱）
4. 风格要符合"design_style"的描述
5. 包含一些高质量的修饰词，如 "4k resolution", "minimalist", "professional", "abstract", "soft lighting" 等
6. 不要包含文字、文本或字母

只返回英文提示词，不要其他解释。
"""

PPT_OVERFLOW_REPAIR_PROMPT = """你是一个专业的PPT前端排版修复工程师。下面这页幻灯片 HTML 已经生成，但在真实浏览器中出现了尺寸/溢出问题。请根据校验报告重写 HTML，使其在 1280x720 幻灯片内自然适配。

## 幻灯片信息

{slide_info}

## 设计方案

{design_plan}

## 全局调色板规范

{global_color_spec}

## 浏览器校验报告

{validation_report}

## 当前有问题的 HTML

{html}

## 修复要求

1. 必须返回完整的幻灯片 HTML 片段，以 `<div class="slide-container"...>` 作为根节点。
2. `.slide-container` 必须是 `width: 1280px; height: 720px; overflow: hidden; position: relative;`。
3. 主要内容必须全部放在 `.content-layer` 内，`.content-layer` 必须有确定高度或 `max-height`，且真实内容高度不得超过可见高度。
4. 不要使用 `transform: scale(...)`、`zoom`、JavaScript、动画、交互或外部脚本来掩盖溢出；必须通过真实布局修复。
5. 禁止用内部容器的 `overflow: hidden` 裁掉表格、列表、卡片或说明文字来假装适配；所有核心内容必须完整可见。
6. 优先删减或合并文字，压缩 margin/padding/gap，降低卡片高度，必要时减少条目数量。
7. 保持原主题、核心数据、全局配色和视觉风格，但可以简化非关键文案。
8. 所有底部内容必须避开页码区域；页码之外不得在 `.content-layer` 外放置文字。
9. 只返回 HTML 代码，不要 ```html 标记，不要解释。

{language_requirement}
"""

FINAL_SLIDE_PROMPT = """你是一个专业的前端开发工程师和PPT设计师。请根据设计方案生成**最终的HTML代码**。

## 幻灯片信息

{slide_info}

## 设计方案

{design_plan}

## 🔴 全局调色板规范（最高优先级，必须严格使用）

{global_color_spec}

**以上色值是整个PPT统一的全局调色板。你必须使用这些精确的颜色值，禁止自行选择其他颜色。**

## 参考资料（如果有）

{source_content}

**说明**：如果上面提供了参考资料，请从中提取与本页主题相关的**具体内容、数据、案例**来充实幻灯片，而不是只展示大纲要点。让内容更加丰富和有说服力。

## 背景图片设置
**重要**：
- 如果提供了 `background_image_url`，请**必须**使用该URL作为 `.bg-image` 的 `src` 属性。
- 如果没有提供（为空），请自行从Unsplash选择合适的图片。

背景图URL: {background_image_url}

## 任务

根据上述设计方案、全局调色板和参考资料，生成这张幻灯片的**完整HTML代码**。

**重点**：如果有参考资料，请结合幻灯片主题，从参考资料中提取相关的具体内容填充到HTML中，而不是仅仅基于 key_points 生成内容。

## 技术规范

### HTML结构
使用1280x720的固定容器，所有样式使用内联style。**注意：下方模板中的颜色仅为示例，你必须替换为上面全局调色板中的对应色值。**

```html
<div class="slide-container" style="width: 1280px; height: 720px; position: relative; overflow: hidden; background-color: {bg_color};">
    <!-- 背景层 (z-index: 1-3) -->
    <img src="{background_image_url}" class="bg-image" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0.6; z-index: 1;">
    <div class="bg-overlay" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: {overlay_bg}; z-index: 2;"></div>

    <!-- SVG网格（使用全局调色板的 decoration 色值） -->
    <svg class="svg-grid" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 2; opacity: 0.2;" xmlns="http://www.w3.org/2000/svg">
        <defs>
            <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                <path d="M 40 0 L 0 0 0 40" fill="none" stroke="{decoration_color}" stroke-width="1"/>
            </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)"/>
    </svg>

    <!-- 内容层 (z-index: 10+) -->
    <div class="content-layer" style="position: relative; z-index: 10; padding: 60px 80px; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; overflow: hidden; max-height: 620px;">
        <!-- 您的内容 -->
    </div>

    <!-- 页码 -->
    <div class="slide-number" style="position: absolute; bottom: 40px; right: 40px; color: {text_secondary_color}; font-size: 14px; z-index: 20;">
        {slide_id} / {total_slides}
    </div>
</div>
```

### 样式规范

1. **禁止使用CSS渐变**（linear-gradient, radial-gradient）
2. **使用内联样式**（所有CSS写在style属性中）
3. **使用Noto Sans SC字体**
4. **配色方案**：**必须严格使用全局调色板中的色值**，确保与其他幻灯片风格一致
5. ***绝对静态**：禁止使用JavaScript、CSS动画、过渡效果、鼠标悬停效果、点击事件或任何形式的交互。

### 🔴 风格一致性（强制要求）

**极其重要**：本页的背景颜色、强调色、装饰元素必须使用全局调色板。
- `.slide-container` 的 `background-color` 必须使用全局调色板的 `background` 值
- 图标、装饰线、边框颜色必须使用 `accent_primary` 或 `accent_secondary`
- 卡片/模块背景必须使用 `card_bg`，边框使用 `card_border`
- **禁止**自行更改配色方案，**禁止**使用全局调色板之外的颜色

### FontAwesome图标

使用CDN图标：
```html
<i class="fas fa-rocket" style="color: #22d3ee; font-size: 24px;"></i>
```

### 背景图片

- 如果 `background_image_url` 有值，直接使用。
- 如果没有，从Unsplash选择合适的科技/商务图片。

## 重要提示

1. **严格遵循设计方案**：按照 design_plan 中的设计决策实现
2. **完整的HTML**：生成可以直接使用的完整HTML代码
3. **内容丰富**：不要只是框架，要填充实际内容
4. **专业美观**：确保视觉效果精美、专业

## ⚠️ 尺寸约束（极其重要，必须严格遵守）

### 🔴 硬性规则（违反将导致显示错误）

1. **容器尺寸固定**：`width: 1280px; height: 720px` 不可更改
2. **必须设置 overflow: hidden**：
   - `.slide-container`: `overflow: hidden`
   - `.content-layer`: `overflow: hidden; max-height: 620px`
3. **内容可用区域**：扣除 padding 后，实际可用高度约 **580-600px**
4. **所有元素必须在边界内**：
   - 任何元素的 `top + height` 不得超过 680px
   - 任何元素的 `left + width` 不得超过 1240px
   - 禁止使用负 margin 导致元素超出容器

### 🟡 内容数量硬性限制

| 布局类型 | 最大数量 | 单项最大高度 |
|---------|---------|-------------|
| 列表/要点 | 5 条 | 80px |
| 时间轴 | 4 个节点 | 120px |
| 卡片布局 | 4 个 (2x2) | 240px |
| 目录章节 | 6 个 | 60px |
| 对比栏 | 每侧 3 条 | 150px |

### 🟢 字体大小规范

- 主标题：36-48px
- 副标题/章节标题：24-32px
- 正文内容：16-20px（最小不低于 14px）
- 辅助文字/页码：12-14px

### ⚠️ 内容过多时的处理策略

1. **优先删减**：减少条目数量，保留最重要的内容
2. **压缩间距**：将 margin/padding 减少到最小可读值
3. **缩小字体**：在保证可读性的前提下缩小字体（不低于下限）
4. **简化文案**：将长句改为短语或关键词

### 🚫 禁止事项

- 禁止内容超出 720px 高度
- 禁止使用会导致内容溢出的大间距
- 禁止忽略内容数量限制

### 📐 内容分布规范（避免留白过大）

1. **垂直居中**：内容层使用 `justify-content: center` 使内容垂直居中
2. **内容少时**：
   - 适当增大标题字体（可达 56-64px）
   - 增加元素间距，让内容均匀分布
   - 添加装饰性元素（图标、分隔线、副标题）填充空间
3. **封面页/总结页**：内容应居中显示，上下留白均匀
4. **内容页**：标题在上方，主体内容垂直居中或均匀分布

### 🚨 元素重叠防范（极其重要）

1. **所有内容必须在 content-layer 内部**：
   - 禁止在 content-layer 外部添加任何文字或装饰元素（页码除外）
   - 装饰性文字、标签、说明文字必须作为 content-layer 的子元素

2. **绝对定位元素的约束**：
   - 如果使用 `position: absolute`，必须确保父容器是 content-layer 或其子元素
   - 底部元素的 bottom 值必须 ≥ 80px（为页码预留空间）
   - 顶部元素的 top 值必须 ≥ 0

3. **元素间距**：
   - 相邻元素之间至少保持 20px 间距
   - 卡片/列表项之间至少 15px 间距
   - 标题与内容之间至少 30px 间距

4. **z-index 分层规范**：
   - 背景层：1-3
   - 装饰元素（如分隔线、背景形状）：5-9
   - 主要内容：10-19
   - 页码：20
   - **禁止随意设置高 z-index 导致层级混乱**

5. **布局验证检查清单**：
   - [ ] 所有文字内容都在 content-layer 内
   - [ ] 没有元素超出 720px 高度
   - [ ] 底部元素与页码位置不冲突
   - [ ] 相邻元素有足够间距，无重叠
   - [ ] 绝对定位元素的坐标合理

{language_requirement}

**只返回HTML代码，不要```html标记，不要其他解释。**
"""

MARKDOWN_TO_OUTLINE_PROMPT = """你是一个顶级的PPT策划专家和内容架构师。请基于以下参考资料，创作一个**专业、吸引人、结构多样化**的PPT大纲。

## 用户原始查询

{user_query}

**说明**：用户的查询可能包含两类信息：
1. **与PPT设计相关的要求**：如背景颜色、设计风格、主题偏好等 - 请在设计时**严格遵守**这些要求
2. **与PPT内容相关的主题**：如"分析最近三天英伟达和微软的股价" - 这类内容已经体现在下面的参考资料中

请仔细分析用户查询，提取其中的设计要求（如有），并在大纲的 `metadata.design_style` 和各页设计中体现。

## 参考资料（Markdown文档）

{markdown_content}

## 语言要求

**非常重要**：生成的PPT内容（标题、副标题、要点等）的语言必须与参考资料的主要语言保持一致。
- 如果参考资料主要是中文，则大纲和所有内容必须使用中文
- 如果参考资料主要是英文，则大纲和所有内容必须使用英文

检测到的参考资料语言：{detected_language}

## 创作要求

**重要：你不需要严格按照 Markdown 的结构来组织 PPT。请将 Markdown 视为参考资料，自由发挥创意，设计最佳的演示结构。**

1. **内容提炼**
   - 深度阅读 Markdown 内容，理解核心主题和关键信息
   - 提取最有价值的数据、观点、结论
   - 识别适合可视化展示的内容（数据、对比、流程等）
   - 过滤技术性内容（HTML 代码、复杂表格等）

2. **PPT 结构设计（总-分-总）**
   - **总（开篇）**：
     - **封面页**：根据内容主题设计吸引人的标题（可以比 Markdown 标题更精炼）
     - **目录/概述页**：设计清晰的逻辑框架（不必完全对应 Markdown 章节）
     - **核心观点摘要页**（使用 layout:`big_number` 震撼开场）
   - **分（详细展开）**：根据内容选择 Timeline, Comparison, Chart 等丰富布局
     - 按照演示逻辑组织，而非 Markdown 顺序
     - 每页聚焦一个核心观点或数据洞察
     - 将复杂内容拆分为多页，保持简洁
     - 数据密集内容用可视化方式呈现
   - **总（结尾）**：总结页（提炼核心结论和要点） -> 呼吁行动(`layout: action_plan`) -> 结束页

3. **要点精炼原则**
   - 每个要点控制在 20-30 字以内
   - 突出数字、百分比、关键结论
   - 使用简洁有力的表述
   - 每页最多 3-4 个要点

4. **布局建议：详细的幻灯片信息规范**
    - `content_mode`: `list` (列表), `paragraph` (深度段落), `data` (数据), `mixed` (混合)，.
    - `layout_suggestion`: `cover`, `list`, `card`, `left-right`, `big_number`, `chart`, `table`, `timeline`, `process`, `dashboard`, `comparison`, `swot`, `pyramid`, `action_plan`, `summary`.

   **这是最重要的步骤**：请深入分析 Markdown 的内容逻辑，**不要仅仅生成普通的列表页**。根据内容特征，匹配以下高级布局：

   | Markdown 内容特征 | 必须使用的 Layout Suggestion | 推荐 Content Mode |
   | :--- | :--- | :--- |
   | **涉及多个 KPI 指标、数据概览、核心数字** | `dashboard` (仪表盘) | `mixed` |
   | **涉及年份、日期、阶段、发展历程** | `timeline` (时间轴) | `list` |
   | **涉及步骤、流程、循环、逻辑顺序** | `process` (流程图) | `list` |
   | **涉及优劣势对比、竞品分析、A vs B** | `comparison` (对比分析) | `list` |
   | **涉及SWOT分析、四象限分类** | `swot` (四象限) | `list` |
   | **涉及组织架构、层级关系、优先级** | `pyramid` (金字塔/层级) | `list` |
   | **涉及下一步计划、待办事项** | `action_plan` (行动计划) | `list` |
   | **涉及总结、核心结论、摘要** | `summary` (摘要总结) | `mixed` |
   | **包含具体数据表格或统计趋势** | `chart` (图表) 或 `table` (表格) | `data` |
   | **包含极具冲击力的单一数据或金句** | `big_number` (大数字/金句) | `data` |
   | **深度解析某个概念或故事** | `left-right` (左图右文) | `paragraph` |
   | **普通的清单或要点** | `list` (列表) | `list` |

   - **多样性要求**: **禁止**连续 3 页使用相同的 layout。

5. **幻灯片数量**{slide_count_instruction}

6. **元数据生成规则**
   - **author（作者）字段**：
     - 仅当用户查询中明确提到身份时才填写（如"我是张三"、"公司：XX科技"）
     - 否则必须留空字符串 `""`
     - 不要根据内容自行推断或编造作者信息
   - **date（日期）字段**：
     - 仅当用户查询中明确提到当前日期/ppt演讲时间时才填写（如"2026年1月报告"、"Q1季度"）
     - 否则必须留空字符串 `""`
     - 不要自动填写当前日期或根据内容推断日期
   - 包含 `design_style` (简短描述即可，如 "Clean Business", "Dark Tech")。
     
请以JSON格式返回大纲，格式如下:

```json
{{
    "title": "精炼的演示标题（可以不同于 Markdown 标题）",
    "subtitle": "副标题或核心价值主张",
    "metadata": {{
        "author": "**仅当用户查询中明确提到身份时才填写**，否则留空字符串",
        "date": "**仅当用户查询中明确提到当前日期/ppt演讲时间时才填写**，否则留空字符串",
        "keywords": ["关键词1", "关键词2", "关键词3"],
        "design_style": "根据主题选择的设计风格。**重要：如果用户查询中没有明确指定颜色或风格偏好，默认使用浅色/白色背景风格**（如：简约白色背景+浅灰点缀+清新商务风、纯白背景+淡蓝强调色+极简专业风）",
        "source_summary": "用1-2句话总结 Markdown 核心内容，供后续幻灯片生成参考"
    }},
    "slides": [
        {{
            "id": 1,
            "title": "封面标题",
            "type": "title",
            "key_points": [],
            "layout_suggestion": "cover",
            "content_mode": "paragraph",  
            "content_note": "从 Markdown 提取的相关信息摘要"
        }},
        {{
            "id": 2,
            "title": "核心洞察或目录",
            "type": "toc",
            "key_points": ["逻辑点1", "逻辑点2", "逻辑点3"],
            "layout_suggestion": "list",
            "content_mode": "list",  
            "content_note": "对应 Markdown 中的哪些内容"
        }},
        {{
            "id": 3,
            "title": "具体内容页标题",
            "type": "content",
            "key_points": [
                "要点1：简洁有力（20-30字）",
                "要点2：包含数据更佳",
                "要点3：突出结论"
            ],
            "layout_suggestion": "card",
            "content_mode": "mixed",  
            "content_note": "这页内容基于 Markdown 的 XX 部分，重点展示 YY 数据"
        }}
    ]
}}
```

## 内容约束

- 每页幻灯片 2-4 个要点
- 每个要点 20-30 字以内
- 目录页最多 5-6 项
- 数据页重点突出关键数字
- 避免技术术语堆砌

## 特殊内容处理

1. **表格数据**：提取关键数值，设计为卡片式对比
2. **HTML/代码块**：完全忽略，只提取描述性文字
3. **长段落**：提炼核心观点，用要点呈现
4. **数据分析**：数字 + 结论的形式

**重要提示**：
- 在每个 slide 的 `content_note` 字段中，简要说明这页内容来源于 Markdown 的哪部分，以便后续生成时参考
- `metadata.source_summary` 也要填写，方便整体设计时保持风格一致

**只返回JSON，不要任何额外说明。**
"""

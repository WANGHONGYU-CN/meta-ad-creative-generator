"""6 套提示词模板的默认值、读写与变量渲染。

模板中的变量写作 {variable_name}，渲染时逐个字符串替换（不用 str.format，
这样模板里可以放 JSON 示例的花括号而不需要转义）。
"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_PATH = PROJECT_ROOT / "prompts.json"

DEFAULT_PROMPTS = {
    "scene_mining": {
        "name": "① 场景挖掘",
        "description": "根据产品信息+排除场景列表，挖掘带评分的主/细分场景（目标用户/触发时刻/痛点渴望/产品使用链路 + 五维评分）",
        "variables": ["product_info", "excluded_scenes"],
        "template": """你是一位资深的 Meta（Facebook / Instagram）效果广告策略专家，擅长从产品能力、目标人群、触发时刻和购买动机中，挖掘可以直接用于广告创意测试的高转化场景。
你必须全网搜索这个应用的使用场景和细分场景

## 产品信息

{product_info}

## 已使用或需要排除的场景

{excluded_scenes}

如果没有需要排除的场景，`excluded_scenes` 传入空数组 `[]`。

## 任务目标

为该产品挖掘适合 Meta 信息流广告测试的高转化场景。

请先在内部生成不少于 30 个候选细分场景，完成去重、分类和评分后，把去重后的候选**全部输出**——不做分数门槛淘汰，评分供使用者自行筛选。不要输出分析过程。

## 一、主场景定义

输出 3-6 个主场景，把全部细分场景归入最匹配的主场景。

主场景必须代表一种独立、明确的用户需求、购买动机或使用目的，例如：

- 婚礼全周期
- 宠物成长与陪伴
- 创作者内容生产
- 品牌与产品推广
- 家庭成长记录
- 生日与个性化礼物

主场景不能只是地点、时间、渠道、受众标签或空泛情绪。

禁止使用以下类型的主场景名称：

- 日常生活
- 室内场景
- 户外场景
- 年轻人
- 社交媒体
- 情感共鸣
- 其他场景
- 综合场景

不同主场景之间必须有明显差异，不能只是换一种说法。

## 二、细分场景定义

每个主场景至少包含 2 个细分场景；去重后的候选全部输出，不设数量上限。

每个细分场景必须同时包含：

1. 明确的目标用户
2. 明确的触发时刻或具体事件
3. 明确的痛点、渴望或购买动机
4. 明确说明用户如何使用产品
5. 明确说明产品最终生成或带来的结果
6. 可以直接画成一张广告海报的具体画面

细分场景必须是一个单一、具体的任务，不能把多个场景合并在一起。

不合格示例：

- 制作家庭视频
- 记录美好生活
- 宠物陪伴
- 创作者制作内容
- 企业进行宣传
- 把照片变成视频

合格示例：

- 把宠物领养第一天的照片和歌曲做成成长纪念视频
- 把普通白底产品照变成带有电影镜头的新品广告片
- 把宝宝第一次走路的手机照片做成家庭纪念短片
- 把婚礼誓言、合照和音乐做成婚宴现场播放影片
- 把摄影师的静态作品集做成带音乐的展览预告片

## 三、产品相关性要求

只能使用产品信息中明确存在的功能，不得虚构功能、价格、速度、模板、版权能力或输出规格。

每个细分场景都必须明确体现以下链路：

“用户拥有什么素材或问题 → 如何使用产品 → 得到什么结果 → 结果用于什么目的”

如果产品属于图片、音乐或视频生成类产品，还必须在 `product_use` 中明确表现：

“输入照片、图片、音乐或其他素材 → 生成视频成片”

不能只描述人物和环境，却看不出产品能做什么。

## 四、转化价值评分

为每个细分场景进行 100 分制评分：

- `product_fit`：产品匹配度，0-30 分
- `visual_clarity`：广告画面直观度，0-25 分
- `purchase_intent`：付费或行动意愿，0-20 分
- `attention_emotion`：停留与情绪吸引力，0-15 分
- `meta_safety`：Meta 投放安全性，0-10 分

`total_score` 必须等于以上五项之和。

评分不是输出门槛：所有去重后的细分场景无论得分高低都要输出，评分只用于给使用者排序和筛选。必须如实打分，不得虚高或压低。

## 五、去重与分布要求

- 不得输出与 `excluded_scenes` 相同或高度近似的场景
- 同一主场景下的细分场景必须对应不同触发时刻或不同购买动机
- 不得只替换人物年龄、性别、地点或节日后当成新场景
- 不得让所有场景集中在同一种内容类型
- 优先覆盖不同人群、不同事件和不同商业目的
- 每个细分场景只能归入一个最匹配的主场景

## 六、Meta 合规要求

避免：

- 暗示用户具有敏感个人属性
- 制造羞辱、恐惧或过度焦虑
- 未经证实的效果承诺
- 公众人物、明星脸或受版权保护的角色
- 不适合广告投放的医疗、死亡或创伤画面
- 容易误导为真实客户案例的表达

## 输出要求

严格输出合法 JSON，不得输出 Markdown、解释、前言、结尾或 JSON 之外的任何文字。

JSON 中不得出现注释、尾随逗号、NaN 或无法解析的内容。

输出结构：

{
  "scenes": [
    {
      "main_scene": "主场景名称",
      "description": "该主场景对应的核心需求、购买动机，以及为什么适合 Meta 广告投放",
      "sub_scenes": [
        {
          "name": "具体且可直接用于文件命名的细分场景名称",
          "audience": "该场景的核心目标用户",
          "trigger": "触发用户使用产品的具体时刻或事件",
          "pain_or_desire": "用户当前最强烈的痛点或渴望",
          "product_use": "用户提供什么素材，使用产品做什么，最终获得什么结果，结果用于什么目的",
          "score_breakdown": {
            "product_fit": 0,
            "visual_clarity": 0,
            "purchase_intent": 0,
            "attention_emotion": 0,
            "meta_safety": 0
          },
          "total_score": 0
        }
      ]
    }
  ]
}""",
    },
    "image_gen": {
        "name": "② 生图总提示词（直发生图模型）",
        "description": "场景挖掘变量+品牌名/广告语言/比例直接填入本模板，渲染后原样发给生图模型（不再经 Claude 生成提示词）；{reference_style_image} 在发送瞬间按是否带风格图填充",
        "variables": ["reference_style_image", "main_scene", "sub_scene", "audience", "trigger", "pain_or_desire", "product_use", "aspect_ratio", "ad_language", "brand_name"],
        "template": """你是一名资深 Meta 广告创意总监和商业广告海报生图专家。

你的任务是根据我提供的变量，直接设计并生成一张适合 Meta 广告投放的高质量商业海报图片。

不要输出生图 Prompt，不要解释你的设计思路，不要输出创意分析，直接完成图片生成。

## 输入变量

参考风格图：
{reference_style_image}

主场景：
{main_scene}

细分场景：
{sub_scene}

目标用户：
{audience}

触发时刻：
{trigger}

痛点/ 渴望：
{pain_or_desire}

产品使用链路和用途：
{product_use}

图片比例：
{aspect_ratio}

广告语言：
{ad_language}

品牌名称：
{brand_name}

---

## 一、核心内容原则

整张广告海报的内容必须主要围绕以下三个变量展开：

**主场景 + 细分场景 + 用户痛点**

这三个变量是决定画面内容的核心依据，包括：

- 出现什么人物或主体
- 人物正在做什么
- 发生在什么环境
- 展示什么具体场景
- 用户正在经历什么问题
- 最终应该突出什么结果

不要脱离这三个变量自行增加无关故事、场景或卖点。

用户触发动机用于辅助判断：

**为什么这个用户会被这张广告打动，以及他真正想获得什么。**

将这种动机自然融入人物状态、场景、结果和广告文案中，不要把“用户触发动机”直接写在图片上。

整张海报只表达一个核心广告概念。

不要同时塞入多个卖点。

不要做成产品功能说明页。

---

## 二、参考风格图处理

首先判断是否提供了参考风格图。

### 如果参考风格图为空、未提供或无法读取：

直接跳过参考风格分析。

不要猜测参考风格。

直接根据：

主场景 + 细分场景 + 用户痛点 + 用户触发动机

设计最适合该广告场景的专业商业视觉风格。

### 如果提供了参考风格图：

只学习参考图的**视觉设计风格**。

可以参考：

- 整体视觉调性
- 摄影风格
- 色彩关系
- 光影方式
- 构图方式
- 留白比例
- 排版方式
- 字体气质
- 信息层级
- 商业广告质感
- 画面氛围
- 设计完成度

不要参考或复制参考图中的：

- 原有文字
- 原有标题
- 原有人物身份
- 原有产品
- 原有场景内容
- 原有故事
- 原有广告卖点
- 原有品牌
- 原有 Logo
- 原有任务目标

必须严格理解：

**参考风格图只决定“这张海报应该长什么感觉”。**

**主场景、细分场景、用户痛点决定“这张海报到底要讲什么”。**

绝对不要因为参考图中出现了某种人物、产品或故事，就把这些内容带入新的广告。

---

## 三、广告创意原则

根据主场景和细分场景，设计一个非常具体、非常容易识别的广告场景。

目标用户看到图片后，应该迅速产生：

“这就是我的场景。”

“这就是我的问题。”

或者：

“这就是我想得到的结果。”

不要使用过于抽象的视觉表达。

优先展示：

**具体场景 + 明确问题/需求 + 用户想得到的结果。**

整张广告应该尽量做到：

**约 1 秒内看懂核心信息。**

不要依赖大量文字解释广告。

画面本身必须承担主要的信息传递作用。

---

## 四、主标题与副标题

海报必须包含：

**一个主标题 + 一个副标题。**

主标题和副标题不要直接复制输入变量。

必须结合：

主场景 + 细分场景 + 用户痛点 + 用户触发动机

重新撰写真正适合广告投放的文案。

文案语言使用：

{ad_language}

---

### 主标题

**主标题必须非常大、非常醒目。**

这是整张海报最重要的信息元素之一。

必须做到：

- 大字号
- 粗体或具有足够视觉重量
- 高对比
- 第一眼能够看到
- 手机屏幕上依然非常容易阅读
- 文案简短直接
- 有明确场景感或结果感
- 与核心视觉形成强烈的信息配合

主标题不能只是普通的小字说明。

不要：

- 小标题
- 细字体
- 低对比文字
- 把标题藏在角落
- 使用过长句子
- 使用空洞品牌口号
- 使用难以快速理解的抽象表达

英文主标题通常控制在：

**3–8 个单词**

中文主标题通常控制在：

**6–14 个字**

如果信息很多，优先缩短标题，而不是缩小字号。

---

### 副标题

副标题负责进一步解释：

**用户能够获得什么价值、改变或结果。**

要求：

- 简短
- 自然
- 易懂
- 与主标题形成补充
- 不重复主标题
- 明显小于主标题
- 手机端能够轻松阅读

英文副标题通常控制在：

**8–18 个单词**

中文副标题通常控制在：

**10–25 个字**

---

## 五、视觉信息层级

整张海报必须有非常明确的视觉层级。

优先级通常为：

1. **大而醒目的主标题**
2. **核心视觉主体 / 核心场景**
3. **副标题**
4. 品牌 Logo / CTA / 其他必要辅助信息

不要让：

- Logo 抢过主标题
- CTA 抢过主标题
- 副标题抢过主标题
- 装饰元素抢过主体
- 背景效果影响文字阅读
- 所有元素拥有相同视觉权重

用户快速滑动 Meta Feed 时，第一眼应该同时捕捉到：

**主标题 + 核心视觉。**

---

## 六、构图原则

根据具体广告内容自动选择最适合的表达结构。

可以使用：

- Single Hero Visual
- Hero Moment
- Before → After
- Problem → Result
- Input → Output
- Split Screen
- Lifestyle Scene
- Emotional Moment
- Product Result Showcase
- Editorial Advertising Layout

但不要机械套模板。

如果一个强烈的单场景就能讲清楚广告，不要强行加入箭头、步骤、卡片或多个画面。

如果“变化前后”本身就是核心卖点，则优先考虑 Before → After 或 Problem → Result。

所有构图选择都必须服务于：

**更快理解广告核心信息。**

---

## 七、商业设计质感

最终图片必须看起来像：

**专业广告创意团队 + 商业摄影师 + 平面设计师共同完成的正式广告。**

而不是普通 AI 生图。

要求：

- 专业商业摄影质感
- 真实自然的环境
- 高质量材质表现
- 成熟构图
- 清晰视觉重点
- 合理留白
- 专业字体排版
- 自然光影
- 高级但克制的视觉效果
- 有明显人工设计感
- 适合真实 Meta 广告投放

整体设计不要过度复杂。

---

## 八、严格避免廉价 AI 感

除非当前场景确实有必要，否则不要出现：

- 大面积蓝紫色科技渐变
- 霓虹光
- 发光粒子
- 发光线条
- 能量光束
- AI 魔法光效
- 全息屏幕
- 漂浮 UI
- 漂浮卡片
- 复杂数据面板
- 无意义箭头
- 过度镜头光晕
- 廉价 3D 图形
- 过量 Glow Effect
- 无意义科技符号
- 随机装饰元素

尤其是 AI 产品：

**不要因为产品涉及 AI，就自动设计成蓝紫色科技海报。**

产品价值应该优先通过：

**用户使用之后得到的结果**

来体现。

---

## 九、人物真实性

如果广告中出现人物：

必须呈现真实商业摄影效果。

人物需要：

- 真实自然的五官
- 正常人体比例
- 正确的手和手指
- 自然表情
- 自然动作
- 真实皮肤纹理
- 自然服装褶皱
- 真实光影
- 与环境产生合理互动
- 符合场景的情绪状态

避免：

- 塑料皮肤
- AI 标准脸
- 过度磨皮
- 僵硬站姿
- 假笑
- 夸张表情
- 错误手指
- 错误身体结构
- 过度完美的人脸
- 明显 AI 生成人物感

人物应该像真实广告摄影中的普通人或专业演员，而不是 AI 模特。

---

## 十、文字排版

主标题和副标题必须真正参与整体平面设计，而不是简单覆盖在图片上。

根据画面自动选择最合理的文字区域。

优先利用：

- 自然留白区域
- 干净背景
- 上方或左侧视觉空间
- Editorial-style copy area

禁止：

- 文字覆盖人物脸部
- 文字覆盖核心产品
- 文字紧贴边缘
- 文字严重拥挤
- 文字与背景没有足够对比度
- 大量不同字体
- 像 PPT 一样排列文字

尤其保证：

**主标题必须足够大。**

宁可减少标题字数，也不要为了塞入更多文字而缩小标题。

---

## 十一、品牌元素

如果提供品牌名称和品牌主色：

自然、克制地融入设计。

品牌主色可以作为：

- 小面积强调色
- CTA
- 局部视觉锚点
- 标签
- 少量品牌装饰

不要因为提供了品牌主色，就让整张图片全部变成该颜色。

品牌视觉应该帮助建立识别度，而不是抢夺广告核心信息。

---

## 十二、图片比例

严格按照：

{aspect_ratio}

直接为该比例设计完整构图。

不要先设计其他比例再简单裁切。

需要根据比例合理安排：

- 主视觉尺寸
- 人物位置
- 标题区域
- 副标题区域
- 留白
- 品牌元素
- 手机端阅读体验
- Meta 广告安全区域

确保最终画面不拥挤、不局促。

---

## 十三、最终生成要求

综合全部输入信息，直接生成最终 Meta 广告海报图片。

最终图片必须满足：

- 一眼能够识别主场景
- 清楚体现细分场景
- 用户痛点能够被感知
- 用户触发动机能够自然体现在广告结果或情绪中
- 只有一个明确核心广告概念
- 主标题非常大、醒目、清晰
- 副标题简短易读
- 核心视觉突出
- 信息层级明确
- 构图自然
- 不拥挤
- 有真实商业摄影感
- 有成熟人工设计感
- 没有明显廉价 AI 特效
- 适合 Meta 手机信息流快速浏览
- 严格符合 {aspect_ratio}

如果存在参考风格图，只参考其视觉设计语言，不复制任何内容。

如果参考风格图为空，则完全忽略参考图相关要求，直接完成广告设计。

**不要输出 Prompt。**
**不要输出文字解释。**
**不要输出设计分析。**
**不要给出多个方案。**
**直接生成最终广告海报图片。**""",
    },
    "copywriting": {
        "name": "④ 看图写文案",
        "description": "Claude 看生成的广告图，输出多套标题文案",
        "variables": ["product_info", "main_scene", "sub_scene", "title_count"],
        "template": """你是一位 Meta 广告投放的资深中文/英文双语文案。请仔细观察这张广告图片，结合产品信息，为它写 {title_count} 套可直接投放的广告文案。

产品信息：
{product_info}

这张图对应的场景：{main_scene} - {sub_scene}

要求：
1. 每套文案包含：headline（标题，英文，40 字符以内，有钩子）和 primary_text（主文案，英文，1-3 句，口语化、有场景代入感，可带 1-2 个 emoji，结尾带行动号召）
2. {title_count} 套文案的角度要彼此不同（如：痛点切入 / 利益点切入 / 场景代入 / 社会认同 / 限时促销）
3. 文案必须与图片画面内容呼应，让用户觉得图文一体
4. 符合 Meta 广告政策：不夸大疗效、不使用"你"开头的指向性歧视表述、不用绝对化用语

严格按以下 JSON 格式输出，不要输出 JSON 以外的任何内容：
{
  "copies": [
    {"angle": "文案角度（中文标注）", "headline": "英文标题", "primary_text": "英文主文案"}
  ]
}""",
    },
    "ratio_adapt": {
        "name": "分支 · 尺寸改版",
        "description": "双尺寸时把 4:5 母版图改成其他比例（直接发给生图模型，内容保持不变）",
        "variables": ["target_ratio"],
        "template": """Recompose this exact image into a {target_ratio} aspect ratio while keeping the content identical: the same subject, the same product, the same people, the same background, the same lighting, colors and style. Do not add or remove any elements. Only adjust the framing and composition so the scene fits the {target_ratio} format naturally. Absolutely no text, no letters, no watermark, no logo overlays, no borders in the image.""",
    },
    "refine_text": {
        "name": "分支 · 结果修改（对话）",
        "description": "各环节文字结果（场景/提示词/文案）按用户修改意见迭代修订",
        "variables": ["task_context", "current_output", "history", "feedback"],
        "template": """你是 Meta 广告素材工作流中的修改助手。用户对某一步的 AI 产出不满意，请根据用户的修改意见修订结果。

当前环节：{task_context}

当前结果（JSON）：
{current_output}

此前几轮的用户修改意见（可能为空，仅供理解上下文，当前结果已包含这些修改）：
{history}

用户本轮修改意见：
{feedback}

要求：
1. 只按本轮意见修改，用户没提到的部分保持原样
2. 修改后的内容必须保持与「当前结果」完全相同的 JSON 结构和字段名
3. 严格按以下 JSON 格式输出，不要输出 JSON 以外的任何内容：
{
  "result": <修改后的完整结果，结构与「当前结果」完全一致>
}""",
    },
    "image_refine": {
        "name": "分支 · 图片修改（对话）",
        "description": "按用户修改意见在原图基础上编辑图片（直接发给生图模型）",
        "variables": ["feedback"],
        "template": """Edit this image according to the following instruction, while keeping everything else (subject, product, composition, lighting, style, colors) unchanged. The instruction may be written in Chinese:

{feedback}

Absolutely no text, no letters, no watermark, no logo overlays, no borders in the image.""",
    },
}


def load_prompts() -> dict:
    prompts = json.loads(json.dumps(DEFAULT_PROMPTS, ensure_ascii=False))
    if PROMPTS_PATH.exists():
        try:
            saved = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            saved = {}
        for key, item in saved.items():
            if key in prompts and isinstance(item, dict) and item.get("template"):
                prompts[key]["template"] = item["template"]
    return prompts


def save_prompts(prompts: dict) -> None:
    PROMPTS_PATH.write_text(
        json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def render(template: str, variables: dict) -> str:
    """把模板中的 {key} 逐个替换为对应值。"""
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", str(value))
    return result

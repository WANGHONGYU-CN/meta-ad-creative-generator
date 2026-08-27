"""4 套提示词模板的默认值、读写与变量渲染。

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
        "description": "根据产品信息挖掘主场景和细分场景",
        "variables": ["product_info"],
        "template": """你是一位资深的 Meta（Facebook/Instagram）广告投放策略专家，擅长为产品挖掘高转化的广告投放场景。

产品信息：
{product_info}

请基于这个产品，挖掘适合 Meta 信息流广告的使用/营销场景。要求：
1. 输出 3-5 个主场景（如：家庭日常、户外运动、职场通勤、节日送礼等，结合产品实际来定）
2. 每个主场景下给出 2-4 个具体的细分场景，细分场景要具体到可以直接画出一张广告图的程度
3. 场景要贴近目标用户的真实生活痛点或渴望，有代入感，利于点击和转化

严格按以下 JSON 格式输出，不要输出 JSON 以外的任何内容：
{
  "scenes": [
    {
      "main_scene": "主场景名称",
      "description": "该主场景的一句话说明（为什么适合投放）",
      "sub_scenes": [
        {"name": "细分场景名称", "description": "具体画面的一句话描述"}
      ]
    }
  ]
}""",
    },
    "image_prompt_gen": {
        "name": "② 生图提示词生成",
        "description": "根据主场景+细分场景生成生图提示词",
        "variables": ["product_info", "main_scene", "sub_scene", "sub_scene_desc", "ratio"],
        "template": """你是一位专业的 AI 生图提示词工程师，服务于 Meta 广告素材团队。请根据下面的信息，写一条用于 AI 生图的英文提示词。

产品信息：
{product_info}

主场景：{main_scene}
细分场景：{sub_scene}（{sub_scene_desc}）
图片比例：{ratio}

要求：
1. 提示词用英文撰写，描述一张真实感强的商业广告摄影图
2. 明确写出：场景环境、人物（如需要）、产品在画面中的位置与使用方式、光线氛围、镜头视角
3. 构图要针对 {ratio} 比例优化（1:1 方图居中构图突出主体；4:5 竖图利用纵向空间，主体偏中上，适合手机信息流）
4. 产品必须是画面的视觉焦点之一，且与上传的产品参考图保持一致
5. 画面中不要出现任何文字、水印、logo 贴片

严格按以下 JSON 格式输出，不要输出 JSON 以外的任何内容：
{
  "image_prompt": "英文生图提示词"
}""",
    },
    "image_style_template": {
        "name": "③ 生图风格模板",
        "description": "拼接在每条生图提示词外层的固定风格/质量要求（直接发给生图模型）",
        "variables": ["image_prompt"],
        "template": """{image_prompt}

Style: photorealistic commercial advertising photography, natural soft lighting, high detail, realistic skin and material textures, shallow depth of field, shot on a professional full-frame camera. The product must look exactly like the reference product image provided. Absolutely no text, no letters, no watermark, no logo overlays, no borders in the image.""",
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

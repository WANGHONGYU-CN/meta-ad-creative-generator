"""Claude 调用封装：文本对话与看图，统一返回解析后的 JSON。"""
import base64
import json
import re

import anthropic


def get_client(config: dict) -> anthropic.Anthropic:
    kwargs = {"api_key": config["anthropic_api_key"], "max_retries": 3}
    if config.get("anthropic_base_url"):
        kwargs["base_url"] = config["anthropic_base_url"]
    return anthropic.Anthropic(**kwargs)


def _extract_json(text: str) -> dict:
    """从模型回复中提取 JSON 对象，容忍代码块围栏和前后多余文字。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError(f"模型未返回有效 JSON，原始回复：\n{text[:2000]}")


def call_json(config: dict, prompt_text: str, images: list | None = None) -> dict:
    """调用 Claude 并解析 JSON 回复。

    images: 可选，[(bytes, mime_type), ...]，用于看图场景。
    """
    client = get_client(config)
    content = []
    for data, mime in images or []:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.standard_b64encode(data).decode("utf-8"),
                },
            }
        )
    content.append({"type": "text", "text": prompt_text})

    response = client.messages.create(
        model=config["claude_model"],
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        detail = ""
        if response.stop_details and response.stop_details.explanation:
            detail = f"：{response.stop_details.explanation}"
        raise RuntimeError(f"模型拒绝了本次请求{detail}")

    text = "".join(b.text for b in response.content if b.type == "text")
    return _extract_json(text)


def list_models(config: dict) -> list:
    """拉取可用的 Claude 模型列表，返回模型 id 列表（按名称排序）。

    优先走 Anthropic 格式的 /v1/models；部分中转站只实现了 OpenAI 格式，则兜底。
    """
    try:
        client = get_client(config)
        models = client.with_options(timeout=30.0).models.list()
        return sorted({m.id for m in models})
    except Exception:  # noqa: BLE001 - 换 OpenAI 兼容格式再试
        from openai import OpenAI

        base = (config.get("anthropic_base_url") or "https://api.anthropic.com").rstrip("/")
        client = OpenAI(
            api_key=config["anthropic_api_key"],
            base_url=base + "/v1",
            max_retries=0,
            timeout=30.0,
            default_headers={"User-Agent": "curl/8.5.0"},
        )
        return sorted({m.id for m in client.models.list()})


def test_connection(config: dict) -> str:
    """发一次最小请求验证 key/模型可用，返回模型回复。"""
    client = get_client(config)
    response = client.with_options(timeout=30.0).messages.create(
        model=config["claude_model"],
        max_tokens=64,
        messages=[{"role": "user", "content": "回复“连接成功”四个字。"}],
    )
    return "".join(b.text for b in response.content if b.type == "text")

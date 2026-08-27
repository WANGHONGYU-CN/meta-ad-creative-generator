"""gpt-image 生图封装：支持产品参考图（图生图）、1:1 / 4:5 两种比例。

gpt-image-1 原生尺寸只有 1024x1024 / 1024x1536 / 1536x1024，不支持 4:5。
4:5 的做法：按 1024x1536 竖版生成，再居中裁切为 1024x1280（恰好 4:5）。
"""
import base64
import io
import time

from openai import OpenAI
from PIL import Image

# 比例 -> (API 请求尺寸, 裁切目标尺寸或 None)
RATIO_SPECS = {
    "1:1": ("1024x1024", None),
    "4:5": ("1024x1536", (1024, 1280)),
}


def get_client(config: dict) -> OpenAI:
    kwargs = {"api_key": config["openai_api_key"], "max_retries": 2}
    if config.get("openai_base_url"):
        kwargs["base_url"] = config["openai_base_url"]
        # 部分中转网关的 WAF 会拦截 OpenAI SDK 默认 User-Agent，统一伪装为 curl
        kwargs["default_headers"] = {"User-Agent": "curl/8.5.0"}
    return OpenAI(**kwargs)


def _center_crop(png_bytes: bytes, target: tuple) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    tw, th = target
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    cropped = img.crop((left, top, left + tw, top + th))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def generate_image(
    config: dict,
    prompt: str,
    ratio: str,
    reference_images: list | None = None,
    retries: int = 2,
) -> bytes:
    """生成一张图，返回 PNG bytes。

    reference_images: [(filename, bytes, mime_type), ...]，提供时走图生图（images.edit）。
    """
    if ratio not in RATIO_SPECS:
        raise ValueError(f"不支持的比例：{ratio}")
    size, crop_target = RATIO_SPECS[ratio]
    client = get_client(config)

    last_error = None
    for attempt in range(retries + 1):
        try:
            if reference_images:
                result = client.images.edit(
                    model=config["image_model"],
                    image=[
                        (name, data, mime) for name, data, mime in reference_images
                    ],
                    prompt=prompt,
                    size=size,
                )
            else:
                result = client.images.generate(
                    model=config["image_model"],
                    prompt=prompt,
                    size=size,
                )
            png_bytes = base64.b64decode(result.data[0].b64_json)
            if crop_target:
                png_bytes = _center_crop(png_bytes, crop_target)
            return png_bytes
        except Exception as e:  # noqa: BLE001 - 统一重试后上抛
            last_error = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise last_error


def edit_image(
    config: dict,
    image_bytes: bytes,
    prompt: str,
    ratio: str,
    retries: int = 2,
) -> bytes:
    """以一张已有成品图为输入做编辑（尺寸改版 / 按修改意见调整），返回 PNG bytes。

    与 generate_image 的区别：输入是成品图本身而非产品参考图，用于
    「4:5 母版改 1:1」和「图片对话式修改」两个场景。
    """
    if ratio not in RATIO_SPECS:
        raise ValueError(f"不支持的比例：{ratio}")
    size, crop_target = RATIO_SPECS[ratio]
    client = get_client(config)

    last_error = None
    for attempt in range(retries + 1):
        try:
            result = client.images.edit(
                model=config["image_model"],
                image=[("current.png", image_bytes, "image/png")],
                prompt=prompt,
                size=size,
            )
            png_bytes = base64.b64decode(result.data[0].b64_json)
            if crop_target:
                png_bytes = _center_crop(png_bytes, crop_target)
            return png_bytes
        except Exception as e:  # noqa: BLE001 - 统一重试后上抛
            last_error = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise last_error


# 用这些关键词从网关全量模型里筛出生图模型
IMAGE_MODEL_KEYWORDS = ("image", "dall", "seedream", "flux", "banana", "imagen")


def list_models(config: dict, only_image: bool = True) -> list:
    """拉取网关模型列表。only_image=True 时只保留生图类模型。"""
    client = get_client(config)
    models = client.with_options(timeout=30.0).models.list()
    ids = sorted({m.id for m in models})
    if only_image:
        ids = [m for m in ids if any(k in m.lower() for k in IMAGE_MODEL_KEYWORDS)]
    return ids


def test_connection(config: dict) -> bool:
    """验证 OpenAI key 可用（列模型即可，不花生图费用）。"""
    client = get_client(config)
    client.with_options(timeout=30.0).models.list()
    return True

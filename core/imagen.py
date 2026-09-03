"""gpt-image 生图封装：支持产品参考图（图生图）、1:1 / 4:5 两种比例。

4:5 的实现按模型分两条路（`ratio_spec()`，2026-09-03 适配）：
- gpt-image-2 系：支持任意 16 倍数自定义分辨率（比例 1:3~3:1），直接按
  1024x1280 原生生成，不裁切——模型构图时知道真实画幅，海报文字不会被切掉；
- 其它模型（gpt-image-1 等）：原生只有 1024x1024 / 1024x1536 / 1536x1024，
  按 1024x1536 竖版生成后居中裁切为 1024x1280（恰好 4:5），画面上下各损失
  128px，提示词需自带安全区约束。
"""
import base64
import io
import time

from openai import OpenAI
from PIL import Image

from core.logger import get_logger

log = get_logger("imagen")

# 比例 -> (API 请求尺寸, 裁切目标尺寸或 None)——不支持自定义分辨率的模型走这套
RATIO_SPECS = {
    "1:1": ("1024x1024", None),
    "4:5": ("1024x1536", (1024, 1280)),
}

# 模型名含这些关键字 = 支持任意自定义分辨率（16 的倍数、比例 1:3~3:1），4:5 原生出图
CUSTOM_SIZE_MODELS = ("gpt-image-2",)
NATIVE_45_SPEC = ("1024x1280", None)


def ratio_spec(model: str, ratio: str) -> tuple:
    """按模型返回 (API 请求尺寸, 裁切目标或 None)。未知比例抛 ValueError。"""
    if ratio not in RATIO_SPECS:
        raise ValueError(f"不支持的比例：{ratio}")
    if ratio == "4:5" and any(k in (model or "").lower() for k in CUSTOM_SIZE_MODELS):
        return NATIVE_45_SPEC
    return RATIO_SPECS[ratio]


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
    size, crop_target = ratio_spec(config["image_model"], ratio)
    client = get_client(config)

    mode = "图生图" if reference_images else "文生图"
    t0 = time.monotonic()
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
            log.info("%s成功 model=%s ratio=%s 耗时=%.1fs 重试=%d",
                     mode, config["image_model"], ratio, time.monotonic() - t0, attempt)
            return png_bytes
        except Exception as e:  # noqa: BLE001 - 统一重试后上抛
            last_error = e
            if attempt < retries:
                log.warning("%s失败将重试 attempt=%d/%d model=%s ratio=%s: %r",
                            mode, attempt + 1, retries, config["image_model"], ratio, e)
                time.sleep(3 * (attempt + 1))
    log.error("%s最终失败 model=%s ratio=%s 耗时=%.1fs: %r",
              mode, config["image_model"], ratio, time.monotonic() - t0, last_error)
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
    size, crop_target = ratio_spec(config["image_model"], ratio)
    client = get_client(config)

    t0 = time.monotonic()
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
            log.info("成品图编辑成功 model=%s ratio=%s 耗时=%.1fs 重试=%d",
                     config["image_model"], ratio, time.monotonic() - t0, attempt)
            return png_bytes
        except Exception as e:  # noqa: BLE001 - 统一重试后上抛
            last_error = e
            if attempt < retries:
                log.warning("成品图编辑失败将重试 attempt=%d/%d model=%s ratio=%s: %r",
                            attempt + 1, retries, config["image_model"], ratio, e)
                time.sleep(3 * (attempt + 1))
    log.error("成品图编辑最终失败 model=%s ratio=%s 耗时=%.1fs: %r",
              config["image_model"], ratio, time.monotonic() - t0, last_error)
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

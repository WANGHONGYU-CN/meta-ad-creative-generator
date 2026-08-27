"""读写 config.json：API key、模型名、base_url。

key 优先从环境变量读取（ANTHROPIC_API_KEY / OPENAI_API_KEY 等），
config.json 里对应字段留空即可；config.json 里填了值则以 config.json 为准。
"""
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

DEFAULT_CONFIG = {
    "anthropic_api_key": "",
    "anthropic_base_url": "",
    "claude_model": "claude-opus-5",
    "openai_api_key": "",
    "openai_base_url": "",
    "image_model": "gpt-image-1",
    "image_concurrency": 2,
}

# config.json 中留空的字段，从这些环境变量兜底
ENV_FALLBACK = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_base_url": "ANTHROPIC_BASE_URL",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_base_url": "OPENAI_BASE_URL",
}


def load_config(env_fallback: bool = True) -> dict:
    """env_fallback=False 时返回 config.json 原始值（供设置页编辑，不混入环境变量）。"""
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    if env_fallback:
        for field, env_name in ENV_FALLBACK.items():
            if not merged[field]:
                merged[field] = os.environ.get(env_name, "")
    return merged


def save_config(config: dict) -> None:
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )

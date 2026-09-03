"""设置接口：config.json 读写、模型列表拉取、连通性测试（对应 Streamlit 设置页）。

与 Streamlit 版同一纪律（决策 4）：GET/PUT 操作的是 config.json 原始值
（env_fallback=False），环境变量只回报状态，避免 env 里的 key 被落盘。
"""
import os

from fastapi import APIRouter

from core import imagen, llm
from core.config import ENV_FALLBACK, load_config, save_config

from server.schemas import ConfigPut

router = APIRouter(prefix="/api/settings", tags=["settings"])

_MASK = "•••"


def _raw_config_masked() -> dict:
    """原始 config.json，key 字段打码返回（是否已填只回报布尔，不回传明文）。"""
    raw = load_config(env_fallback=False)
    return {
        **raw,
        "anthropic_api_key": _MASK if raw["anthropic_api_key"] else "",
        "openai_api_key": _MASK if raw["openai_api_key"] else "",
    }


@router.get("")
def get_settings():
    effective = load_config()
    return {
        "config": _raw_config_masked(),
        "env": {name: bool(os.environ.get(name)) for name in ENV_FALLBACK.values()},
        "effective_ready": {
            "anthropic": bool(effective["anthropic_api_key"]),
            "image": bool(effective["openai_api_key"]),
        },
    }


@router.put("")
def put_settings(body: ConfigPut):
    """保存设置。key 字段传打码占位值（•••）表示保持不变，传空串表示清空。"""
    current = load_config(env_fallback=False)
    incoming = body.model_dump()
    for key_field in ("anthropic_api_key", "openai_api_key"):
        if incoming[key_field] == _MASK:
            incoming[key_field] = current[key_field]
        else:
            incoming[key_field] = incoming[key_field].strip()
    for f in ("anthropic_base_url", "claude_model", "openai_base_url", "image_model"):
        incoming[f] = incoming[f].strip()
    save_config(incoming)
    return get_settings()


@router.get("/models")
def list_models():
    cfg = load_config()
    out = {"llm": [], "image": [], "errors": {}}
    try:
        out["llm"] = llm.list_models(cfg)
    except Exception as e:  # noqa: BLE001 —— 拉取失败前端可手动输入
        out["errors"]["llm"] = str(e)
    try:
        out["image"] = imagen.list_models(cfg)
    except Exception as e:  # noqa: BLE001
        out["errors"]["image"] = str(e)
    return out


@router.post("/test")
def test_connection():
    cfg = load_config()
    result = {}
    try:
        result["claude"] = {"ok": True, "message": llm.test_connection(cfg)}
    except Exception as e:  # noqa: BLE001
        result["claude"] = {"ok": False, "message": str(e)}
    try:
        imagen.test_connection(cfg)
        result["image"] = {"ok": True, "message": "生图接口连接成功"}
    except Exception as e:  # noqa: BLE001
        result["image"] = {"ok": False, "message": str(e)}
    return result

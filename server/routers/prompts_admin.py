"""提示词管理接口：6 套提示词在线编辑 / 恢复默认（存储在 prompt_templates / prompt_defaults 表）。"""
from fastapi import APIRouter, HTTPException

from core.prompts import load_prompts, reset_prompt as reset_prompt_row, save_prompts

from server.schemas import PromptPut

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

MAIN_KEYS = ["scene_mining", "image_gen", "copywriting"]


@router.get("")
def get_prompts():
    prompts = load_prompts()
    return {
        "main_keys": MAIN_KEYS,
        "branch_keys": [k for k in prompts if k not in MAIN_KEYS],
        "prompts": prompts,
    }


def _key_or_404(key: str) -> dict:
    prompts = load_prompts()
    if key not in prompts:
        raise HTTPException(404, f"提示词不存在：{key}")
    return prompts


@router.put("/{key}")
def save_prompt(key: str, body: PromptPut):
    prompts = _key_or_404(key)
    prompts[key]["template"] = body.template
    save_prompts(prompts)
    return {"key": key, "prompt": prompts[key]}


@router.post("/{key}/reset")
def reset_prompt(key: str):
    _key_or_404(key)
    restored = reset_prompt_row(key)
    if restored is None:
        raise HTTPException(400, f"该提示词没有出厂默认值：{key}")
    return {"key": key, "prompt": restored}

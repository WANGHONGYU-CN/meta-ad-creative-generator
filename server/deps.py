"""FastAPI 路由公共件：run 名校验、state bundle 读取、响应里的文件 URL 拼装。"""
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from core import tasks as bg
from core.config import OUTPUTS_DIR

from server.services import mining
from server.services import state_store as st

OUTPUTS_URL = "/files/outputs"
ASSETS_URL = "/files/assets"


def get_run_dir(run: str) -> Path:
    """run 对外标识 = 目录名 run_{id}；校验格式 + 库内存在，目录缺失自动补建。"""
    rid = st.parse_run_name(run)
    if rid is None:
        raise HTTPException(400, "非法任务名")
    if not st.run_exists(rid):
        raise HTTPException(404, f"任务不存在：{run}")
    run_dir = OUTPUTS_DIR / run
    (run_dir / "images").mkdir(parents=True, exist_ok=True)
    return run_dir


def load_state(run_dir: Path) -> dict:
    state = st.load(run_dir)
    if state is None:
        raise HTTPException(404, f"任务 {Path(run_dir).name} 不存在，无法载入")
    return state


def _url(run: str, rel: str) -> str:
    return f"{OUTPUTS_URL}/{quote(run)}/{quote(rel)}"


def enrich_state(run: str, state: dict) -> dict:
    """在 state 副本上补充前端可直接用的文件 URL（不入库、不改交换格式字段）。
    image_url 带 ?v= 缓存戳（rev + 版本位置），图片变更后 URL 变化即刷新。"""
    run_dir = OUTPUTS_DIR / run
    out = dict(state)
    jobs = []
    for job in state.get("jobs", []):
        j = dict(job)
        fname = j.get("filename", "")
        if j.get("image_path") and fname and (run_dir / "images" / fname).exists():
            j["image_url"] = _url(run, f"images/{fname}") + f"?v={j.get('rev', 0)}-{j.get('hist_idx', 0)}"
        else:
            j["image_url"] = ""
        j["hist_urls"] = [_url(run, rel) for rel in j.get("hist") or []]
        jobs.append(j)
    out["jobs"] = jobs
    for attr in ("ref_images", "style_images", "logo_images"):
        out[attr + "_urls"] = [
            _url(run, rel) for rel in state.get(attr) or [] if (run_dir / rel).exists()
        ]
    return out


def combined_status(run: str) -> dict:
    """一次轮询拿全：管线状态 + 各图片修改状态 + 场景挖掘状态。"""
    return {
        "pipeline": bg.status(run),
        "edits": {str(i): s for i, s in bg.edit_status(run).items()},
        "mining": mining.status(run),
        "busy": bg.is_busy(run) or mining.is_running(run),
    }

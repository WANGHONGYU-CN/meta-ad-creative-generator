"""FastAPI 路由公共件：run 目录校验、state 读取、响应里的文件 URL 拼装。"""
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from core import runstate
from core import tasks as bg
from core.config import OUTPUTS_DIR

from server.services import mining

# run 目录名形如 "2026-09-01_120000_产品名"，禁止路径穿越字符
_RUN_NAME_RE = re.compile(r"^[^/\\]+$")

OUTPUTS_URL = "/files/outputs"
ASSETS_URL = "/files/assets"


def get_run_dir(run: str) -> Path:
    if not _RUN_NAME_RE.match(run) or run in (".", ".."):
        raise HTTPException(400, "非法任务名")
    run_dir = OUTPUTS_DIR / run
    if not run_dir.is_dir():
        raise HTTPException(404, f"任务不存在：{run}")
    return run_dir


def load_state(run_dir: Path) -> dict:
    state = runstate.load(run_dir) or runstate.rebuild_from_manifest(run_dir)
    if state is None:
        raise HTTPException(404, f"任务 {run_dir.name} 缺少 state.json / manifest.json，无法载入")
    return state


def _url(run: str, rel: str) -> str:
    return f"{OUTPUTS_URL}/{quote(run)}/{quote(rel)}"


def enrich_state(run: str, state: dict) -> dict:
    """在 state 副本上补充前端可直接用的文件 URL（不落盘、不改协议字段）。
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

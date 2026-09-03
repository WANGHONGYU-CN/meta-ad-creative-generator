"""任务（run）级接口：列表 / 新建 / 读取 / 字段修改 / 状态轮询 / 导出。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core import runstate, store
from core import tasks as bg

from server import deps
from server.schemas import RunCreate, RunPatch
from server.services import mining
from server.services import workflow as wf

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs():
    """全部任务（新→旧），带后台运行状态；product_info 等详情从索引库补充。"""
    from core import db

    info = {r["dir_name"]: r for r in db.list_runs()}
    out = []
    for d in runstate.list_task_dirs():
        row = info.get(d.name, {})
        out.append(
            {
                "name": d.name,
                "product_info": row.get("product_info", ""),
                "updated_at": row.get("updated_at", ""),
                "job_count": row.get("job_count", 0),
                "busy": bg.is_busy(d.name) or mining.is_running(d.name),
            }
        )
    return out


@router.post("", status_code=201)
def create_run(body: RunCreate):
    state = runstate.default_state()
    state["product_info"] = body.product_info
    state["brand_name"] = body.brand_name
    state["ad_language"] = body.ad_language
    if body.ratio_choice:
        try:
            state["ratio_choice"] = wf.normalize_ratio_choice(body.ratio_choice)
        except ValueError as e:
            raise HTTPException(400, str(e))
    else:
        state["ratio_choice"] = wf.RATIO_LABELS[0]
    if body.title_count:
        state["title_count"] = int(body.title_count)
    run_dir = store.create_run_dir(body.product_info[:20] or "新任务")
    runstate.persist(run_dir, state)
    return {"name": run_dir.name}


@router.get("/{run}")
def get_run(run: str):
    run_dir = deps.get_run_dir(run)
    state = deps.load_state(run_dir)
    return {
        "name": run,
        "state": deps.enrich_state(run, state),
        "status": deps.combined_status(run),
    }


@router.patch("/{run}")
def patch_run(run: str, body: RunPatch):
    """字段级修改（锁内读改写，与后台结果互不覆盖）。"""
    run_dir = deps.get_run_dir(run)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "没有要修改的字段")
    if "ratio_choice" in fields:
        try:
            fields["ratio_choice"] = wf.normalize_ratio_choice(fields["ratio_choice"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    if "selected_scenes" in fields:
        n = len(deps.load_state(run_dir).get("scenes", []))
        fields["selected_scenes"] = [i for i in dict.fromkeys(fields["selected_scenes"]) if 0 <= i < n]

    def mut(state):
        state.update(fields)

    state = runstate.update(run_dir, mut)
    return {"name": run, "state": deps.enrich_state(run, state)}


@router.get("/{run}/status")
def run_status(run: str):
    deps.get_run_dir(run)
    return deps.combined_status(run)


@router.post("/{run}/pipeline/ack")
def ack_pipeline(run: str):
    """客户端收割完管线完成/失败提示后调用，避免重复弹提示。"""
    deps.get_run_dir(run)
    bg.mark_consumed(run)
    return {"ok": True}


@router.post("/{run}/export")
def export_run(run: str):
    run_dir = deps.get_run_dir(run)
    try:
        result = wf.export_run(run_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    result["zip_url"] = f"/api/runs/{run}/export/zip"
    return result


@router.get("/{run}/export/zip")
def download_zip(run: str):
    run_dir = deps.get_run_dir(run)
    zip_path = Path(run_dir) / "交付包.zip"
    if not zip_path.exists():
        raise HTTPException(404, "尚未导出，请先调用导出接口")
    return FileResponse(zip_path, filename=f"{run}_交付包.zip", media_type="application/zip")

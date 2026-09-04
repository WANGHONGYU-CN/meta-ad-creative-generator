"""任务（run）级接口：列表 / 新建 / 读取 / 字段修改 / 状态轮询 / 导出。run 标识 = run_{id}。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core import tasks as bg

from server import deps
from server.schemas import RunCreate, RunPatch
from server.services import mining
from server.services import state_store as st
from server.services import workflow as wf

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs():
    """全部任务（新→旧），带后台运行状态。"""
    out = []
    for row in st.list_runs_overview():
        row["busy"] = bg.is_busy(row["name"]) or mining.is_running(row["name"])
        out.append(row)
    return out


@router.post("", status_code=201)
def create_run(body: RunCreate):
    ratio = wf.RATIO_LABELS[0]
    if body.ratio_choice:
        try:
            ratio = wf.normalize_ratio_choice(body.ratio_choice)
        except ValueError as e:
            raise HTTPException(400, str(e))
    try:
        name = st.create_run(
            body.product_id,
            brand_name=body.brand_name,
            ad_language=body.ad_language,
            ratio_choice=ratio,
            title_count=int(body.title_count or 3),
        )
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "产品不存在")
    return {"name": name}


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
    """字段级修改（锁内事务，与后台结果互不覆盖）。product_info 落在产品行。"""
    run_dir = deps.get_run_dir(run)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "没有要修改的字段")
    if "ratio_choice" in fields:
        try:
            fields["ratio_choice"] = wf.normalize_ratio_choice(fields["ratio_choice"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    state = st.patch_run_fields(run_dir, fields)
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

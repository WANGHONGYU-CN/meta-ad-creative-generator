"""历史素材 + 场景库接口（对应 Streamlit 的 history / scene_library 两页）。"""
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query

from core import db
from core.config import OUTPUTS_DIR

from server import deps
from server.schemas import InAdsPatch, SceneIds
from server.services import scene_lib as sl

router = APIRouter(prefix="/api", tags=["library"])


# ---------------------------------------------------------------- 历史素材
@router.get("/history/runs")
def history_runs(keyword: str = ""):
    return db.list_runs(keyword.strip())


@router.get("/history/runs/{run_id}/jobs")
def history_run_jobs(run_id: int):
    run_row = next((r for r in db.list_runs() if r["id"] == run_id), None)
    if not run_row:
        raise HTTPException(404, "run 不存在")
    dir_name = run_row["dir_name"]
    run_dir = OUTPUTS_DIR / dir_name
    jobs = db.get_run_jobs(run_id)
    for job in jobs:
        # image_path 存的是绝对路径；目录被挪动时兜底按文件名在 run 目录下找
        img = Path(job["image_path"]) if job["image_path"] else None
        if not (img and img.exists()):
            img = run_dir / "images" / job["filename"]
        job["image_url"] = (
            f"{deps.OUTPUTS_URL}/{quote(dir_name)}/images/{quote(job['filename'])}" if img.exists() else ""
        )
    return {"run": run_row, "jobs": jobs}


@router.post("/history/rebuild")
def rebuild_index():
    result = db.rebuild_from_outputs()
    return {"imported": result["imported"], "errors": [{"dir": d, "message": m} for d, m in result["errors"]]}


# ---------------------------------------------------------------- 场景库
@router.get("/scene-lib")
def scene_lib_list(
    keyword: str = "",
    product: str = "",
    main_scene: Annotated[list[str] | None, Query()] = None,
    score_min: int | None = None,
    score_max: int | None = None,
    has_image: bool | None = None,
    in_ads: bool | None = None,
    order: str = "score",
):
    score_range = None
    if score_min is not None or score_max is not None:
        score_range = (score_min or 0, score_max if score_max is not None else 100)
    rows = db.list_scene_lib(
        keyword=keyword.strip(),
        product=product,
        main_scenes=main_scene or None,
        score_range=score_range,
        has_image=has_image,
        in_ads=in_ads,
        order="score" if order == "score" else "time",
    )
    for r in rows:
        r["detail"] = sl.detail_dict(r)  # JSON 字符串转对象，前端直接用
    return rows


@router.get("/scene-lib/products")
def scene_lib_products():
    return db.scene_lib_products()


@router.get("/scene-lib/main-scenes")
def scene_lib_main_scenes(product: str = ""):
    return db.scene_lib_main_scenes(product)


@router.patch("/scene-lib/{scene_id}")
def patch_scene(scene_id: int, body: InAdsPatch):
    db.set_scene_in_ads(scene_id, body.in_ads)
    return {"ok": True}


@router.post("/scene-lib/delete")
def delete_scenes(body: SceneIds):
    db.delete_scene_lib(body.ids)
    return {"deleted": len(body.ids)}


@router.post("/scene-lib/create-task", status_code=201)
def create_task(body: SceneIds):
    """用选中场景新建独立生图任务（自动继承同产品参考图与品牌名/广告语言，决策 18）。"""
    try:
        name = sl.create_task_from_scenes(body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": name}

"""历史素材 + 场景库接口（PostgreSQL 版）。

历史页直接查 runs/jobs/copies 表（数据实时，无需索引重建——原 SQLite「重建索引」端点已移除）；
场景库查 scene_lib 表，产品筛选用 product_id。
"""
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from core.config import OUTPUTS_DIR

from server import deps
from server.db.models import Copy, Job, Product, Run
from server.db.session import get_session_factory
from server.schemas import InAdsPatch, SceneIds
from server.services import scene_lib_store as sls
from server.services import scene_lib as sl
from server.services import state_store as st

router = APIRouter(prefix="/api", tags=["library"])


# ---------------------------------------------------------------- 历史素材
@router.get("/history/runs")
def history_runs(keyword: str = ""):
    kw = keyword.strip()
    with get_session_factory()() as s:
        stmt = (
            select(Run, Product.info, func.count(Job.id))
            .join(Product, Product.id == Run.product_id)
            .outerjoin(Job, Job.run_id == Run.id)
            .group_by(Run.id, Product.info)
            .order_by(Run.updated_at.desc(), Run.id.desc())
        )
        if kw:
            like = f"%{kw}%"
            sub = select(Job.run_id).where(Job.main_scene.like(like) | Job.sub_scene.like(like))
            stmt = stmt.where(Product.info.like(like) | Run.id.in_(sub))
        return [
            {
                "id": r.id,
                "dir_name": st.run_name(r.id),
                "product_info": info,
                "updated_at": r.updated_at.astimezone().isoformat(timespec="seconds") if r.updated_at else "",
                "job_count": n,
            }
            for r, info, n in s.execute(stmt).all()
        ]


@router.get("/history/runs/{run_id}/jobs")
def history_run_jobs(run_id: int):
    with get_session_factory()() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "run 不存在")
        product = s.get(Product, run.product_id)
        name = st.run_name(run_id)
        run_dir = OUTPUTS_DIR / name
        jobs = s.execute(select(Job).where(Job.run_id == run_id).order_by(Job.seq)).scalars().all()
        by_id = {j.id: j for j in jobs}
        copies: dict = {}
        if jobs:
            for c in s.execute(select(Copy).where(Copy.job_id.in_(by_id)).order_by(Copy.seq)).scalars():
                copies.setdefault(c.job_id, []).append(
                    {"seq": c.seq, "angle": c.angle, "headline": c.headline, "primary_text": c.primary_text}
                )
        out = []
        for j in jobs:
            has_file = bool(j.image_rel_path) and (run_dir / "images" / j.filename).exists()
            master = by_id.get(j.derived_from_job_id) if j.derived_from_job_id else None
            out.append(
                {
                    "id": j.id,
                    "main_scene": j.main_scene,
                    "sub_scene": j.sub_scene,
                    "ratio": j.ratio,
                    "image_prompt": j.image_prompt,
                    "filename": j.filename,
                    "derived_from": master.filename if master else "",
                    "image_url": f"{deps.OUTPUTS_URL}/{quote(name)}/images/{quote(j.filename)}" if has_file else "",
                    "copies": copies.get(j.id, []),
                }
            )
        run_row = {
            "id": run_id, "dir_name": name, "product_info": product.info if product else "",
            "updated_at": run.updated_at.astimezone().isoformat(timespec="seconds") if run.updated_at else "",
            "job_count": len(jobs),
        }
        return {"run": run_row, "jobs": out}


# ---------------------------------------------------------------- 场景库
@router.get("/scene-lib")
def scene_lib_list(
    keyword: str = "",
    product_id: int | None = None,
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
    return sls.list_scene_lib(
        keyword=keyword.strip(),
        product_id=product_id,
        main_scenes=main_scene or None,
        score_range=score_range,
        has_image=has_image,
        in_ads=in_ads,
        order="score" if order == "score" else "time",
    )


@router.get("/scene-lib/products")
def scene_lib_products():
    return sls.lib_products()


@router.get("/scene-lib/main-scenes")
def scene_lib_main_scenes(product_id: int | None = None):
    return sls.lib_main_scenes(product_id)


@router.patch("/scene-lib/{scene_id}")
def patch_scene(scene_id: int, body: InAdsPatch):
    sls.set_in_ads(scene_id, body.in_ads)
    return {"ok": True}


@router.post("/scene-lib/delete")
def delete_scenes(body: SceneIds):
    sls.delete_ids(body.ids)
    return {"deleted": len(body.ids)}


@router.post("/scene-lib/create-task", status_code=201)
def create_task(body: SceneIds):
    """用选中场景新建独立生图任务（自动继承同产品参考图与品牌名/广告语言，决策 18）。"""
    try:
        name = sl.create_task_from_scenes(body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": name}

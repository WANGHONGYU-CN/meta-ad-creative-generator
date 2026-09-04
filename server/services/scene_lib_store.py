"""场景分类库（scene_lib 表，PostgreSQL 版）：取代 core/db.py 的 SQLite 场景库函数。

语义沿用（决策 14）：唯一键（产品, 主场景, 细分场景），重复入库更新内容但保留
has_image / in_ads 标签；入库失败不得中断素材生产（*_safe 版本吞异常记日志）。
改进：excluded_scenes 按 product_id 精确取，不再靠 product_info 全文全等匹配。
"""
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.logger import get_logger
from server.db.models import Product, Run, SceneLibEntry
from server.db.session import get_session_factory

log = get_logger("scene_lib_store")

_TEXT_FIELDS = ("audience", "trigger", "pain_or_desire", "product_use")
_SCORE_FIELDS = ("product_fit", "visual_clarity", "purchase_intent", "attention_emotion", "meta_safety")


def _session():
    return get_session_factory()()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def upsert_scenes(product_id: int, source_run_id: int | None, rows: list) -> None:
    """场景挖掘/对话修改结果入库（唯一键冲突时更新内容、保留标签）。"""
    with _session() as s:
        for row in rows:
            d = row.get("detail") or {}
            scores = d.get("score_breakdown") or {}
            values = {
                "product_id": product_id,
                "main_scene": str(row.get("main_scene", "")),
                "sub_scene": str(row.get("sub_scene", "")),
                "description": str(row.get("description", "")),
                **{k: str(d.get(k, "") or "") for k in _TEXT_FIELDS},
                **{k: _int(scores.get(k)) for k in _SCORE_FIELDS},
                "total_score": _int(d.get("total_score")),
                "source_run_id": source_run_id,
            }
            stmt = pg_insert(SceneLibEntry).values(**values)
            update_cols = {k: stmt.excluded[k] for k in values if k != "product_id"}
            update_cols["updated_at"] = func.now()
            s.execute(stmt.on_conflict_do_update(
                index_elements=["product_id", "main_scene", "sub_scene"], set_=update_cols))
        s.commit()


def upsert_scenes_safe(product_id: int, source_run_id: int | None, rows: list) -> str:
    try:
        upsert_scenes(product_id, source_run_id, rows)
        return ""
    except Exception as exc:  # noqa: BLE001 —— 入库失败不得中断素材生产
        log.error("场景库入库失败 product=%s run=%s: %r", product_id, source_run_id, exc)
        return f"{type(exc).__name__}: {exc}"


def excluded_scene_names(product_id: int) -> list:
    with _session() as s:
        return list(s.execute(
            select(SceneLibEntry.sub_scene).where(SceneLibEntry.product_id == product_id)
            .order_by(SceneLibEntry.id)
        ).scalars())


def mark_scene_has_image(product_id: int, main_scene: str, sub_scene: str) -> None:
    """出图成功自动打标；场景不在库中静默跳过，失败只记日志（不影响生图）。"""
    try:
        with _session() as s:
            row = s.execute(select(SceneLibEntry).where(
                SceneLibEntry.product_id == product_id,
                SceneLibEntry.main_scene == main_scene,
                SceneLibEntry.sub_scene == sub_scene,
            )).scalar_one_or_none()
            if row is not None:
                row.has_image = True
                s.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("场景出图打标失败 %s/%s: %r", main_scene, sub_scene, exc)


def _row_dict(r: SceneLibEntry, product_info: str, source_run: str) -> dict:
    detail = {k: getattr(r, k) for k in _TEXT_FIELDS if getattr(r, k)}
    scores = {k: getattr(r, k) for k in _SCORE_FIELDS if getattr(r, k) is not None}
    if scores:
        detail["score_breakdown"] = scores
    if r.total_score is not None:
        detail["total_score"] = r.total_score
    return {
        "id": r.id,
        "product_id": r.product_id,
        "product_info": product_info,
        "main_scene": r.main_scene,
        "sub_scene": r.sub_scene,
        "description": r.description,
        "detail": detail,
        "total_score": r.total_score,
        "has_image": r.has_image,
        "in_ads": r.in_ads,
        "source_run": source_run,
        "created_at": r.created_at.astimezone().isoformat(timespec="seconds") if r.created_at else "",
    }


def list_scene_lib(
    keyword: str = "",
    product_id: int | None = None,
    main_scenes: list | None = None,
    score_range: tuple | None = None,
    has_image=None,
    in_ads=None,
    order: str = "score",
) -> list:
    """筛选查询。score_range=(lo, hi) 时排除无分数的老数据；tri-state 参数 None=不筛。"""
    stmt = (
        select(SceneLibEntry, Product.info, Run.id)
        .join(Product, Product.id == SceneLibEntry.product_id)
        .outerjoin(Run, Run.id == SceneLibEntry.source_run_id)
    )
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            SceneLibEntry.sub_scene.like(like)
            | SceneLibEntry.main_scene.like(like)
            | SceneLibEntry.description.like(like)
            | SceneLibEntry.audience.like(like)
            | SceneLibEntry.trigger.like(like)
            | SceneLibEntry.pain_or_desire.like(like)
            | SceneLibEntry.product_use.like(like)
        )
    if product_id is not None:
        stmt = stmt.where(SceneLibEntry.product_id == product_id)
    if main_scenes:
        stmt = stmt.where(SceneLibEntry.main_scene.in_(main_scenes))
    if score_range:
        stmt = stmt.where(
            SceneLibEntry.total_score.isnot(None),
            SceneLibEntry.total_score.between(score_range[0], score_range[1]),
        )
    if has_image is not None:
        stmt = stmt.where(SceneLibEntry.has_image.is_(bool(has_image)))
    if in_ads is not None:
        stmt = stmt.where(SceneLibEntry.in_ads.is_(bool(in_ads)))
    if order == "score":
        stmt = stmt.order_by(SceneLibEntry.total_score.desc().nulls_last(),
                             SceneLibEntry.main_scene, SceneLibEntry.id.desc())
    else:
        stmt = stmt.order_by(SceneLibEntry.id.desc())
    with _session() as s:
        return [
            _row_dict(r, info, f"run_{src_id}" if src_id else "")
            for r, info, src_id in s.execute(stmt).all()
        ]


def get_scenes_by_ids(ids: list) -> list:
    with _session() as s:
        rows = s.execute(
            select(SceneLibEntry, Product.info)
            .join(Product, Product.id == SceneLibEntry.product_id)
            .where(SceneLibEntry.id.in_(ids))
        ).all()
        return [_row_dict(r, info, "") for r, info in rows]


def lib_products() -> list:
    """场景库涉及的产品（去重），供筛选下拉。"""
    with _session() as s:
        rows = s.execute(
            select(Product).join(SceneLibEntry, SceneLibEntry.product_id == Product.id)
            .distinct().order_by(Product.name)
        ).scalars().all()
        return [{"id": p.id, "name": p.name, "info": p.info} for p in rows]


def lib_main_scenes(product_id: int | None = None) -> list:
    with _session() as s:
        stmt = select(SceneLibEntry.main_scene).distinct().order_by(SceneLibEntry.main_scene)
        if product_id is not None:
            stmt = stmt.where(SceneLibEntry.product_id == product_id)
        return list(s.execute(stmt).scalars())


def set_in_ads(scene_id: int, value: bool) -> None:
    with _session() as s:
        row = s.get(SceneLibEntry, scene_id)
        if row is not None:
            row.in_ads = bool(value)
            s.commit()


def delete_ids(ids: list) -> None:
    if not ids:
        return
    with _session() as s:
        s.execute(delete(SceneLibEntry).where(SceneLibEntry.id.in_(ids)))
        s.commit()

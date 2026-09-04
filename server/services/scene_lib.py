"""场景库：勾选场景创建独立生图任务（PostgreSQL 版）。

继承语义（决策 18）：新任务自动带同产品最近任务的 风格图/Logo 副本，
品牌名/广告语言取同产品最近填过的任务（取不到回退产品默认值）。
"""
from server.services import scene_lib_store
from server.services import state_store as st


def create_task_from_scenes(scene_ids: list) -> str:
    """用场景库里选中的场景新建独立生图任务，返回新任务名（run_{id}）。
    所选场景必须属于同一产品。"""
    if not scene_ids:
        raise ValueError("未选择任何场景")
    rows = scene_lib_store.get_scenes_by_ids(list(set(scene_ids)))
    if len(rows) != len(set(scene_ids)):
        raise ValueError("部分场景不存在（可能已被删除），请刷新后重试")
    product_ids = {r["product_id"] for r in rows}
    if len(product_ids) > 1:
        raise ValueError("所选场景属于不同产品，请只勾选同一产品的场景")
    product_id = product_ids.pop()

    scenes = [
        {
            "main_scene": r["main_scene"],
            "sub_scene": r["sub_scene"],
            "description": r["description"],
            **({"detail": r["detail"]} if r.get("detail") else {}),
        }
        for r in rows
    ]

    name = st.create_run(product_id)
    run_dir = st.run_dir_of(st.parse_run_name(name))
    rid = st.parse_run_name(name)

    # 品牌名/广告语言：同产品最近任务优先，取不到保持产品默认值（create_run 已带入）
    inherited = st.latest_run_brand(product_id, exclude_run_id=rid)
    if inherited:
        st.patch_run_fields(run_dir, inherited)

    # 场景写入并全选，进工作台可直接点生图
    st.replace_scenes(run_dir, scenes, select_all=True)

    # 参考图副本继承
    refs = st.latest_run_refs(product_id, exclude_run_id=rid)
    for kind in ("style", "logo"):
        if refs.get(kind):
            st.set_run_refs(run_dir, kind, refs[kind])
    return name

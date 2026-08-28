"""场景分类库：历史挖掘场景的汇总、筛选、打标、删除，以及勾选场景创建新生图任务。

- 数据来源：scene_lib 表（场景挖掘成功时自动入库）
- 标签：has_image（出图成功时系统自动打）、in_ads（是否在投放，本页手动勾选）
- 「用选中场景创建生图任务」：新建独立 run（多任务架构），不影响任何正在跑的任务
"""
import json

import streamlit as st

from core import db, runstate, store

st.title("🗃 场景库")

# ---------------------------------------------------------------- 筛选器
with st.container(border=True):
    row1 = st.columns([2, 2, 2])
    keyword = row1[0].text_input("关键词", placeholder="场景名 / 描述 / 画面 brief…")
    products = db.scene_lib_products()
    product = row1[1].selectbox("产品", ["（全部）"] + products)
    product = "" if product == "（全部）" else product
    main_scenes = row1[2].multiselect("主场景分类", db.scene_lib_main_scenes(product))

    row2 = st.columns([2, 2, 2])
    use_score = row2[0].checkbox("按总分筛选（无分数的老场景会被过滤）")
    score_range = row2[0].slider("总分范围", 0, 100, (90, 100), disabled=not use_score)
    img_filter = row2[1].radio("出图状态", ["全部", "已出图", "未出图"], horizontal=True)
    ads_filter = row2[2].radio("投放状态", ["全部", "投放中", "未投放"], horizontal=True)
    order = st.radio("排序", ["按分数（高→低）", "按入库时间（新→旧）"], horizontal=True)

rows = db.list_scene_lib(
    keyword=keyword.strip(),
    product=product,
    main_scenes=main_scenes,
    score_range=score_range if use_score else None,
    has_image={"全部": None, "已出图": True, "未出图": False}[img_filter],
    in_ads={"全部": None, "投放中": True, "未投放": False}[ads_filter],
    order="score" if order.startswith("按分数") else "time",
)

if not rows:
    st.info("没有符合条件的场景。场景在「素材工作流」Step 1 挖掘成功后会自动入库。")
    st.stop()

st.caption(f"共 {len(rows)} 个场景。勾选后可在底部删除或创建生图任务；「投放中」勾选即保存。")


def _detail_dict(row: dict) -> dict:
    try:
        return json.loads(row["detail"]) if row["detail"] else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------- 列表（按主场景分组）
groups: dict = {}
for r in rows:
    groups.setdefault(r["main_scene"], []).append(r)

for main, items in groups.items():
    st.markdown(f"##### {main}")
    for r in items:
        cols = st.columns([0.5, 4.5, 1, 1.2, 1.2, 1.2])
        cols[0].checkbox("选择", key=f"pick_{r['id']}", label_visibility="collapsed")
        with cols[1]:
            st.markdown(f"**{r['sub_scene']}**")
            st.caption(r["description"] or "—")
        score = r["total_score"]
        cols[2].markdown(f"⭐ **{score}**" if score is not None else "（无分）")
        cols[3].markdown("🖼 已出图" if r["has_image"] else "◻ 未出图")
        in_ads_now = cols[4].checkbox("📢 投放中", value=bool(r["in_ads"]), key=f"inads_{r['id']}")
        if in_ads_now != bool(r["in_ads"]):
            db.set_scene_in_ads(r["id"], in_ads_now)
        with cols[5].popover("详情"):
            d = _detail_dict(r)
            for k, label in [
                ("audience", "目标用户"), ("trigger", "触发时刻"), ("pain_or_desire", "痛点/渴望"),
                ("product_use", "产品使用链路"), ("video_purpose", "成片用途"),
                ("visual_brief", "广告画面 brief"), ("headline_angle", "标题方向"),
            ]:
                if d.get(k):
                    st.markdown(f"**{label}**：{d[k]}")
            scores = d.get("score_breakdown") or {}
            if scores:
                st.caption(
                    f"产品匹配 {scores.get('product_fit', '-')} / 画面直观 {scores.get('visual_clarity', '-')} / "
                    f"付费意愿 {scores.get('purchase_intent', '-')} / 情绪吸引 {scores.get('attention_emotion', '-')} / "
                    f"投放安全 {scores.get('meta_safety', '-')}"
                )
            st.caption(f"来源任务：{r['source_run'] or '—'}　入库：{r['created_at'] or '—'}")
            if r["product_info"]:
                st.caption("产品：" + r["product_info"][:120] + ("…" if len(r["product_info"]) > 120 else ""))

# ---------------------------------------------------------------- 底部操作
picked = [r for r in rows if st.session_state.get(f"pick_{r['id']}")]
st.divider()
col_del, col_gen, col_info = st.columns([1.2, 1.8, 3])

with col_del:
    if st.button(f"🗑 删除选中的 {len(picked)} 个场景", disabled=not picked):
        db.delete_scene_lib([r["id"] for r in picked])
        for r in picked:
            st.session_state.pop(f"pick_{r['id']}", None)
        st.toast(f"已删除 {len(picked)} 个场景（只删库记录，不影响历史任务文件）")
        st.rerun()

with col_gen:
    if st.button(f"🎨 用选中的 {len(picked)} 个场景创建生图任务", type="primary", disabled=not picked):
        picked_products = {r["product_info"] for r in picked}
        if len(picked_products) > 1:
            st.error("所选场景属于不同产品，请只勾选同一产品的场景。")
        else:
            scenes = []
            for r in picked:
                row = {
                    "main_scene": r["main_scene"],
                    "sub_scene": r["sub_scene"],
                    "description": r["description"],
                }
                d = _detail_dict(r)
                if d:
                    row["detail"] = d
                scenes.append(row)
            product_info = picked_products.pop()
            run_dir = store.create_run_dir(product_info[:20] or "场景库任务")
            state = runstate.default_state()
            state.update(
                {
                    "product_info": product_info,
                    "scenes": scenes,
                    "selected_scenes": list(range(len(scenes))),
                    "jobs_gen": 0,
                }
            )
            runstate.persist(run_dir, state)
            st.session_state["load_run_request"] = str(run_dir)
            st.switch_page("pages_/workflow.py")

with col_info:
    if picked:
        st.caption("创建生图任务 = 新建一个独立任务并跳转到工作流（场景已选好，直接从 Step 2 开始）；不影响正在后台跑的任务。")
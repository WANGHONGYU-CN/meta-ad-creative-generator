"""历史 run 浏览页：从 SQLite 索引检索，图片按 manifest 记录的路径读取。

- 数据来源：data/app.db（索引层）；权威数据仍是 outputs/*/manifest.json
- 「重建索引」按钮 = scripts/rebuild_db.py 的页面版，库与磁盘不一致时点一下即可
"""
from pathlib import Path

import streamlit as st

from core import db
from core.config import OUTPUTS_DIR

st.title("🗂️ 历史素材")

col_search, col_rebuild = st.columns([4, 1])
keyword = col_search.text_input(
    "搜索", label_visibility="collapsed", placeholder="按产品信息 / 主场景 / 细分场景搜索…"
)
if col_rebuild.button("🔄 重建索引", help="扫描 outputs/ 全部 manifest.json 重新导入数据库"):
    result = db.rebuild_from_outputs()
    st.toast(f"已导入 {result['imported']} 个 run" + (f"，{len(result['errors'])} 个失败" if result["errors"] else ""))
    for dir_name, msg in result["errors"]:
        st.warning(f"{dir_name}: {msg}")

runs = db.list_runs(keyword.strip())

if not runs:
    st.info("暂无记录。完成一次工作流后会自动入库；已有历史 run 可点右上「重建索引」导入。")
    st.stop()

st.caption(f"共 {len(runs)} 个 run")

for run in runs:
    label = f"**{run['dir_name']}**　图片 {run['job_count']} 张"
    with st.expander(label):
        if st.button("↩️ 载入到工作流继续编辑", key=f"load_{run['id']}"):
            st.session_state["load_run_request"] = str(OUTPUTS_DIR / run["dir_name"])
            st.switch_page("pages_/workflow.py")
        if run["product_info"]:
            st.markdown("**产品信息**")
            st.text(run["product_info"][:500] + ("…" if len(run["product_info"]) > 500 else ""))
        run_dir = OUTPUTS_DIR / run["dir_name"]
        st.caption(f"目录：`{run_dir}`　更新于 {run['updated_at'] or '未知'}")

        jobs = db.get_run_jobs(run["id"])
        for job in jobs:
            st.divider()
            col_img, col_info = st.columns([1, 2])
            with col_img:
                # image_path 存的是绝对路径；目录被挪动时兜底按文件名在 run 目录下找
                img = Path(job["image_path"]) if job["image_path"] else None
                if not (img and img.exists()):
                    img = run_dir / "images" / job["filename"]
                if img.exists():
                    st.image(str(img), width="stretch")
                else:
                    st.caption("（图片文件不存在）")
            with col_info:
                title = f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）"
                if job["derived_from"]:
                    title += "　派生自母版"
                st.markdown(f"**{title}**")
                if job["image_prompt"]:
                    with st.popover("生图提示词"):
                        st.text(job["image_prompt"])
                for copy in job["copies"]:
                    st.markdown(
                        f"- **[{copy['angle']}] {copy['headline']}**　{copy['primary_text']}"
                    )

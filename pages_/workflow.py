"""主工作流页：输入 → 找场景 → 生成生图提示词 → 生图 → 看图写文案 → 导出。"""
from datetime import datetime

import streamlit as st

from core import imagen, llm, store
from core.config import load_config
from core.prompts import load_prompts, render

st.title("🎨 Meta 素材工作流")

config = load_config()
prompts = load_prompts()

if not config["anthropic_api_key"] or not config["openai_api_key"]:
    st.warning("请先到「设置」页填写 Anthropic 和 OpenAI 的 API Key。")

ss = st.session_state
ss.setdefault("scenes", [])          # [{selected, main_scene, sub_scene, description}]
ss.setdefault("jobs", [])            # 每个 job = 一张图（场景 x 尺寸）
ss.setdefault("run_dir", None)

RATIO_OPTIONS = {"1:1（方图）": ["1:1"], "4:5（竖图）": ["4:5"], "双尺寸（1:1 + 4:5）": ["1:1", "4:5"]}


def save_manifest():
    if not ss.run_dir:
        return
    manifest = {
        "product_info": ss.get("product_info", ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "jobs": [
            {k: v for k, v in job.items() if k != "image_bytes"}
            for job in ss.jobs
        ],
    }
    store.save_manifest(ss.run_dir, manifest)


# ---------------------------------------------------------------- Step 0 输入
st.header("Step 0 · 输入产品信息")
product_info = st.text_area(
    "产品信息（名称、卖点、目标人群、目标市场等，越具体场景越准）",
    height=140,
    key="product_info",
    placeholder="例：便携颈挂风扇，卖点是超静音/续航18小时/可折叠，目标人群是欧美户外通勤人群，目标市场美国…",
)
uploaded_refs = st.file_uploader(
    "产品参考图（生图时作为图生图参考，建议 1-3 张白底或实拍图）",
    type=["png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True,
)
col_ratio, col_count = st.columns(2)
with col_ratio:
    ratio_choice = st.radio("生图尺寸", list(RATIO_OPTIONS.keys()), horizontal=True)
with col_count:
    title_count = st.number_input("每张图的文案套数", min_value=1, max_value=10, value=3)
ratios = RATIO_OPTIONS[ratio_choice]

st.divider()

# ---------------------------------------------------------------- Step 1 找场景
st.header("Step 1 · 挖掘投放场景")
if st.button("🔍 AI 挖掘场景", type="primary", disabled=not product_info.strip()):
    with st.spinner("Claude 正在挖掘场景…"):
        try:
            prompt = render(prompts["scene_mining"]["template"], {"product_info": product_info})
            result = llm.call_json(config, prompt)
            rows = []
            for scene in result.get("scenes", []):
                for sub in scene.get("sub_scenes", []):
                    rows.append(
                        {
                            "selected": True,
                            "main_scene": scene.get("main_scene", ""),
                            "sub_scene": sub.get("name", ""),
                            "description": sub.get("description", ""),
                        }
                    )
            ss.scenes = rows
            ss.jobs = []
        except Exception as e:  # noqa: BLE001
            st.error(f"场景挖掘失败：{e}")

if ss.scenes:
    st.caption("勾选要生图的细分场景；表格内容可直接编辑，也可增删行。")
    ss.scenes = st.data_editor(
        ss.scenes,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "selected": st.column_config.CheckboxColumn("选用", default=True),
            "main_scene": st.column_config.TextColumn("主场景"),
            "sub_scene": st.column_config.TextColumn("细分场景"),
            "description": st.column_config.TextColumn("画面描述", width="large"),
        },
        key="scene_editor",
    )
    selected_scenes = [r for r in ss.scenes if r.get("selected") and r.get("sub_scene")]
    st.caption(f"已选 {len(selected_scenes)} 个细分场景 × {len(ratios)} 个尺寸 = 将生成 {len(selected_scenes) * len(ratios)} 张图")
else:
    selected_scenes = []

st.divider()

# ---------------------------------------------------------------- Step 2 生图提示词
st.header("Step 2 · 生成生图提示词")
if st.button("✏️ 为选中场景生成提示词", type="primary", disabled=not selected_scenes):
    jobs = []
    progress = st.progress(0.0, text="生成提示词中…")
    total = len(selected_scenes) * len(ratios)
    done = 0
    for row in selected_scenes:
        for ratio in ratios:
            done += 1
            progress.progress(done / total, text=f"生成提示词 {done}/{total}：{row['sub_scene']}（{ratio}）")
            try:
                prompt = render(
                    prompts["image_prompt_gen"]["template"],
                    {
                        "product_info": product_info,
                        "main_scene": row["main_scene"],
                        "sub_scene": row["sub_scene"],
                        "sub_scene_desc": row.get("description", ""),
                        "ratio": ratio,
                    },
                )
                result = llm.call_json(config, prompt)
                image_prompt = result.get("image_prompt", "")
            except Exception as e:  # noqa: BLE001
                st.error(f"「{row['sub_scene']}（{ratio}）」提示词生成失败：{e}")
                image_prompt = ""
            jobs.append(
                {
                    "main_scene": row["main_scene"],
                    "sub_scene": row["sub_scene"],
                    "sub_scene_desc": row.get("description", ""),
                    "ratio": ratio,
                    "image_prompt": image_prompt,
                    "filename": store.image_filename(row["main_scene"], row["sub_scene"], ratio),
                    "image_bytes": None,
                    "image_path": "",
                    "copies": [],
                }
            )
    progress.empty()
    ss.jobs = jobs

if ss.jobs:
    st.caption("生图前可直接修改每条提示词（英文），改完直接进入 Step 3。")
    for i, job in enumerate(ss.jobs):
        ss.jobs[i]["image_prompt"] = st.text_area(
            f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）",
            value=job["image_prompt"],
            height=120,
            key=f"job_prompt_{i}",
        )

st.divider()

# ---------------------------------------------------------------- Step 3 生图
st.header("Step 3 · 生图")


def run_generation(indices):
    ref_images = [(f.name, f.getvalue(), f.type or "image/png") for f in (uploaded_refs or [])]
    if not ss.run_dir:
        ss.run_dir = store.create_run_dir(product_info[:20])
    progress = st.progress(0.0, text="生图中…")
    for n, i in enumerate(indices, start=1):
        job = ss.jobs[i]
        progress.progress(n / len(indices), text=f"生图 {n}/{len(indices)}：{job['sub_scene']}（{job['ratio']}）")
        try:
            final_prompt = render(prompts["image_style_template"]["template"], {"image_prompt": job["image_prompt"]})
            png = imagen.generate_image(config, final_prompt, job["ratio"], reference_images=ref_images)
            path = store.save_image(ss.run_dir, job["filename"], png)
            ss.jobs[i]["image_bytes"] = png
            ss.jobs[i]["image_path"] = str(path)
            ss.jobs[i]["copies"] = []
        except Exception as e:  # noqa: BLE001
            st.error(f"「{job['sub_scene']}（{job['ratio']}）」生图失败：{e}")
    progress.empty()
    save_manifest()


pending = [i for i, j in enumerate(ss.jobs) if j["image_prompt"] and j["image_bytes"] is None]
col_gen, col_info = st.columns([1, 2])
with col_gen:
    if st.button(f"🖼️ 生成待生成的 {len(pending)} 张图", type="primary", disabled=not pending):
        run_generation(pending)
with col_info:
    if not uploaded_refs:
        st.info("未上传产品参考图，将走纯文生图；上传参考图可让产品与实物一致。")

done_jobs = [i for i, j in enumerate(ss.jobs) if j["image_bytes"] is not None]
if done_jobs:
    cols = st.columns(3)
    for n, i in enumerate(done_jobs):
        job = ss.jobs[i]
        with cols[n % 3]:
            st.image(job["image_bytes"], caption=f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）", use_container_width=True)
            if st.button("🔄 重新生成这张", key=f"regen_{i}"):
                run_generation([i])
                st.rerun()

st.divider()

# ---------------------------------------------------------------- Step 4 看图写文案
st.header("Step 4 · 看图写文案")
need_copy = [i for i, j in enumerate(ss.jobs) if j["image_bytes"] is not None and not j["copies"]]
if st.button(f"🗒️ 为 {len(need_copy)} 张图生成文案（每张 {title_count} 套）", type="primary", disabled=not need_copy):
    progress = st.progress(0.0, text="生成文案中…")
    for n, i in enumerate(need_copy, start=1):
        job = ss.jobs[i]
        progress.progress(n / len(need_copy), text=f"文案 {n}/{len(need_copy)}：{job['sub_scene']}（{job['ratio']}）")
        try:
            prompt = render(
                prompts["copywriting"]["template"],
                {
                    "product_info": product_info,
                    "main_scene": job["main_scene"],
                    "sub_scene": job["sub_scene"],
                    "title_count": int(title_count),
                },
            )
            result = llm.call_json(config, prompt, images=[(job["image_bytes"], "image/png")])
            ss.jobs[i]["copies"] = result.get("copies", [])
        except Exception as e:  # noqa: BLE001
            st.error(f"「{job['sub_scene']}（{job['ratio']}）」文案生成失败：{e}")
    progress.empty()
    save_manifest()

copied_jobs = [i for i, j in enumerate(ss.jobs) if j["copies"]]
for i in copied_jobs:
    job = ss.jobs[i]
    with st.container(border=True):
        col_img, col_copy = st.columns([1, 2])
        with col_img:
            if job["image_bytes"] is not None:
                st.image(job["image_bytes"], use_container_width=True)
            st.caption(f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）")
        with col_copy:
            ss.jobs[i]["copies"] = st.data_editor(
                job["copies"],
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "angle": st.column_config.TextColumn("角度"),
                    "headline": st.column_config.TextColumn("标题 Headline", width="medium"),
                    "primary_text": st.column_config.TextColumn("主文案 Primary Text", width="large"),
                },
                key=f"copies_editor_{i}",
            )

st.divider()

# ---------------------------------------------------------------- 导出
st.header("导出交付包")
exportable = [j for j in ss.jobs if j["image_bytes"] is not None]
if st.button("📦 导出（图片 + manifest + 交付表.xlsx）", type="primary", disabled=not exportable):
    save_manifest()
    xlsx_path = store.export_xlsx(ss.run_dir, exportable)
    st.success(f"已导出到：`{ss.run_dir}`")
    st.markdown(f"- 图片目录：`{ss.run_dir}/images/`\n- 绑定关系：`{ss.run_dir}/manifest.json`\n- 交付表：`{xlsx_path}`")

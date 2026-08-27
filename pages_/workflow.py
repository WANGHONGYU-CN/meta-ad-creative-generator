"""主工作流页：输入 → 找场景 → 生成生图提示词 → 生图 → 看图写文案 → 导出。

关键机制：
- 场景卡片多选：Step 1 结果以卡片渲染，点卡片勾选/取消若干细分场景进入后续步骤
- 双尺寸 = 母版派生：先生成 4:5 母版，再用「尺寸改版」提示词把成品改成 1:1（内容不变）；
  母版重新生成/被修改后，派生图自动失效待重做
- 持续对话修改：场景 / 生图提示词 / 图片 / 文案 四处均有对话框，
  入参 = 当前结果 + 用户修改意见 + 历史意见，输出替换当前结果
- 生图并发：独立图（母版）全部并行提交，不设并发上限，由 API 侧限流兜底
  （generate_image 自带重试退避）；派生图依赖母版串行
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
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
ss.setdefault("scenes", [])          # [{main_scene, sub_scene, description}]
ss.setdefault("selected_scenes", [])  # 多选的场景下标列表
ss.setdefault("jobs", [])            # 每个 job = 一张图（场景 x 尺寸）
ss.setdefault("jobs_gen", 0)         # job 批次号，用于隔离各批次的 widget/对话状态
ss.setdefault("run_dir", None)

# 双尺寸时 4:5 为母版（排前），1:1 由母版改尺寸派生
RATIO_OPTIONS = {
    "1:1（方图）": ["1:1"],
    "4:5（竖图）": ["4:5"],
    "双尺寸（先出 4:5 母版，再改尺寸出内容一致的 1:1）": ["4:5", "1:1"],
}
# manifest 不落盘的运行时字段
MANIFEST_EXCLUDE = {"image_bytes", "prev_image_bytes", "rev"}


def save_manifest():
    if not ss.run_dir:
        return
    manifest = {
        "product_info": ss.get("product_info", ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "jobs": [
            {k: v for k, v in job.items() if k not in MANIFEST_EXCLUDE}
            for job in ss.jobs
        ],
    }
    store.save_manifest(ss.run_dir, manifest)


# ---------------------------------------------------------------- 对话修改通用件
def chat_box(chat_key: str, apply_feedback, placeholder="输入修改意见，AI 会在当前结果基础上修改…"):
    """通用修改对话框：展示历史 + 输入框；apply_feedback(fb) 负责真正修改结果。"""
    history = ss.setdefault(chat_key, [])
    for msg in history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    with st.form(f"{chat_key}_form", clear_on_submit=True, border=False):
        col_in, col_btn = st.columns([5, 1])
        feedback = col_in.text_input("修改意见", label_visibility="collapsed", placeholder=placeholder)
        send = col_btn.form_submit_button("发送", use_container_width=True)
    if send and feedback.strip():
        feedback = feedback.strip()
        with st.spinner("AI 修改中…"):
            try:
                apply_feedback(feedback)
                reply = "✅ 已按意见修改"
            except Exception as e:  # noqa: BLE001
                reply = f"❌ 修改失败：{e}"
        history.append({"role": "user", "content": feedback})
        history.append({"role": "assistant", "content": reply})
        st.rerun()


def refine_via_llm(task_context: str, current_output, chat_key: str, feedback: str, images=None):
    """走「结果修改」提示词让 Claude 修订当前结果，返回修改后的内容。"""
    history_lines = [f"- {m['content']}" for m in ss.get(chat_key, []) if m["role"] == "user"]
    prompt = render(
        prompts["refine_text"]["template"],
        {
            "task_context": task_context,
            "current_output": json.dumps(current_output, ensure_ascii=False, indent=2),
            "history": "\n".join(history_lines) or "（无）",
            "feedback": feedback,
        },
    )
    result = llm.call_json(config, prompt, images=images)
    if "result" not in result:
        raise ValueError("模型未按约定返回 result 字段")
    return result["result"]


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
dual_mode = len(ratios) > 1

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
                            "main_scene": scene.get("main_scene", ""),
                            "sub_scene": sub.get("name", ""),
                            "description": sub.get("description", ""),
                        }
                    )
            ss.scenes = rows
            ss.selected_scenes = []
            ss.jobs = []
            ss.pop("chat_scenes", None)
        except Exception as e:  # noqa: BLE001
            st.error(f"场景挖掘失败：{e}")


def apply_scene_feedback(feedback: str):
    current = {
        "scenes": [
            {
                "main_scene": r.get("main_scene", ""),
                "sub_scene": r.get("sub_scene", ""),
                "description": r.get("description", ""),
            }
            for r in ss.scenes
        ]
    }
    new = refine_via_llm("场景挖掘结果（主场景 + 细分场景列表）", current, "chat_scenes", feedback)
    rows = new.get("scenes", []) if isinstance(new, dict) else new
    if not isinstance(rows, list) or not rows:
        raise ValueError("模型返回的场景列表为空或格式不对")
    ss.scenes = [
        {
            "main_scene": str(r.get("main_scene", "")),
            "sub_scene": str(r.get("sub_scene", "")),
            "description": str(r.get("description", "")),
        }
        for r in rows
        if isinstance(r, dict) and r.get("sub_scene")
    ]
    ss.selected_scenes = []


selected_rows = []
if ss.scenes:
    st.caption("点击卡片勾选/取消细分场景（可多选）；对结果不满意可在下方对话框让 AI 修改。")
    groups = {}
    for idx, row in enumerate(ss.scenes):
        groups.setdefault(row.get("main_scene", ""), []).append(idx)
    for main, indices in groups.items():
        st.markdown(f"##### {main}")
        cols = st.columns(3)
        for n, idx in enumerate(indices):
            row = ss.scenes[idx]
            picked = idx in ss.selected_scenes
            with cols[n % 3], st.container(border=True):
                st.markdown(f"**{'✅ ' if picked else ''}{row.get('sub_scene', '')}**")
                st.caption(row.get("description", "") or "—")
                if st.button(
                    "已选中（点击取消）" if picked else "选择这个场景",
                    key=f"scene_pick_{idx}",
                    type="primary" if picked else "secondary",
                    use_container_width=True,
                ):
                    if picked:
                        ss.selected_scenes = [i for i in ss.selected_scenes if i != idx]
                    else:
                        ss.selected_scenes = ss.selected_scenes + [idx]
                    st.rerun()
    with st.expander("💬 对场景结果不满意？让 AI 修改", expanded=False):
        chat_box("chat_scenes", apply_scene_feedback, placeholder="例：场景太泛了，聚焦冬季户外；把第 2 个主场景换成送礼场景…")

    selected_rows = [ss.scenes[i] for i in ss.selected_scenes if i < len(ss.scenes)]
    if selected_rows:
        extra = "（每个场景 = 4:5 母版 + 改尺寸 1:1）" if dual_mode else ""
        st.caption(
            f"已选 **{len(selected_rows)}** 个细分场景 × {len(ratios)} 个尺寸 = "
            f"将生成 {len(selected_rows) * len(ratios)} 张图{extra}"
        )
    else:
        st.caption("尚未选择场景。")

st.divider()

# ---------------------------------------------------------------- Step 2 生图提示词
st.header("Step 2 · 生成生图提示词")
g = ss.jobs_gen

if st.button("✏️ 为选中场景生成提示词", type="primary", disabled=not selected_rows):
    ss.jobs_gen = g = g + 1
    # 清理上一批 job 的对话历史
    for k in [k for k in list(ss.keys()) if k.startswith(("chat_prompt_", "chat_image_", "chat_copies_"))]:
        del ss[k]
    master_ratios = ["4:5"] if dual_mode else ratios
    jobs = []
    total = len(selected_rows) * len(master_ratios)
    progress = st.progress(0.0, text="生成提示词中…")
    done = 0
    for row in selected_rows:
        master_filename = ""
        for ratio in master_ratios:
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
            master_filename = store.image_filename(row["main_scene"], row["sub_scene"], ratio)
            jobs.append(
                {
                    "main_scene": row["main_scene"],
                    "sub_scene": row["sub_scene"],
                    "sub_scene_desc": row.get("description", ""),
                    "ratio": ratio,
                    "image_prompt": image_prompt,
                    "filename": master_filename,
                    "image_bytes": None,
                    "image_path": "",
                    "copies": [],
                    "derived_from": "",
                    "prev_image_bytes": None,
                    "rev": 0,
                }
            )
        if dual_mode and master_filename:
            jobs.append(
                {
                    "main_scene": row["main_scene"],
                    "sub_scene": row["sub_scene"],
                    "sub_scene_desc": row.get("description", ""),
                    "ratio": "1:1",
                    "image_prompt": "",  # 派生图与母版共用提示词，改尺寸时回填
                    "filename": store.image_filename(row["main_scene"], row["sub_scene"], "1:1"),
                    "image_bytes": None,
                    "image_path": "",
                    "copies": [],
                    "derived_from": master_filename,
                    "prev_image_bytes": None,
                    "rev": 0,
                }
            )
    progress.empty()
    ss.jobs = jobs


def make_prompt_feedback(i: int):
    def apply(feedback: str):
        job = ss.jobs[i]
        new = refine_via_llm(
            f"AI 生图英文提示词（场景：{job['main_scene']} / {job['sub_scene']}，比例 {job['ratio']}）",
            {"image_prompt": job["image_prompt"]},
            f"chat_prompt_{g}_{i}",
            feedback,
        )
        if isinstance(new, dict):
            new = new.get("image_prompt", "")
        if not str(new).strip():
            raise ValueError("模型返回的提示词为空")
        job["image_prompt"] = str(new).strip()
        job["rev"] = job.get("rev", 0) + 1

    return apply


if ss.jobs:
    st.caption("生图前可直接改提示词文本，或用对话框让 AI 按意见改。")
    for i, job in enumerate(ss.jobs):
        if job.get("derived_from"):
            st.info(
                f"**{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）**：由 4:5 母版成品改尺寸生成，"
                "内容与母版一致，无需单独提示词。"
            )
            continue
        ss.jobs[i]["image_prompt"] = st.text_area(
            f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）",
            value=job["image_prompt"],
            height=120,
            key=f"job_prompt_{g}_{i}_v{job.get('rev', 0)}",
        )
        with st.expander("💬 让 AI 修改这条提示词", expanded=False):
            chat_box(
                f"chat_prompt_{g}_{i}",
                make_prompt_feedback(i),
                placeholder="例：光线改成黄昏；人物换成中年男性；产品再突出一点…",
            )

st.divider()

# ---------------------------------------------------------------- Step 3 生图
st.header("Step 3 · 生图")


def _master_index(job: dict):
    for k, m in enumerate(ss.jobs):
        if not m.get("derived_from") and m["filename"] == job.get("derived_from"):
            return k
    return None


def _apply_new_image(i: int, png: bytes):
    """写盘并更新 job；若该图是母版，其派生图自动失效待重做。"""
    job = ss.jobs[i]
    path = store.save_image(ss.run_dir, job["filename"], png)
    job["image_bytes"] = png
    job["image_path"] = str(path)
    job["copies"] = []
    job["rev"] = job.get("rev", 0) + 1
    if not job.get("derived_from"):
        for other in ss.jobs:
            if other is not job and other.get("derived_from") == job["filename"]:
                other["image_bytes"] = None
                other["image_path"] = ""
                other["copies"] = []
                other["prev_image_bytes"] = None


def run_generation(master_indices: list, derived_indices: list):
    if not ss.run_dir:
        ss.run_dir = store.create_run_dir(product_info[:20])
    ref_images = [(f.name, f.getvalue(), f.type or "image/png") for f in (uploaded_refs or [])]
    total = len(master_indices) + len(derived_indices)
    progress = st.progress(0.0, text="生图中…")
    done = 0

    # 母版/独立图：全部并行提交，不设并发上限，限流交给 API 侧（generate_image 自带重试退避）。
    # 线程内不碰 session_state，参数先在主线程取好
    tasks = []
    for i in master_indices:
        job = ss.jobs[i]
        final_prompt = render(prompts["image_style_template"]["template"], {"image_prompt": job["image_prompt"]})
        tasks.append((i, final_prompt, job["ratio"]))
    if tasks:
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {
                pool.submit(imagen.generate_image, config, p, r, ref_images): i
                for i, p, r in tasks
            }
            for fut in as_completed(futures):
                i = futures[fut]
                job = ss.jobs[i]
                done += 1
                progress.progress(done / total, text=f"生图 {done}/{total}：{job['sub_scene']}（{job['ratio']}）")
                try:
                    _apply_new_image(i, fut.result())
                except Exception as e:  # noqa: BLE001
                    st.error(f"「{job['sub_scene']}（{job['ratio']}）」生图失败：{e}")

    # 派生图：依赖母版成品，串行改尺寸
    for i in derived_indices:
        job = ss.jobs[i]
        done += 1
        progress.progress(done / total, text=f"改尺寸 {done}/{total}：{job['sub_scene']}（{job['ratio']}）")
        try:
            mi = _master_index(job)
            if mi is None or ss.jobs[mi]["image_bytes"] is None:
                raise RuntimeError("4:5 母版尚未生成成功，无法改尺寸")
            master = ss.jobs[mi]
            adapt_prompt = render(prompts["ratio_adapt"]["template"], {"target_ratio": job["ratio"]})
            png = imagen.edit_image(config, master["image_bytes"], adapt_prompt, job["ratio"])
            job["image_prompt"] = master["image_prompt"]
            _apply_new_image(i, png)
        except Exception as e:  # noqa: BLE001
            st.error(f"「{job['sub_scene']}（{job['ratio']}）」改尺寸失败：{e}")
    progress.empty()
    save_manifest()


pending_masters = [
    i for i, j in enumerate(ss.jobs)
    if not j.get("derived_from") and j["image_prompt"] and j["image_bytes"] is None
]
pending_derived = []
for i, j in enumerate(ss.jobs):
    if j.get("derived_from") and j["image_bytes"] is None:
        mi = _master_index(j)
        if mi is not None and (ss.jobs[mi]["image_bytes"] is not None or mi in pending_masters):
            pending_derived.append(i)

col_gen, col_info = st.columns([1, 2])
with col_gen:
    pending_total = len(pending_masters) + len(pending_derived)
    if st.button(f"🖼️ 生成待生成的 {pending_total} 张图", type="primary", disabled=not pending_total):
        run_generation(pending_masters, pending_derived)
        st.rerun()
with col_info:
    if not uploaded_refs:
        st.info("未上传产品参考图，将走纯文生图；上传参考图可让产品与实物一致。")


def make_image_feedback(i: int):
    def apply(feedback: str):
        job = ss.jobs[i]
        edit_prompt = render(prompts["image_refine"]["template"], {"feedback": feedback})
        png = imagen.edit_image(config, job["image_bytes"], edit_prompt, job["ratio"])
        job["prev_image_bytes"] = job["image_bytes"]
        _apply_new_image(i, png)
        save_manifest()

    return apply


display = [i for i, j in enumerate(ss.jobs) if j["image_bytes"] is not None or j.get("derived_from")]
if display:
    cols = st.columns(3)
    for n, i in enumerate(display):
        job = ss.jobs[i]
        with cols[n % 3]:
            derived = bool(job.get("derived_from"))
            if job["image_bytes"] is not None:
                st.image(
                    job["image_bytes"],
                    caption=f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）"
                    + ("・改尺寸自母版" if derived else ""),
                    use_container_width=True,
                )
                if derived:
                    if st.button("🔄 重新改尺寸", key=f"regen_{g}_{i}"):
                        run_generation([], [i])
                        st.rerun()
                else:
                    if st.button("🔄 重新生成这张", key=f"regen_{g}_{i}"):
                        run_generation([i], [])
                        st.rerun()
                if job.get("prev_image_bytes") and st.button("↩️ 回退上一版", key=f"revert_{g}_{i}"):
                    prev = job["prev_image_bytes"]
                    job["prev_image_bytes"] = None
                    _apply_new_image(i, prev)
                    save_manifest()
                    st.rerun()
                with st.expander("💬 修改这张图", expanded=False):
                    st.caption("在当前图基础上按意见重绘，改完可回退上一版。")
                    chat_box(
                        f"chat_image_{g}_{i}",
                        make_image_feedback(i),
                        placeholder="例：背景换成海边；把产品放大一点；整体调亮…",
                    )
            else:
                with st.container(border=True):
                    st.markdown(f"**{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）**")
                    mi = _master_index(job)
                    if mi is not None and ss.jobs[mi]["image_bytes"] is not None:
                        st.caption("母版已就绪，待改尺寸。")
                        if st.button("🔁 由 4:5 母版改尺寸", key=f"derive_{g}_{i}"):
                            run_generation([], [i])
                            st.rerun()
                    else:
                        st.caption("等待 4:5 母版生成后改尺寸。")

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
            ss.jobs[i]["rev"] = job.get("rev", 0) + 1
        except Exception as e:  # noqa: BLE001
            st.error(f"「{job['sub_scene']}（{job['ratio']}）」文案生成失败：{e}")
    progress.empty()
    save_manifest()


def make_copies_feedback(i: int):
    def apply(feedback: str):
        job = ss.jobs[i]
        new = refine_via_llm(
            f"广告标题文案（场景：{job['main_scene']} / {job['sub_scene']}，配图见附图）",
            {"copies": job["copies"]},
            f"chat_copies_{g}_{i}",
            feedback,
            images=[(job["image_bytes"], "image/png")] if job["image_bytes"] else None,
        )
        if isinstance(new, dict):
            new = new.get("copies", [])
        if not isinstance(new, list) or not new:
            raise ValueError("模型返回的文案列表为空或格式不对")
        job["copies"] = new
        job["rev"] = job.get("rev", 0) + 1
        save_manifest()

    return apply


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
                key=f"copies_editor_{g}_{i}_v{job.get('rev', 0)}",
            )
            with st.expander("💬 让 AI 修改这批文案", expanded=False):
                chat_box(
                    f"chat_copies_{g}_{i}",
                    make_copies_feedback(i),
                    placeholder="例：语气更年轻；第 2 套换成促销角度；标题都加 emoji…",
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

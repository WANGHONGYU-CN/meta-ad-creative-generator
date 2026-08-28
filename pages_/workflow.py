"""主工作流页：多任务工作台（输入 → 找场景 → 生成生图提示词 → 生图 → 文案 → 导出）。

关键机制：
- Step2 提示词生成：把场景挖掘输出的全部变量（audience/selling_point/visual_brief/
  headline/subheadline/cta 等）喂给 Claude（「② 生图提示词生成」模板，可在提示词管理页改），
  产出含广告语的海报提示词；Step3 直接把该提示词发给生图模型（无风格外壳）
- 任务 = 一个 run 目录：完整工作状态持久化在 run 目录 state.json（core/runstate.py），
  侧边栏可新建/切换任务，历史素材页可把老任务载入继续编辑
- 后台任务：耗时环节（Step2 批量提示词 / Step3 生图改尺寸 / Step4 批量文案）提交
  后台线程池（core/tasks.py），期间本任务页面锁定并轮询进度，可切到其它任务继续工作；
  对话式修改（改图/改文案等）保持前台同步——单次交互需要即时看结果
- 双尺寸母版派生 / 场景卡片多选 / 持续对话修改 等机制不变（CLAUDE.md 决策 9/10/11）
"""
import json
from pathlib import Path

import streamlit as st

from core import db, imagen, llm, runstate, store
from core import tasks as bg
from core.config import OUTPUTS_DIR, load_config
from core.prompts import load_prompts, render

st.title("🎨 Meta 素材工作流")

config = load_config()
prompts = load_prompts()

if not config["anthropic_api_key"] or not config["openai_api_key"]:
    st.warning("请先到「设置」页填写 Anthropic 和 OpenAI 的 API Key。")

ss = st.session_state
ss.setdefault("scenes", [])           # [{main_scene, sub_scene, description}]
ss.setdefault("selected_scenes", [])  # 多选的场景下标列表
ss.setdefault("jobs", [])             # 每个 job = 一张图（场景 x 尺寸），图片按 image_path 从盘读
ss.setdefault("jobs_gen", 0)          # job 批次号，用于隔离各批次的 widget/对话状态
ss.setdefault("run_dir", None)
ss.setdefault("ref_images", [])       # 已保存的参考图相对路径（refs/xxx）
ss.setdefault("ratio_choice", None)
ss.setdefault("title_count", 3)

# 双尺寸时 4:5 为母版（排前），1:1 由母版改尺寸派生
RATIO_OPTIONS = {
    "1:1（方图）": ["1:1"],
    "4:5（竖图）": ["4:5"],
    "双尺寸（先出 4:5 母版，再改尺寸出内容一致的 1:1）": ["4:5", "1:1"],
}
RATIO_LABELS = list(RATIO_OPTIONS)
if ss.ratio_choice not in RATIO_OPTIONS:
    ss.ratio_choice = RATIO_LABELS[0]
NEW_TASK = "🆕 新建任务"


def token() -> str:
    return ss.run_dir.name if ss.run_dir else ""


@st.cache_data(show_spinner=False)
def _img_bytes(path: str, rev: int) -> bytes | None:
    """按路径读图，rev 变化时失效缓存（rev 在每次图片变更时 +1）。"""
    p = Path(path)
    return p.read_bytes() if path and p.exists() else None


def _has_image(job: dict) -> bool:
    return bool(job.get("image_path"))


def _infer_ratio_choice(jobs: list) -> str:
    if any(j.get("derived_from") for j in jobs):
        return RATIO_LABELS[2]
    if {j.get("ratio") for j in jobs} == {"4:5"}:
        return RATIO_LABELS[1]
    return RATIO_LABELS[0]


# ---------------------------------------------------------------- 任务状态存取
def state_from_ss() -> dict:
    return {
        "product_info": ss.get("product_info", ""),
        "ratio_choice": ss.ratio_choice,
        "title_count": int(ss.get("title_count", 3)),
        "scenes": ss.scenes,
        "selected_scenes": ss.selected_scenes,
        "jobs": ss.jobs,
        "jobs_gen": ss.jobs_gen,
        "ref_images": ss.ref_images,
        "chats": {str(k): v for k, v in ss.items() if str(k).startswith("chat_")},
    }


def persist():
    """把当前任务状态落盘（state.json + manifest + 数据库）。
    本任务有后台作业在跑时跳过——磁盘上的 state 正由后台更新，不能用旧内存副本覆盖。"""
    if ss.run_dir and not bg.is_running(token()):
        runstate.persist(ss.run_dir, state_from_ss())


def _clear_chat_keys():
    for k in [k for k in list(ss.keys()) if str(k).startswith("chat_")]:
        del ss[k]


def _fill_ss_from_state(state: dict):
    _clear_chat_keys()
    ss["product_info"] = state.get("product_info", "")
    rc = state.get("ratio_choice", "")
    ss.ratio_choice = rc if rc in RATIO_OPTIONS else (
        _infer_ratio_choice(state.get("jobs", [])) if state.get("jobs") else RATIO_LABELS[0]
    )
    ss["title_count"] = int(state.get("title_count", 3))
    ss.scenes = state.get("scenes", [])
    ss.selected_scenes = state.get("selected_scenes", [])
    ss.jobs = state.get("jobs", [])
    ss.jobs_gen = int(state.get("jobs_gen", 0))
    ss.ref_images = state.get("ref_images", [])
    for k, v in (state.get("chats") or {}).items():
        ss[k] = v


def _load_task(run_dir: Path) -> bool:
    state = runstate.load(run_dir) or runstate.rebuild_from_manifest(run_dir)
    if state is None:
        st.error(f"任务 {run_dir.name} 缺少 state.json / manifest.json，无法载入。")
        return False
    ss.run_dir = run_dir
    _fill_ss_from_state(state)
    return True


def _new_task():
    _clear_chat_keys()
    ss.run_dir = None
    ss.scenes, ss.selected_scenes, ss.jobs = [], [], []
    ss.jobs_gen, ss.ref_images = 0, []
    ss["product_info"] = ""
    ss.ratio_choice = RATIO_LABELS[0]
    ss["title_count"] = 3


# ---------------------------------------------------------------- 历史页跳转载入
_req = ss.pop("load_run_request", None)
if _req:
    persist()
    if _load_task(Path(_req)):
        ss["_pending_task_select"] = Path(_req).name

# ---------------------------------------------------------------- 侧边栏：任务切换
if (_p := ss.pop("_pending_task_select", None)) is not None:
    ss["task_select"] = _p

task_names = [d.name for d in runstate.list_task_dirs()]
cur = token()
if cur and cur not in task_names:
    task_names.insert(0, cur)
options = [NEW_TASK] + task_names
if ss.get("task_select") not in options:
    ss["task_select"] = cur if cur else NEW_TASK

with st.sidebar:
    st.subheader("🗂 任务")
    sel = st.selectbox("当前任务", options, key="task_select")
    running = [n for n in task_names if bg.is_running(n)]
    if running:
        st.caption("⏳ 后台运行中：" + "、".join(running))
    st.caption("任务状态自动保存，可随时切换/回来继续；历史任务也可在「历史素材」页载入。")

_current_choice = cur if cur else NEW_TASK
if sel != _current_choice:
    persist()
    if sel == NEW_TASK:
        _new_task()
    elif not _load_task(OUTPUTS_DIR / sel):
        ss["_pending_task_select"] = _current_choice
    st.rerun()

# ---------------------------------------------------------------- 后台任务：锁定 / 收割
tok = token()
stat = bg.status(tok) if tok else None

if stat and stat["state"] == "running":
    st.info(
        f"⏳ 本任务正在后台**{bg.KIND_LABELS.get(stat['kind'], '处理')}**，完成前本页锁定。"
        "可在左侧切换到其它任务继续工作，跑完回来看结果。"
    )

    @st.fragment(run_every=2.0)
    def _watch():
        s = bg.status(tok)
        if not s or s["state"] != "running":
            st.rerun(scope="app")
            return
        st.progress(min(s["done"] / s["total"], 1.0) if s["total"] else 0.0, text=s["text"])
        if s["errors"]:
            st.caption(f"已有 {len(s['errors'])} 个子任务失败，完成后可查看并重试")

    _watch()
    st.stop()

if stat and stat["state"] in ("finished", "failed") and not stat.get("consumed"):
    bg.mark_consumed(tok)
    _state = runstate.load(ss.run_dir)
    if _state:
        _fill_ss_from_state(_state)
    if stat["state"] == "failed":
        st.error("后台任务异常终止；已完成的部分均已保存，可重新提交补齐剩余。")
    else:
        st.toast(f"✅ 后台{bg.KIND_LABELS.get(stat['kind'], '任务')}完成")

if stat and stat["errors"]:
    with st.expander(f"⚠️ 上次后台任务有 {len(stat['errors'])} 个失败项", expanded=stat["state"] == "failed"):
        for e in stat["errors"]:
            st.error(e)


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
        persist()
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
if not uploaded_refs and ss.ref_images:
    st.caption(f"本任务已保存 {len(ss.ref_images)} 张参考图，生图时继续使用；重新上传即替换。")
col_ratio, col_count = st.columns(2)
with col_ratio:
    ratio_choice = st.radio("生图尺寸", RATIO_LABELS, horizontal=True, key="ratio_choice")
with col_count:
    title_count = st.number_input("每张图的文案套数", min_value=1, max_value=10, key="title_count")
ratios = RATIO_OPTIONS[ratio_choice]
dual_mode = len(ratios) > 1

st.divider()

# ---------------------------------------------------------------- Step 1 找场景
st.header("Step 1 · 挖掘投放场景")


def _scene_rows_from_result(result: dict) -> list:
    """兼容两代提示词的返回：老版 sub 只有 name/description；
    新版 sub 有 9 字段（audience/trigger/…/score_breakdown），除 name 外全部收进 detail。"""
    rows = []
    for scene in result.get("scenes", []):
        for sub in scene.get("sub_scenes", []):
            if not isinstance(sub, dict) or not sub.get("name"):
                continue
            detail = {k: v for k, v in sub.items() if k not in ("name", "description")}
            desc = str(sub.get("description", ""))
            if not desc:
                bits = [str(sub.get("trigger", "")), str(sub.get("pain_or_desire", ""))]
                desc = "；".join(b for b in bits if b)
            row = {
                "main_scene": str(scene.get("main_scene", "")),
                "sub_scene": str(sub.get("name", "")),
                "description": desc,
            }
            if detail:
                row["detail"] = detail
            rows.append(row)
    return rows


def _scene_prompt_desc(row: dict) -> str:
    """喂给生图提示词环节的场景描述：新版场景用 visual_brief 等组合，老版用 description。"""
    d = row.get("detail") or {}
    parts = []
    if d.get("visual_brief"):
        parts.append(f"广告画面要求：{d['visual_brief']}")
    if d.get("audience"):
        parts.append(f"目标用户：{d['audience']}")
    if d.get("product_use"):
        parts.append(f"产品使用链路：{d['product_use']}")
    if d.get("video_purpose"):
        parts.append(f"成片用途：{d['video_purpose']}")
    return "\n".join(parts) or row.get("description", "")


if st.button("🔍 AI 挖掘场景", type="primary", disabled=not product_info.strip()):
    mined = False
    with st.spinner("Claude 正在挖掘场景…（新版提示词需内部筛选候选，可能要 1-3 分钟）"):
        try:
            excluded = db.excluded_scene_names(product_info)
            prompt = render(
                prompts["scene_mining"]["template"],
                {
                    "product_info": product_info,
                    "excluded_scenes": json.dumps(excluded, ensure_ascii=False),
                },
            )
            result = llm.call_json(config, prompt)
            rows = _scene_rows_from_result(result)
            if not ss.run_dir:
                ss.run_dir = store.create_run_dir(product_info[:20])
                ss["_pending_task_select"] = ss.run_dir.name
            ss.scenes = rows
            ss.selected_scenes = []
            ss.jobs = []
            ss.pop("chat_scenes", None)
            persist()
            db.upsert_scene_rows_safe(product_info, ss.run_dir.name, rows)
            mined = True
        except Exception as e:  # noqa: BLE001
            st.error(f"场景挖掘失败：{e}")
    if mined:
        st.rerun()


def apply_scene_feedback(feedback: str):
    # 全字段（含 detail）发给模型，修改后 detail 原样保留/更新
    current = {"scenes": ss.scenes}
    new = refine_via_llm("场景挖掘结果（主场景 + 细分场景列表）", current, "chat_scenes", feedback)
    rows = new.get("scenes", []) if isinstance(new, dict) else new
    if not isinstance(rows, list) or not rows:
        raise ValueError("模型返回的场景列表为空或格式不对")
    cleaned = []
    for r in rows:
        if not (isinstance(r, dict) and r.get("sub_scene")):
            continue
        row = {
            "main_scene": str(r.get("main_scene", "")),
            "sub_scene": str(r.get("sub_scene", "")),
            "description": str(r.get("description", "")),
        }
        if isinstance(r.get("detail"), dict):
            row["detail"] = r["detail"]
        cleaned.append(row)
    if not cleaned:
        raise ValueError("模型返回的场景列表为空或格式不对")
    ss.scenes = cleaned
    ss.selected_scenes = []
    if ss.run_dir:
        db.upsert_scene_rows_safe(ss.get("product_info", ""), ss.run_dir.name, cleaned)


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
                d = row.get("detail") or {}
                if d.get("total_score") is not None:
                    st.caption(f"⭐ 综合评分 {d['total_score']}")
                st.caption(row.get("description", "") or "—")
                if d:
                    with st.popover("📋 详情", use_container_width=True):
                        field_labels = [
                            ("audience", "目标用户"), ("trigger", "触发时刻"),
                            ("pain_or_desire", "痛点/渴望"), ("product_use", "产品使用链路"),
                            ("video_purpose", "成片用途"), ("visual_brief", "广告画面 brief"),
                            ("headline_angle", "标题方向"),
                        ]
                        for k, label in field_labels:
                            if d.get(k):
                                st.markdown(f"**{label}**：{d[k]}")
                        scores = d.get("score_breakdown") or {}
                        if scores:
                            st.caption(
                                f"产品匹配 {scores.get('product_fit', '-')} / 画面直观 {scores.get('visual_clarity', '-')} / "
                                f"付费意愿 {scores.get('purchase_intent', '-')} / 情绪吸引 {scores.get('attention_emotion', '-')} / "
                                f"投放安全 {scores.get('meta_safety', '-')}"
                            )
                if st.button(
                    "已选中（点击取消）" if picked else "选择这个场景",
                    key=f"scene_pick_{tok}_{idx}",
                    type="primary" if picked else "secondary",
                    use_container_width=True,
                ):
                    if picked:
                        ss.selected_scenes = [i for i in ss.selected_scenes if i != idx]
                    else:
                        ss.selected_scenes = ss.selected_scenes + [idx]
                    persist()
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

# ---------------------------------------------------------------- Step 2 生成生图提示词
st.header("Step 2 · 生成生图提示词")
g = ss.jobs_gen

if st.button("✏️ 为选中场景生成提示词（后台运行）", type="primary", disabled=not selected_rows):
    ss.jobs_gen = g = g + 1
    # 清理上一批 job 的对话历史
    for k in [k for k in list(ss.keys()) if str(k).startswith(("chat_prompt_", "chat_image_", "chat_copies_"))]:
        del ss[k]
    ss.jobs = []
    persist()
    # detail（目标用户/卖点/画面 brief/广告文字）原样传给提示词管线；description 换成组合文本供 manifest 使用
    payload = [dict(r, description=_scene_prompt_desc(r)) for r in selected_rows]
    bg.submit_prompt_generation(config, prompts, ss.run_dir, product_info, payload, dual_mode, ratios)
    st.rerun()


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
            height=160,
            key=f"job_prompt_{tok}_{g}_{i}_v{job.get('rev', 0)}",
        )
        with st.expander("💬 让 AI 修改这条提示词", expanded=False):
            chat_box(
                f"chat_prompt_{g}_{i}",
                make_prompt_feedback(i),
                placeholder="例：光线改成黄昏；标题更有冲击力；产品再突出一点…",
            )

st.divider()

# ---------------------------------------------------------------- Step 3 生图
st.header("Step 3 · 生图")


def _master_index(job: dict):
    for k, m in enumerate(ss.jobs):
        if not m.get("derived_from") and m["filename"] == job.get("derived_from"):
            return k
    return None


def _apply_new_image_ss(i: int, png: bytes):
    """前台版：写盘并更新 job；若该图是母版，其派生图自动失效待重做。"""
    job = ss.jobs[i]
    path = store.save_image(ss.run_dir, job["filename"], png)
    job["image_path"] = str(path)
    job["copies"] = []
    job["rev"] = job.get("rev", 0) + 1
    db.mark_scene_has_image(ss.get("product_info", ""), job["main_scene"], job["sub_scene"])
    if not job.get("derived_from"):
        for other in ss.jobs:
            if other is not job and other.get("derived_from") == job["filename"]:
                other["image_path"] = ""
                other["copies"] = []
                other["has_prev"] = False


def _submit_images(master_indices: list, derived_indices: list):
    if uploaded_refs:
        ss.ref_images = runstate.save_ref_images(
            ss.run_dir, [(f.name, f.getvalue()) for f in uploaded_refs]
        )
    persist()
    bg.submit_image_generation(config, prompts, ss.run_dir, master_indices, derived_indices, ss.ref_images)
    st.rerun()


pending_masters = [
    i for i, j in enumerate(ss.jobs)
    if not j.get("derived_from") and j["image_prompt"] and not _has_image(j)
]
pending_derived = []
for i, j in enumerate(ss.jobs):
    if j.get("derived_from") and not _has_image(j):
        mi = _master_index(j)
        if mi is not None and (_has_image(ss.jobs[mi]) or mi in pending_masters):
            pending_derived.append(i)

col_gen, col_info = st.columns([1, 2])
with col_gen:
    pending_total = len(pending_masters) + len(pending_derived)
    if st.button(f"🖼️ 生成待生成的 {pending_total} 张图（后台运行）", type="primary", disabled=not pending_total):
        _submit_images(pending_masters, pending_derived)
with col_info:
    if not uploaded_refs and not ss.ref_images:
        st.info("未上传产品参考图，将走纯文生图；上传参考图可让产品与实物一致。")


def make_image_feedback(i: int):
    def apply(feedback: str):
        job = ss.jobs[i]
        cur = _img_bytes(job["image_path"], job.get("rev", 0))
        if not cur:
            raise RuntimeError("找不到当前图片文件")
        edit_prompt = render(prompts["image_refine"]["template"], {"feedback": feedback})
        png = imagen.edit_image(config, cur, edit_prompt, job["ratio"])
        runstate.save_prev_image(ss.run_dir, job["filename"], cur)
        _apply_new_image_ss(i, png)
        ss.jobs[i]["has_prev"] = True
        persist()

    return apply


display = [i for i, j in enumerate(ss.jobs) if _has_image(j) or j.get("derived_from")]
if display:
    cols = st.columns(3)
    for n, i in enumerate(display):
        job = ss.jobs[i]
        with cols[n % 3]:
            derived = bool(job.get("derived_from"))
            png = _img_bytes(job.get("image_path", ""), job.get("rev", 0)) if _has_image(job) else None
            if png is not None:
                st.image(
                    png,
                    caption=f"{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）"
                    + ("・改尺寸自母版" if derived else ""),
                    use_container_width=True,
                )
                if derived:
                    if st.button("🔄 重新改尺寸", key=f"regen_{tok}_{g}_{i}"):
                        _submit_images([], [i])
                else:
                    if st.button("🔄 重新生成这张", key=f"regen_{tok}_{g}_{i}"):
                        _submit_images([i], [])
                if job.get("has_prev") and st.button("↩️ 回退上一版", key=f"revert_{tok}_{g}_{i}"):
                    prev = runstate.load_prev_image(ss.run_dir, job["filename"])
                    if prev:
                        runstate.delete_prev_image(ss.run_dir, job["filename"])
                        _apply_new_image_ss(i, prev)
                        job["has_prev"] = False
                        persist()
                        st.rerun()
                    else:
                        st.error("上一版图片文件不存在")
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
                    if mi is not None and _has_image(ss.jobs[mi]):
                        st.caption("母版已就绪，待改尺寸。")
                        if st.button("🔁 由 4:5 母版改尺寸", key=f"derive_{tok}_{g}_{i}"):
                            _submit_images([], [i])
                    else:
                        st.caption("等待 4:5 母版生成后改尺寸。")

st.divider()

# ---------------------------------------------------------------- Step 4 看图写文案
st.header("Step 4 · 看图写文案")
need_copy = [i for i, j in enumerate(ss.jobs) if _has_image(j) and not j["copies"]]
if st.button(
    f"🗒️ 为 {len(need_copy)} 张图生成文案（每张 {int(title_count)} 套，后台运行）",
    type="primary",
    disabled=not need_copy,
):
    persist()
    bg.submit_copywriting(config, prompts, ss.run_dir, need_copy, int(title_count), product_info)
    st.rerun()


def make_copies_feedback(i: int):
    def apply(feedback: str):
        job = ss.jobs[i]
        png = _img_bytes(job.get("image_path", ""), job.get("rev", 0))
        new = refine_via_llm(
            f"广告标题文案（场景：{job['main_scene']} / {job['sub_scene']}，配图见附图）",
            {"copies": job["copies"]},
            f"chat_copies_{g}_{i}",
            feedback,
            images=[(png, "image/png")] if png else None,
        )
        if isinstance(new, dict):
            new = new.get("copies", [])
        if not isinstance(new, list) or not new:
            raise ValueError("模型返回的文案列表为空或格式不对")
        job["copies"] = new
        job["rev"] = job.get("rev", 0) + 1
        persist()

    return apply


copied_jobs = [i for i, j in enumerate(ss.jobs) if j["copies"]]
for i in copied_jobs:
    job = ss.jobs[i]
    with st.container(border=True):
        col_img, col_copy = st.columns([1, 2])
        with col_img:
            png = _img_bytes(job.get("image_path", ""), job.get("rev", 0))
            if png is not None:
                st.image(png, use_container_width=True)
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
                key=f"copies_editor_{tok}_{g}_{i}_v{job.get('rev', 0)}",
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
exportable = [j for j in ss.jobs if _has_image(j)]
if st.button("📦 导出（图片 + manifest + 交付表.xlsx）", type="primary", disabled=not exportable):
    persist()
    xlsx_path = store.export_xlsx(ss.run_dir, exportable)
    st.success(f"已导出到：`{ss.run_dir}`")
    st.markdown(f"- 图片目录：`{ss.run_dir}/images/`\n- 绑定关系：`{ss.run_dir}/manifest.json`\n- 交付表：`{xlsx_path}`")

"""主工作流页：多任务工作台（输入 → 找场景 → 变量直填生图 → 文案 → 导出）。

关键机制：
- 生图提示词 = 场景挖掘变量直填模板（V4）：勾选场景后，把 audience/trigger/
  pain_or_desire/product_use + 品牌名/广告语言/比例 本地替换进「② 生图总提示词」
  模板（可在提示词管理页改），渲染结果就是最终提示词，直接发给生图模型——
  不再经 Claude 生成提示词，点「生图」即开始出图
- 任务 = 一个 run 目录：完整工作状态持久化在 run 目录 state.json（core/runstate.py），
  侧边栏可新建/切换任务，历史素材页可把老任务载入继续编辑
- 后台任务：耗时环节（Step2 生图改尺寸 / Step3 批量文案）提交后台线程池
  （core/tasks.py），期间本任务页面锁定并轮询进度，可切到其它任务继续工作
- 图片对话修改走后台**并发**通道（bg.submit_image_edit）：不锁整页、多张图可同时改，
  只有被修改的图卡片锁定；每张图有版本历史（上一步/下一步/任意回跳，上限 10 版）
- 点击提速：勾选场景等高频操作只打脏标记（mark_dirty），落盘收敛到关键节点 +
  15 秒兜底；场景卡片区为 st.fragment，点卡片只局部重跑不整页刷新
- 双尺寸母版派生 / 场景卡片多选 / 持续对话修改 等机制不变（CLAUDE.md 决策 9/10/11）
"""
import json
import math
import time
from pathlib import Path

import streamlit as st

from core import assets, db, llm, runstate, store
from core import tasks as bg
from core.config import OUTPUTS_DIR, load_config
from core.prompts import load_prompts, render

st.title("🎨 Meta 素材工作流")

config = load_config()
prompts = load_prompts()
assets.ensure_backfill()  # 参考图库首次自动导入历史任务的风格图/Logo（之后仅一次文件 stat）

if not config["anthropic_api_key"] or not config["openai_api_key"]:
    st.warning("请先到「设置」页填写 Anthropic 和 OpenAI 的 API Key。")

ss = st.session_state
ss.setdefault("scenes", [])           # [{main_scene, sub_scene, description}]
ss.setdefault("selected_scenes", [])  # 多选的场景下标列表
ss.setdefault("jobs", [])             # 每个 job = 一张图（场景 x 尺寸），图片按 image_path 从盘读
ss.setdefault("jobs_gen", 0)          # job 批次号，用于隔离各批次的 widget/对话状态
ss.setdefault("run_dir", None)
ss.setdefault("ref_images", [])       # 产品参考图相对路径（refs/xxx）。上传位已于 2026-08-31 移除，仅老任务遗留数据，生图时仍生效
ss.setdefault("style_images", [])     # 海报风格参考图相对路径（refs_style/xxx）
ss.setdefault("logo_images", [])      # 品牌 Logo 图相对路径（refs_logo/xxx）
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
        "brand_name": ss.get("brand_name", ""),
        "ad_language": ss.get("ad_language", ""),
        "ratio_choice": ss.ratio_choice,
        "title_count": int(ss.get("title_count", 3)),
        "scenes": ss.scenes,
        "selected_scenes": ss.selected_scenes,
        "jobs": ss.jobs,
        "jobs_gen": ss.jobs_gen,
        "ref_images": ss.ref_images,
        "style_images": ss.style_images,
        "logo_images": ss.logo_images,
        "chats": {str(k): v for k, v in ss.items() if str(k).startswith("chat_")},
    }


def persist():
    """把当前任务状态落盘（state.json + manifest + 数据库）。
    本任务有后台作业（管线或图片修改）在跑时跳过——磁盘上的 state 正由后台更新，
    不能用旧内存副本覆盖（决策 12）；此时脏标记保留，等后台结束后的下次落盘补上。"""
    if ss.run_dir and not bg.is_busy(token()):
        runstate.persist(ss.run_dir, state_from_ss())
        ss["_dirty"] = False
        ss["_last_persist"] = time.time()


def mark_dirty():
    """高频轻操作（勾选场景等）只打脏标记，落盘收敛到关键节点 + 15 秒兜底——
    每次点击都写 state.json/manifest/SQLite（/mnt/c 慢盘三连写）是点击延迟的主因。"""
    ss["_dirty"] = True


def _clear_chat_keys():
    for k in [k for k in list(ss.keys()) if str(k).startswith("chat_")]:
        del ss[k]
    # 上传指纹跟任务走：切换/新建任务后重置，让上传框里的文件重新存进新任务。
    # 注意 _pending_* 暂存不清——还没建任务时上传的图，切换/载入任务后应自动存进新任务
    # （与指纹重置的语义一致），清掉会丢图。
    for k in ("_fp_style", "_fp_logo"):
        ss.pop(k, None)


def _fill_ss_from_state(state: dict):
    _clear_chat_keys()
    ss["product_info"] = state.get("product_info", "")
    ss["brand_name"] = state.get("brand_name", "")
    ss["ad_language"] = state.get("ad_language", "")
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
    ss.style_images = state.get("style_images", [])
    ss.logo_images = state.get("logo_images", [])
    for k, v in (state.get("chats") or {}).items():
        ss[k] = v


def _load_task(run_dir: Path) -> bool:
    state = runstate.load(run_dir) or runstate.rebuild_from_manifest(run_dir)
    if state is None:
        st.error(f"任务 {run_dir.name} 缺少 state.json / manifest.json，无法载入。")
        return False
    ss.run_dir = run_dir
    _fill_ss_from_state(state)
    ss["_dirty"] = False
    return True


def _new_task():
    _clear_chat_keys()
    ss.run_dir = None
    ss.scenes, ss.selected_scenes, ss.jobs = [], [], []
    ss.jobs_gen, ss.ref_images = 0, []
    ss.style_images, ss.logo_images = [], []
    ss["product_info"] = ""
    ss["brand_name"] = ""
    ss["ad_language"] = ""
    ss.ratio_choice = RATIO_LABELS[0]
    ss["title_count"] = 3
    ss["_dirty"] = False


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

# 脏数据兜底：高频操作只打脏标记，任意一次重跑时距上次落盘超 15 秒就顺手写一次
if ss.get("_dirty") and ss.run_dir and time.time() - ss.get("_last_persist", 0.0) > 15:
    persist()

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

# ---------------------------------------------------------------- 图片并发修改：收割 / 轮询
_edits = bg.edit_status(tok) if tok else {}
for _i, _es in list(_edits.items()):
    if _es["state"] not in ("finished", "failed"):
        continue
    bg.clear_edit(tok, _i)
    _chat = ss.get(f"chat_image_{ss.jobs_gen}_{_i}")
    if _es["state"] == "failed":
        st.error(f"❌ 图片修改失败：{_es['error']}")
        if _chat is not None:
            _chat.append({"role": "assistant", "content": f"❌ 修改失败：{_es['error']}"})
        continue
    # 只把这张图（含新版本历史）和受它牵连的派生图从盘合并回内存，不整页重载，
    # 页面上其它未落盘的改动不受影响
    _disk_jobs = (runstate.load(ss.run_dir) or {}).get("jobs", [])
    if _i < len(_disk_jobs) and _i < len(ss.jobs):
        ss.jobs[_i] = _disk_jobs[_i]
        _fname = _disk_jobs[_i].get("filename", "")
        for _k, _dj in enumerate(_disk_jobs):
            if _dj.get("derived_from") == _fname and _k < len(ss.jobs):
                ss.jobs[_k] = _dj
        st.toast(f"✅ 「{_disk_jobs[_i]['sub_scene']}（{_disk_jobs[_i]['ratio']}）」修改完成")
    if _chat is not None:
        _chat.append({"role": "assistant", "content": "✅ 修改完成，图片已更新"})

_editing_jobs = {i for i, s in _edits.items() if s["state"] == "running"}
if _editing_jobs:
    @st.fragment(run_every=2.0)
    def _watch_edits():
        e = bg.edit_status(tok)
        if not e or any(s["state"] != "running" for s in e.values()):
            st.rerun(scope="app")
            return
        st.caption(f"🎨 {len(e)} 张图正在后台修改，完成后自动更新；期间可继续修改其它图或做别的操作。")

    _watch_edits()


# ---------------------------------------------------------------- 对话修改通用件
def chat_box(chat_key: str, apply_feedback, placeholder="输入修改意见，AI 会在当前结果基础上修改…"):
    """通用修改对话框：展示历史 + 输入框；apply_feedback(fb) 负责真正修改结果，
    可返回自定义回复文案（如图片修改的「已提交后台」），返回 None 用默认文案。"""
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
                reply = apply_feedback(feedback) or "✅ 已按意见修改"
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
col_brand, col_lang = st.columns(2)
with col_brand:
    st.text_input("品牌名称（融入海报设计，可留空）", key="brand_name", placeholder="例：CoolBreeze")
with col_lang:
    st.text_input("广告语言（海报文案使用的语言）", key="ad_language", placeholder="例：English / 中文 / Español")
col_style, col_logo = st.columns(2)
with col_style:
    uploaded_styles = st.file_uploader(
        "海报风格参考图（可选，1-3 张，生成的海报会贴近其风格/配色/排版气质）",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )
with col_logo:
    uploaded_logos = st.file_uploader(
        "品牌 Logo（可选，1 张，建议透明底 PNG，会原样放进海报角落）",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=False,
    )


def _sync_uploads():
    """上传后立即接管：任务已建则马上落盘；任务未建先暂存进普通 session key
    （切页不丢），建任务后的下一次重跑自动补落盘。后台任务锁页（st.stop）或
    切页时 Streamlit 会丢弃 file_uploader 的内容，等到点生图才存就晚了。
    按 文件名+大小 指纹判断是否有新上传，避免每次重跑重复写盘。"""
    groups = (
        ([(f.name, f.getvalue()) for f in uploaded_styles or []],
         "style_images", runstate.STYLE_REFS_DIRNAME, "_fp_style", "style"),
        ([(uploaded_logos.name, uploaded_logos.getvalue())] if uploaded_logos else [],
         "logo_images", runstate.LOGO_REFS_DIRNAME, "_fp_logo", "logo"),
    )
    changed = False
    for files, attr, dirname, fp_key, kind in groups:
        pend_key = f"_pending_{attr}"
        fp = [(n, len(b)) for n, b in files]
        if files and ss.get(fp_key) != fp:
            ss[fp_key] = fp
            ss[pend_key] = files
            for n, b in files:  # 同步收录进全局参考图库（内容去重），供各任务复用
                assets.add(kind, n, b)
        if ss.run_dir and ss.get(pend_key):
            ss[attr] = runstate.save_ref_images(ss.run_dir, ss[pend_key], dirname=dirname)
            ss.pop(pend_key, None)
            changed = True
    if changed:
        persist()


_sync_uploads()


def _delete_ref_image(attr: str, rel: str):
    (Path(ss.run_dir) / rel).unlink(missing_ok=True)
    ss[attr] = [r for r in ss[attr] if r != rel]
    persist()


def _ref_thumbs(col, attr: str, label: str):
    """上传框下方的缩略图回显：切页后 file_uploader 本体无法程序回填（平台限制），
    用缩略图展示当前生效的图，支持单张删除。"""
    with col:
        pend = ss.get(f"_pending_{attr}") or []
        rels = ss.get(attr) or []
        if pend:
            st.caption(f"已暂存 {len(pend)} 张{label}（创建任务后自动保存）；重新上传即整组替换。")
        elif rels:
            st.caption(f"本任务已保存 {len(rels)} 张{label}，生图时使用；重新上传即整组替换。")
        else:
            return
        tcols = st.columns(3)
        if pend:
            for i, (_name, data) in enumerate(pend):
                with tcols[i % 3]:
                    st.image(data, use_container_width=True)
                    if st.button("✕ 删除", key=f"del_pend_{attr}_{i}"):
                        pend.pop(i)
                        st.rerun()
            return
        for i, rel in enumerate(rels):
            p = Path(ss.run_dir) / rel
            with tcols[i % 3]:
                if p.exists():
                    st.image(str(p), use_container_width=True)
                if st.button("✕ 删除", key=f"del_{attr}_{i}"):
                    _delete_ref_image(attr, rel)
                    st.rerun()


def _asset_picker(col, kind: str, attr: str, dirname: str, label: str, multi: bool):
    """「从历史图选择」：全局参考图库（data/ref_assets）的缩略图勾选区。
    应用 = 整组替换当前任务的该类参考图；任务未建时先暂存、建任务后自动落盘。"""
    with col, st.expander(f"📚 从历史{label}中选择（跨任务累积）"):
        paths = assets.list_assets(kind)
        if not paths:
            st.caption("暂无历史图，上传过一次后这里会自动累积。")
            return
        picked = []
        tcols = st.columns(3)
        for i, path in enumerate(paths):
            with tcols[i % 3]:
                st.image(path, caption=assets.display_name(path), use_container_width=True)
                c_pick, c_del = st.columns([2, 1])
                if c_pick.checkbox("选用", key=f"asset_pick_{kind}_{Path(path).name}"):
                    picked.append(path)
                if c_del.button(
                    "✕", key=f"asset_del_{kind}_{Path(path).name}",
                    help="从历史库删除（不影响已生成的任务）",
                ):
                    assets.remove(path)
                    st.rerun()
        too_many = not multi and len(picked) > 1
        if too_many:
            st.warning("Logo 只能选 1 张，请取消多余勾选。")
        if st.button(
            f"应用所选 {len(picked)} 张（整组替换当前{label}）",
            key=f"asset_apply_{kind}", disabled=not picked or too_many,
        ):
            files = [(assets.display_name(p), Path(p).read_bytes()) for p in picked]
            if ss.run_dir:
                ss[attr] = runstate.save_ref_images(ss.run_dir, files, dirname=dirname)
                ss.pop(f"_pending_{attr}", None)
                persist()
            else:
                ss[f"_pending_{attr}"] = files
            st.rerun()


_ref_thumbs(col_style, "style_images", "风格参考图")
_ref_thumbs(col_logo, "logo_images", "Logo")
_asset_picker(col_style, "style", "style_images", runstate.STYLE_REFS_DIRNAME, "风格参考图", multi=True)
_asset_picker(col_logo, "logo", "logo_images", runstate.LOGO_REFS_DIRNAME, "Logo", multi=False)

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


if st.button("🔍 AI 挖掘场景", type="primary", disabled=not product_info.strip()):
    mined = False
    with st.spinner("Claude 正在挖掘场景…（内部生成候选并逐个评分，可能要 1-3 分钟）"):
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


@st.fragment
def _scene_section():
    """场景筛选 + 卡片区。整体是 st.fragment：点卡片勾选/取消只局部重跑本区域，
    不再整页刷新两遍（点击提速的另一半）；勾选只打脏标记，落盘等关键节点。
    区域外依赖勾选数的文字（如 Step2 按钮上的数量）在下一次全页重跑时自然更新。"""
    st.caption("点击卡片勾选/取消细分场景（可多选）；对结果不满意可在下方对话框让 AI 修改。")

    # 筛选器：只影响卡片展示，不影响已勾选状态（无评分的老场景在分数筛选 >0 时会被隐藏）
    main_names = list(dict.fromkeys(row.get("main_scene", "") for row in ss.scenes))
    has_scores = any(
        (row.get("detail") or {}).get("total_score") is not None for row in ss.scenes
    )
    fcol1, fcol2, fcol3 = st.columns([3, 2, 2])
    with fcol1:
        picked_mains = st.multiselect(
            "筛选主场景（不选 = 全部）", main_names, key=f"scene_filter_main_{tok}"
        )
    with fcol2:
        min_score = (
            st.slider("最低综合评分", 0, 100, 0, key=f"scene_filter_score_{tok}")
            if has_scores
            else 0
        )
    with fcol3:
        compact = st.toggle(
            "精简模式",
            value=True,
            key=f"scene_compact_{tok}",
            help="每个主场景只显示综合评分最高的前 30%（至少 2 个），已勾选的场景恒显示；关掉即展开全部。",
        )

    def _scene_visible(row: dict) -> bool:
        if picked_mains and row.get("main_scene", "") not in picked_mains:
            return False
        if min_score > 0:
            score = (row.get("detail") or {}).get("total_score")
            if not isinstance(score, (int, float)) or score < min_score:
                return False
        return True

    visible = [idx for idx, row in enumerate(ss.scenes) if _scene_visible(row)]
    if len(visible) < len(ss.scenes):
        hidden_picked = sum(
            1 for i in ss.selected_scenes if i < len(ss.scenes) and i not in visible
        )
        note = f"（其中 {hidden_picked} 个已勾选场景被筛选隐藏，勾选状态不受影响）" if hidden_picked else ""
        st.caption(f"筛选后显示 {len(visible)} / {len(ss.scenes)} 个细分场景{note}")

    groups = {}
    for idx in visible:
        groups.setdefault(ss.scenes[idx].get("main_scene", ""), []).append(idx)

    def _score_of(idx: int) -> float:
        s = (ss.scenes[idx].get("detail") or {}).get("total_score")
        return float(s) if isinstance(s, (int, float)) else -1.0

    collapsed = {}  # 主场景 -> 精简模式下收起的数量
    if compact:
        for main, indices in groups.items():
            keep = max(2, math.ceil(len(indices) * 0.3))
            top = set(sorted(indices, key=lambda i: -_score_of(i))[:keep])
            shown = [i for i in indices if i in top or i in ss.selected_scenes]
            collapsed[main] = len(indices) - len(shown)
            groups[main] = shown

    for main, indices in groups.items():
        hidden_n = collapsed.get(main, 0)
        st.markdown(f"##### {main}" + (f"　`已收起 {hidden_n} 个低分场景`" if hidden_n else ""))
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
                    mark_dirty()
                    st.rerun(scope="fragment")
    with st.expander("💬 对场景结果不满意？让 AI 修改", expanded=False):
        chat_box("chat_scenes", apply_scene_feedback, placeholder="例：场景太泛了，聚焦冬季户外；把第 2 个主场景换成送礼场景…")

    n_sel = len([i for i in ss.selected_scenes if i < len(ss.scenes)])
    if n_sel:
        extra = "（每个场景 = 4:5 母版 + 改尺寸 1:1）" if dual_mode else ""
        st.caption(
            f"已选 **{n_sel}** 个细分场景 × {len(ratios)} 个尺寸 = "
            f"将生成 {n_sel * len(ratios)} 张图{extra}"
        )
    else:
        st.caption("尚未选择场景。")


if ss.scenes:
    _scene_section()
selected_rows = [ss.scenes[i] for i in ss.selected_scenes if i < len(ss.scenes)]

st.divider()

# ---------------------------------------------------------------- Step 2 生图（变量直填总提示词）
st.header("Step 2 · 生图")
g = ss.jobs_gen


def _render_job_prompt(row: dict, ratio: str) -> str:
    """场景变量 + 品牌名/广告语言/比例 本地替换进「② 生图总提示词」模板（瞬时完成，
    不调 Claude），渲染结果即最终提示词。{reference_style_image} 占位符保留，
    由生图管线在发送瞬间按当时是否带风格图填充（core/tasks.py）。
    老格式场景（无 detail）product_use 回退 description，其余字段为空。"""
    d = row.get("detail") or {}
    return render(
        prompts["image_gen"]["template"],
        {
            "main_scene": row.get("main_scene", ""),
            "sub_scene": row.get("sub_scene", ""),
            "audience": d.get("audience", ""),
            "trigger": d.get("trigger", ""),
            "pain_or_desire": d.get("pain_or_desire", ""),
            "product_use": d.get("product_use") or row.get("description", ""),
            "aspect_ratio": ratio,
            "ad_language": (ss.get("ad_language", "") or "").strip() or "与产品目标市场语言一致",
            "brand_name": (ss.get("brand_name", "") or "").strip() or "未提供",
        },
    )


def _build_jobs(rows: list) -> list:
    """选中场景 → jobs（每张图一个）。双尺寸时 4:5 为母版，1:1 派生（决策 9）。"""
    jobs = []
    master_ratios = ["4:5"] if dual_mode else list(ratios)
    for row in rows:
        master_filename = ""
        for ratio in master_ratios:
            master_filename = store.image_filename(row["main_scene"], row["sub_scene"], ratio)
            jobs.append(bg.new_job(row, ratio, _render_job_prompt(row, ratio), master_filename, ""))
        if dual_mode and master_filename:
            jobs.append(
                bg.new_job(row, "1:1", "", store.image_filename(row["main_scene"], row["sub_scene"], "1:1"), master_filename)
            )
    return jobs


def _submit_images(master_indices: list, derived_indices: list):
    if bg.edits_running(tok):
        st.warning("有图片正在后台修改，请等修改完成后再生图。")
        return
    if uploaded_styles:
        ss.style_images = runstate.save_ref_images(
            ss.run_dir, [(f.name, f.getvalue()) for f in uploaded_styles],
            dirname=runstate.STYLE_REFS_DIRNAME,
        )
    if uploaded_logos:
        ss.logo_images = runstate.save_ref_images(
            ss.run_dir, [(uploaded_logos.name, uploaded_logos.getvalue())],
            dirname=runstate.LOGO_REFS_DIRNAME,
        )
    persist()
    bg.submit_image_generation(
        config, prompts, ss.run_dir, master_indices, derived_indices,
        ss.ref_images, ss.style_images, ss.logo_images,
    )
    st.rerun()


col_gen_all, col_ref_info = st.columns([1, 2])
with col_gen_all:
    # 按钮不按 selected_rows 禁用/计数：勾选发生在场景 fragment 内（只局部重跑），
    # 本处代码只在整页重跑时执行，按旧数据禁用会导致「勾了场景按钮还是灰的」。
    # 点击本身就会触发整页重跑，此刻 ss.selected_scenes 一定是最新的，点击时实时校验即可；
    # 实时勾选数看 Step 1 底部的「已选 N 个 = 将生成 N 张图」提示。
    if st.button("🖼️ 为已勾选的场景生成图（后台运行）", type="primary", disabled=not ss.scenes):
        fresh_rows = [ss.scenes[i] for i in ss.selected_scenes if i < len(ss.scenes)]
        if not fresh_rows:
            st.warning("请先在 Step 1 勾选至少一个场景。")
        elif bg.edits_running(tok):
            st.warning("有图片正在后台修改，请等修改完成后再生图。")
        else:
            ss.jobs_gen = g = g + 1
            # 清理上一批 job 的对话历史
            for k in [k for k in list(ss.keys()) if str(k).startswith(("chat_prompt_", "chat_image_", "chat_copies_"))]:
                del ss[k]
            ss.jobs = _build_jobs(fresh_rows)
            _submit_images(
                [i for i, j in enumerate(ss.jobs) if not j.get("derived_from")],
                [i for i, j in enumerate(ss.jobs) if j.get("derived_from")],
            )
with col_ref_info:
    bits = []
    if ss.ref_images:  # 老任务遗留的产品参考图（Step 0 上传位已移除，生图时仍生效）
        bits.append("产品参考图")
    if uploaded_styles or ss.get("_pending_style_images") or ss.style_images:
        bits.append("风格参考图")
    if uploaded_logos or ss.get("_pending_logo_images") or ss.logo_images:
        bits.append("品牌 Logo")
    if bits:
        st.caption(f"已带 {' + '.join(bits)}，生图时会随提示词一并发给模型。")
    elif selected_rows:
        st.warning("⚠ 本任务未带任何参考图（风格图/Logo），将纯文生图。可回 Step 0 上传或「从历史图中选择」。")


def make_prompt_feedback(i: int):
    def apply(feedback: str):
        job = ss.jobs[i]
        new = refine_via_llm(
            f"生图总提示词（场景：{job['main_scene']} / {job['sub_scene']}，比例 {job['ratio']}，渲染后直发生图模型）",
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
    st.caption("每张图的最终提示词（场景变量已填入总提示词模板）可直接编辑或让 AI 修改，改后点对应图片的「重新生成这张」生效。")
    for i, job in enumerate(ss.jobs):
        if job.get("derived_from"):
            st.info(
                f"**{job['main_scene']} / {job['sub_scene']}（{job['ratio']}）**：由 4:5 母版成品改尺寸生成，"
                "内容与母版一致，无需单独提示词。"
            )
            continue
        with st.expander(f"📄 {job['main_scene']} / {job['sub_scene']}（{job['ratio']}）提示词", expanded=False):
            ss.jobs[i]["image_prompt"] = st.text_area(
                "最终提示词",
                value=job["image_prompt"],
                height=240,
                key=f"job_prompt_{tok}_{g}_{i}_v{job.get('rev', 0)}",
                label_visibility="collapsed",
            )
            chat_box(
                f"chat_prompt_{g}_{i}",
                make_prompt_feedback(i),
                placeholder="例：光线改成黄昏；构图更聚焦人物；产品再突出一点…",
            )


def _master_index(job: dict):
    for k, m in enumerate(ss.jobs):
        if not m.get("derived_from") and m["filename"] == job.get("derived_from"):
            return k
    return None


def _apply_new_image_ss(i: int, png: bytes):
    """前台版：写盘（含版本历史）并更新 job；若该图是母版，其派生图自动失效待重做。"""
    job = ss.jobs[i]
    path = runstate.apply_image_version(ss.run_dir, job, png)
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

pending_total = len(pending_masters) + len(pending_derived)
if pending_total:
    if st.button(f"🔁 补齐/重试待生成的 {pending_total} 张图（后台运行）"):
        _submit_images(pending_masters, pending_derived)


def make_image_feedback(i: int):
    """图片修改改为后台并发（bg.submit_image_edit）：提交即返回，不再前台等 1-2 分钟；
    多张图可同时修改，完成后页面顶部收割区自动合并结果。"""

    def apply(feedback: str):
        job = ss.jobs[i]
        if not _has_image(job):
            raise RuntimeError("找不到当前图片文件")
        if not bg.submit_image_edit(config, prompts, ss.run_dir, i, feedback):
            raise RuntimeError("这张图已有修改在进行中，或本任务后台管线正在运行")
        return "🕐 已提交后台修改，完成后图片自动更新；期间可继续修改其它图或做别的操作"

    return apply


def _goto_version(i: int, new_idx: int):
    """把第 i 张图切到版本 new_idx（上一步/下一步/任意回跳）。
    走 runstate.update 锁内改盘（与并发的图片修改互不覆盖），再把结果合并回内存。"""
    hit = {}

    def mut(state):
        jobs = state.get("jobs", [])
        if i >= len(jobs):
            return
        job = jobs[i]
        if not runstate.goto_image_version(ss.run_dir, job, new_idx):
            return
        job["rev"] = job.get("rev", 0) + 1
        job["copies"] = []
        if not job.get("derived_from"):  # 母版换版本 → 派生图失效待重做
            for other in jobs:
                if other is not job and other.get("derived_from") == job["filename"]:
                    other["image_path"] = ""
                    other["copies"] = []
                    other["has_prev"] = False
        hit["filename"] = job["filename"]

    state = runstate.update(ss.run_dir, mut)
    if not hit:
        st.error("该版本的图片文件不存在。")
        return
    disk_jobs = state.get("jobs", [])
    if i < len(ss.jobs) and i < len(disk_jobs):
        ss.jobs[i] = disk_jobs[i]
        for k, dj in enumerate(disk_jobs):
            if dj.get("derived_from") == hit["filename"] and k < len(ss.jobs):
                ss.jobs[k] = dj
    st.rerun()


def _hist_bar(i: int, job: dict):
    """图片下方的版本导航：◀ 上一版 / 下一版 ▶ + 历史缩略图任意回跳。
    历史机制启用前的老图（只有 .prev 单版回退）保留旧按钮，首次修改后自动并入版本链。"""
    hist = job.get("hist") or []
    if len(hist) >= 2:
        hidx = int(job.get("hist_idx", len(hist) - 1))
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        if c1.button("◀ 上一版", key=f"hprev_{tok}_{g}_{i}", disabled=hidx <= 0, use_container_width=True):
            _goto_version(i, hidx - 1)
        c2.caption(f"{hidx + 1} / {len(hist)} 版")
        if c3.button("下一版 ▶", key=f"hnext_{tok}_{g}_{i}", disabled=hidx >= len(hist) - 1, use_container_width=True):
            _goto_version(i, hidx + 1)
        with st.expander("🕘 历史版本", expanded=False):
            st.caption(f"最多保留最近 {runstate.HIST_LIMIT} 版；回到旧版后再修改，会丢弃它后面的版本。")
            tcols = st.columns(3)
            for k, rel in enumerate(hist):
                p = Path(ss.run_dir) / rel
                with tcols[k % 3]:
                    if p.exists():
                        st.image(
                            str(p),
                            caption=f"第 {k + 1} 版" + ("（当前）" if k == hidx else ""),
                            use_container_width=True,
                        )
                    if k != hidx and st.button("回到此版", key=f"hgoto_{tok}_{g}_{i}_{k}", use_container_width=True):
                        _goto_version(i, k)
    elif job.get("has_prev"):
        if st.button("↩️ 回退上一版", key=f"revert_{tok}_{g}_{i}"):
            prev = runstate.load_prev_image(ss.run_dir, job["filename"])
            if prev:
                runstate.delete_prev_image(ss.run_dir, job["filename"])
                _apply_new_image_ss(i, prev)
                job["has_prev"] = False
                persist()
                st.rerun()
            else:
                st.error("上一版图片文件不存在")


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
                if i in _editing_jobs:
                    st.info("🎨 修改中…（后台运行，完成后自动更新，期间可操作其它图）")
                else:
                    if derived:
                        if st.button("🔄 重新改尺寸", key=f"regen_{tok}_{g}_{i}"):
                            _submit_images([], [i])
                    else:
                        if st.button("🔄 重新生成这张", key=f"regen_{tok}_{g}_{i}"):
                            _submit_images([i], [])
                    _hist_bar(i, job)
                    with st.expander("💬 修改这张图", expanded=False):
                        st.caption("在当前图基础上按意见重绘（后台运行，多张图可同时改）；历史版本可随时上一步/下一步。")
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

# ---------------------------------------------------------------- Step 3 看图写文案
st.header("Step 3 · 看图写文案")
need_copy = [i for i, j in enumerate(ss.jobs) if _has_image(j) and not j["copies"]]
if st.button(
    f"🗒️ 为 {len(need_copy)} 张图生成文案（每张 {int(title_count)} 套，后台运行）",
    type="primary",
    disabled=not need_copy,
):
    if bg.edits_running(tok):
        st.warning("有图片正在后台修改，请等修改完成后再生成文案。")
    else:
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
            images=[llm.vision_image(png)] if png else None,
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

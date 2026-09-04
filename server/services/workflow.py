"""工作流业务逻辑（无 UI 依赖），数据层为 PostgreSQL（决策 20 第二阶段）。

- 所有状态读写走 server/services/state_store（每 run 锁 + DB 事务），
  交换格式仍是 state dict；对话历史在 chat_messages 表，
  bundle 里的 key 为 chat_scenes / chat_prompt_{i} / chat_image_{i} / chat_copies_{i}（i=job seq）；
- manifest.json 不再持续落盘，导出交付包时由库内数据现场生成（协议不变）。
"""
import json
import zipfile
from datetime import datetime
from pathlib import Path

from core import llm, store
from core import tasks as bg
from core.prompts import render

from server.services import scene_lib_store
from server.services import state_store as st

# 与旧版完全一致的比例选项（run.ratio_choice 存的就是这些 label）
RATIO_OPTIONS = {
    "1:1（方图）": ["1:1"],
    "4:5（竖图）": ["4:5"],
    "双尺寸（先出 4:5 母版，再改尺寸出内容一致的 1:1）": ["4:5", "1:1"],
}
RATIO_LABELS = list(RATIO_OPTIONS)
# API 侧允许用短别名（前端好用），落库时转成 label
RATIO_ALIASES = {"1:1": RATIO_LABELS[0], "4:5": RATIO_LABELS[1], "dual": RATIO_LABELS[2]}

# 导出交付包内 manifest.json 的 job 字段白名单（对外交付协议，不得增删）
MANIFEST_JOB_KEYS = [
    "main_scene", "sub_scene", "sub_scene_desc", "ratio",
    "image_prompt", "filename", "image_path", "copies", "derived_from",
]


def normalize_ratio_choice(value: str) -> str:
    if value in RATIO_OPTIONS:
        return value
    if value in RATIO_ALIASES:
        return RATIO_ALIASES[value]
    raise ValueError(f"不支持的尺寸选项：{value}")


def ratios_of(state: dict) -> list:
    rc = state.get("ratio_choice") or ""
    return RATIO_OPTIONS.get(rc, RATIO_OPTIONS[RATIO_LABELS[0]])


def scene_rows_from_result(result: dict) -> list:
    """挖掘返回 → 场景行。sub 的 name/description 之外字段全部收进 detail。"""
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


def render_job_prompt(prompts: dict, state: dict, row: dict, ratio: str) -> str:
    """场景变量 + 品牌名/广告语言/比例 本地替换进「生图总提示词」模板。
    {reference_style_image} 占位符保留，由生图管线在发送瞬间填充（core/tasks.py）。"""
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
            "ad_language": (state.get("ad_language", "") or "").strip() or "与产品目标市场语言一致",
            "brand_name": (state.get("brand_name", "") or "").strip() or "未提供",
        },
    )


def build_jobs(prompts: dict, state: dict, rows: list) -> list:
    """选中场景 → jobs（每张图一个）。双尺寸时 4:5 为母版，1:1 派生（决策 9）。"""
    ratios = ratios_of(state)
    dual_mode = len(ratios) > 1
    master_ratios = ["4:5"] if dual_mode else list(ratios)
    jobs = []
    for row in rows:
        master_filename = ""
        for ratio in master_ratios:
            master_filename = store.image_filename(row["main_scene"], row["sub_scene"], ratio)
            jobs.append(bg.new_job(row, ratio, render_job_prompt(prompts, state, row, ratio), master_filename, ""))
        if dual_mode and master_filename:
            jobs.append(
                bg.new_job(row, "1:1", "", store.image_filename(row["main_scene"], row["sub_scene"], "1:1"), master_filename)
            )
    return jobs


def has_image(job: dict) -> bool:
    return bool(job.get("image_path"))


# ---------------------------------------------------------------- 对话式修改公共件
def refine_via_llm(config, prompts, history: list, task_context, current_output, feedback, images=None):
    """走「结果修改」提示词让 Claude 修订当前结果，返回修改后的内容。
    history 为该对话已有消息列表（历史意见取其中的用户消息，不含本次）。"""
    history_lines = [f"- {m['content']}" for m in history or [] if m.get("role") == "user"]
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


_DONE = {"role": "assistant", "content": "✅ 已按意见修改"}


# ---------------------------------------------------------------- 场景：对话修改
def refine_scenes(config, prompts, run_dir: Path, feedback: str) -> dict:
    """让 Claude 修订场景列表；成功后落库并同步场景库，返回最新 state。"""
    state = st.load(run_dir) or {}
    current = {"scenes": state.get("scenes", [])}
    history = (state.get("chats") or {}).get("chat_scenes") or []
    new = refine_via_llm(config, prompts, history, "场景挖掘结果（主场景 + 细分场景列表）", current, feedback)
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

    new_state = st.replace_scenes(run_dir, cleaned)
    st.append_chat(run_dir, "scenes", None, {"role": "user", "content": feedback}, _DONE)
    scene_lib_store.upsert_scenes_safe(
        new_state["product_id"], st.parse_run_name(Path(run_dir).name), cleaned)
    new_state.setdefault("chats", {}).setdefault("chat_scenes", []).extend(
        [{"role": "user", "content": feedback}, dict(_DONE)])
    return new_state


# ---------------------------------------------------------------- 提示词 / 文案：对话修改
def _job_or_raise(state: dict, i: int) -> dict:
    jobs = state.get("jobs", [])
    if not (0 <= i < len(jobs)):
        raise IndexError("job 下标越界")
    return jobs[i]


def refine_job_prompt(config, prompts, run_dir: Path, i: int, feedback: str) -> dict:
    state = st.load(run_dir) or {}
    job = _job_or_raise(state, i)
    history = (state.get("chats") or {}).get(f"chat_prompt_{i}") or []
    new = refine_via_llm(
        config, prompts, history,
        f"生图总提示词（场景：{job['main_scene']} / {job['sub_scene']}，比例 {job['ratio']}，渲染后直发生图模型）",
        {"image_prompt": job["image_prompt"]},
        feedback,
    )
    if isinstance(new, dict):
        new = new.get("image_prompt", "")
    if not str(new).strip():
        raise ValueError("模型返回的提示词为空")
    new_state = st.update_job(run_dir, i, image_prompt=str(new).strip())
    st.append_chat(run_dir, "prompt", i, {"role": "user", "content": feedback}, _DONE)
    new_state.setdefault("chats", {}).setdefault(f"chat_prompt_{i}", []).extend(
        [{"role": "user", "content": feedback}, dict(_DONE)])
    return new_state


def refine_job_copies(config, prompts, run_dir: Path, i: int, feedback: str) -> dict:
    state = st.load(run_dir) or {}
    job = _job_or_raise(state, i)
    history = (state.get("chats") or {}).get(f"chat_copies_{i}") or []
    images = None
    p = Path(job.get("image_path", ""))
    if job.get("image_path") and p.exists():
        images = [llm.vision_image(p.read_bytes())]
    new = refine_via_llm(
        config, prompts, history,
        f"广告标题文案（场景：{job['main_scene']} / {job['sub_scene']}，配图见附图）",
        {"copies": job["copies"]},
        feedback,
        images=images,
    )
    if isinstance(new, dict):
        new = new.get("copies", [])
    if not isinstance(new, list) or not new:
        raise ValueError("模型返回的文案列表为空或格式不对")
    new_state = st.set_job_copies(run_dir, i, new)
    st.append_chat(run_dir, "copies", i, {"role": "user", "content": feedback}, _DONE)
    new_state.setdefault("chats", {}).setdefault(f"chat_copies_{i}", []).extend(
        [{"role": "user", "content": feedback}, dict(_DONE)])
    return new_state


# ---------------------------------------------------------------- 生图提交
def _submit_to_pipeline(config, prompts, run_dir: Path, state: dict, masters: list, derived: list) -> None:
    ok = bg.submit_image_generation(
        config, prompts, run_dir, masters, derived,
        state.get("ref_images", []), state.get("style_images", []), state.get("logo_images", []),
    )
    if not ok:
        raise RuntimeError("本任务已有后台任务或图片修改在运行，请稍后再试")


def start_generation(config, prompts, run_dir: Path) -> dict:
    """勾选场景一键生图：重建 jobs（旧批次连带对话级联清理）并提交后台管线。
    返回最新 state。冲突（管线/修改在跑）抛 RuntimeError。"""
    key = Path(run_dir).name
    if bg.is_busy(key):
        raise RuntimeError("本任务已有后台任务或图片修改在运行，请稍后再试")
    state = st.load(run_dir) or {}
    rows = [state["scenes"][i] for i in state.get("selected_scenes", []) if i < len(state.get("scenes", []))]
    if not rows:
        raise ValueError("请先勾选至少一个场景")
    state = st.rebuild_jobs(run_dir, build_jobs(prompts, state, rows))
    masters = [i for i, j in enumerate(state["jobs"]) if not j.get("derived_from")]
    derived = [i for i, j in enumerate(state["jobs"]) if j.get("derived_from")]
    _submit_to_pipeline(config, prompts, run_dir, state, masters, derived)
    return state


def pending_indices(state: dict) -> tuple:
    """待生成/失败可重试的下标：(母版列表, 派生列表)。"""
    jobs = state.get("jobs", [])

    def master_index(job):
        for k, m in enumerate(jobs):
            if not m.get("derived_from") and m["filename"] == job.get("derived_from"):
                return k
        return None

    masters = [
        i for i, j in enumerate(jobs)
        if not j.get("derived_from") and j.get("image_prompt") and not has_image(j)
    ]
    derived = []
    for i, j in enumerate(jobs):
        if j.get("derived_from") and not has_image(j):
            mi = master_index(j)
            if mi is not None and (has_image(jobs[mi]) or mi in masters):
                derived.append(i)
    return masters, derived


def retry_generation(config, prompts, run_dir: Path) -> int:
    """补齐/重试待生成的图，返回提交张数（0 = 无待生成）。"""
    state = st.load(run_dir) or {}
    masters, derived = pending_indices(state)
    if not masters and not derived:
        return 0
    _submit_to_pipeline(config, prompts, run_dir, state, masters, derived)
    return len(masters) + len(derived)


def regenerate_job(config, prompts, run_dir: Path, i: int) -> None:
    """单张重生成：母版走生图，派生图走母版改尺寸。"""
    state = st.load(run_dir) or {}
    job = _job_or_raise(state, i)
    if job.get("derived_from"):
        _submit_to_pipeline(config, prompts, run_dir, state, [], [i])
    else:
        _submit_to_pipeline(config, prompts, run_dir, state, [i], [])


# ---------------------------------------------------------------- 图片对话修改（后台并发）
def submit_image_edit(config, prompts, run_dir: Path, i: int, feedback: str) -> None:
    """提交单张图后台修改，并把对话写入库（用户意见 + 排队回执）。"""
    state = st.load(run_dir) or {}
    job = _job_or_raise(state, i)
    if not has_image(job):
        raise RuntimeError("找不到当前图片文件")
    if not bg.submit_image_edit(config, prompts, run_dir, i, feedback):
        raise RuntimeError("这张图已有修改在进行中，或本任务后台管线正在运行")
    st.append_chat(
        run_dir, "image", i,
        {"role": "user", "content": feedback},
        {"role": "assistant", "content": "🕐 已提交后台修改，完成后图片自动更新；期间可继续修改其它图或做别的操作"},
    )


def ack_image_edit(run_dir: Path, i: int) -> dict | None:
    """收割一张图的修改结果：清除状态记录并把结果写进对话历史。
    返回被清除的状态（{state, error}），无待收割记录返回 None。"""
    key = Path(run_dir).name
    status = bg.edit_status(key).get(i)
    if not status or status["state"] not in ("finished", "failed"):
        return None
    bg.clear_edit(key, i)
    msg = "✅ 修改完成，图片已更新" if status["state"] == "finished" else f"❌ 修改失败：{status['error']}"
    st.append_chat(run_dir, "image", i, {"role": "assistant", "content": msg})
    return status


# ---------------------------------------------------------------- 图片版本历史
def goto_version(run_dir: Path, i: int, new_idx: int) -> dict:
    """把第 i 张图切到版本 new_idx（锁内文件+库一致更新）。
    母版换版本后派生图失效待重做、copies 清空。版本缺失抛 FileNotFoundError。"""
    return st.goto_image_version(run_dir, i, new_idx)


# ---------------------------------------------------------------- 文案
def start_copywriting(config, prompts, run_dir: Path, title_count: int | None = None) -> int:
    """对已出图且无文案的 job 批量生成文案（后台），返回提交张数。"""
    state = st.load(run_dir) or {}
    need = [i for i, j in enumerate(state.get("jobs", [])) if has_image(j) and not j.get("copies")]
    if not need:
        return 0
    count = int(title_count or state.get("title_count", 3))
    if title_count is not None and int(title_count) != int(state.get("title_count", 3)):
        st.patch_run_fields(run_dir, {"title_count": int(title_count)})
    ok = bg.submit_copywriting(config, prompts, run_dir, need, count, state.get("product_info", ""))
    if not ok:
        raise RuntimeError("本任务已有后台任务在运行，请稍后再试")
    return len(need)


# ---------------------------------------------------------------- 导出
def _manifest_from_state(state: dict) -> dict:
    """交付包内 manifest.json：由库内数据现场生成，字段协议与历史版本一致。"""
    return {
        "product_info": state.get("product_info", ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "jobs": [
            {k: job.get(k, [] if k == "copies" else "") for k in MANIFEST_JOB_KEYS}
            for job in state.get("jobs", [])
        ],
    }


def export_run(run_dir: Path) -> dict:
    """导出交付表 xlsx，并打包 交付包.zip（图片 + manifest + 交付表），返回文件路径。"""
    state = st.load(run_dir) or {}
    exportable = [j for j in state.get("jobs", []) if has_image(j)]
    if not exportable:
        raise ValueError("没有已出图的素材可导出")
    xlsx_path = store.export_xlsx(run_dir, exportable)
    zip_path = Path(run_dir) / "交付包.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in exportable:
            p = Path(run_dir) / "images" / job["filename"]
            if p.exists():
                zf.write(p, f"images/{job['filename']}")
        zf.writestr("manifest.json", json.dumps(_manifest_from_state(state), ensure_ascii=False, indent=2))
        zf.write(xlsx_path, xlsx_path.name)
    return {"xlsx": str(xlsx_path), "zip": str(zip_path), "images": len(exportable)}

"""工作流业务逻辑（无 UI 依赖）：从 pages_/workflow.py 抽取，供 FastAPI 层调用。

与 Streamlit 页的关键差异：
- 所有状态修改都走 runstate.update() 锁内「读盘-改-写盘」，字段级合并，
  不存在「整份内存 state 覆盖盘上后台结果」的问题，因此不需要 persist 守卫/脏标记；
- 对话历史直接存 state.json 的 chats key（沿用 chat_scenes / chat_prompt_{g}_{i} /
  chat_image_{g}_{i} / chat_copies_{g}_{i} 命名，与 Streamlit 版数据互通）；
- 数据协议（state.json / manifest.json / prompts.json / xlsx）与 CLAUDE.md 一致，未变。
"""
import json
import zipfile
from pathlib import Path

from core import db, llm, runstate, store
from core import tasks as bg
from core.prompts import render

# 与 pages_/workflow.py 完全一致的比例选项（state.ratio_choice 存的就是这些 label，
# 不得改动，否则老任务载入后比例解析不一致）
RATIO_OPTIONS = {
    "1:1（方图）": ["1:1"],
    "4:5（竖图）": ["4:5"],
    "双尺寸（先出 4:5 母版，再改尺寸出内容一致的 1:1）": ["4:5", "1:1"],
}
RATIO_LABELS = list(RATIO_OPTIONS)
# API 侧允许用短别名（前端好用），落盘时转成 label
RATIO_ALIASES = {"1:1": RATIO_LABELS[0], "4:5": RATIO_LABELS[1], "dual": RATIO_LABELS[2]}


def normalize_ratio_choice(value: str) -> str:
    if value in RATIO_OPTIONS:
        return value
    if value in RATIO_ALIASES:
        return RATIO_ALIASES[value]
    raise ValueError(f"不支持的尺寸选项：{value}")


def infer_ratio_choice(jobs: list) -> str:
    if any(j.get("derived_from") for j in jobs):
        return RATIO_LABELS[2]
    if {j.get("ratio") for j in jobs} == {"4:5"}:
        return RATIO_LABELS[1]
    return RATIO_LABELS[0]


def ratios_of(state: dict) -> list:
    rc = state.get("ratio_choice") or ""
    if rc not in RATIO_OPTIONS:
        rc = infer_ratio_choice(state.get("jobs", [])) if state.get("jobs") else RATIO_LABELS[0]
    return RATIO_OPTIONS[rc]


def scene_rows_from_result(result: dict) -> list:
    """兼容两代提示词的返回：老版 sub 只有 name/description；
    新版 sub 有多字段（audience/trigger/…/score_breakdown），除 name 外全部收进 detail。"""
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
    {reference_style_image} 占位符保留，由生图管线在发送瞬间填充（core/tasks.py）。
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


# ---------------------------------------------------------------- 对话历史（存 state.chats）
def append_chat(run_dir: Path, chat_key: str, *messages: dict) -> None:
    def mut(state):
        chats = state.setdefault("chats", {})
        chats.setdefault(chat_key, []).extend(messages)

    runstate.update(run_dir, mut)


def refine_via_llm(config, prompts, state, chat_key, task_context, current_output, feedback, images=None):
    """走「结果修改」提示词让 Claude 修订当前结果，返回修改后的内容。
    历史意见取 state.chats[chat_key] 中的用户消息（与 Streamlit 版一致，不含本次）。"""
    history = (state.get("chats") or {}).get(chat_key) or []
    history_lines = [f"- {m['content']}" for m in history if m.get("role") == "user"]
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


# ---------------------------------------------------------------- 场景：对话修改
def refine_scenes(config, prompts, run_dir: Path, feedback: str) -> dict:
    """让 Claude 修订场景列表；成功后落盘并同步场景库，返回最新 state。"""
    state = runstate.load(run_dir) or runstate.default_state()
    current = {"scenes": state.get("scenes", [])}
    new = refine_via_llm(config, prompts, state, "chat_scenes", "场景挖掘结果（主场景 + 细分场景列表）", current, feedback)
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

    def mut(s):
        s["scenes"] = cleaned
        s["selected_scenes"] = []
        chats = s.setdefault("chats", {})
        chats.setdefault("chat_scenes", []).extend(
            [{"role": "user", "content": feedback}, {"role": "assistant", "content": "✅ 已按意见修改"}]
        )

    new_state = runstate.update(run_dir, mut)
    db.upsert_scene_rows_safe(new_state.get("product_info", ""), Path(run_dir).name, cleaned)
    return new_state


# ---------------------------------------------------------------- 提示词 / 文案：对话修改
def refine_job_prompt(config, prompts, run_dir: Path, i: int, feedback: str) -> dict:
    state = runstate.load(run_dir) or runstate.default_state()
    jobs = state.get("jobs", [])
    if not (0 <= i < len(jobs)):
        raise IndexError("job 下标越界")
    job = jobs[i]
    g = int(state.get("jobs_gen", 0))
    chat_key = f"chat_prompt_{g}_{i}"
    new = refine_via_llm(
        config, prompts, state,
        chat_key,
        f"生图总提示词（场景：{job['main_scene']} / {job['sub_scene']}，比例 {job['ratio']}，渲染后直发生图模型）",
        {"image_prompt": job["image_prompt"]},
        feedback,
    )
    if isinstance(new, dict):
        new = new.get("image_prompt", "")
    if not str(new).strip():
        raise ValueError("模型返回的提示词为空")

    def mut(s):
        js = s.get("jobs", [])
        if i < len(js):
            js[i]["image_prompt"] = str(new).strip()
            js[i]["rev"] = js[i].get("rev", 0) + 1
        chats = s.setdefault("chats", {})
        chats.setdefault(chat_key, []).extend(
            [{"role": "user", "content": feedback}, {"role": "assistant", "content": "✅ 已按意见修改"}]
        )

    return runstate.update(run_dir, mut)


def refine_job_copies(config, prompts, run_dir: Path, i: int, feedback: str) -> dict:
    state = runstate.load(run_dir) or runstate.default_state()
    jobs = state.get("jobs", [])
    if not (0 <= i < len(jobs)):
        raise IndexError("job 下标越界")
    job = jobs[i]
    g = int(state.get("jobs_gen", 0))
    chat_key = f"chat_copies_{g}_{i}"
    images = None
    p = Path(job.get("image_path", ""))
    if job.get("image_path") and p.exists():
        images = [llm.vision_image(p.read_bytes())]
    new = refine_via_llm(
        config, prompts, state,
        chat_key,
        f"广告标题文案（场景：{job['main_scene']} / {job['sub_scene']}，配图见附图）",
        {"copies": job["copies"]},
        feedback,
        images=images,
    )
    if isinstance(new, dict):
        new = new.get("copies", [])
    if not isinstance(new, list) or not new:
        raise ValueError("模型返回的文案列表为空或格式不对")

    def mut(s):
        js = s.get("jobs", [])
        if i < len(js):
            js[i]["copies"] = new
            js[i]["rev"] = js[i].get("rev", 0) + 1
        chats = s.setdefault("chats", {})
        chats.setdefault(chat_key, []).extend(
            [{"role": "user", "content": feedback}, {"role": "assistant", "content": "✅ 已按意见修改"}]
        )

    return runstate.update(run_dir, mut)


# ---------------------------------------------------------------- 生图提交
def _submit_to_pipeline(config, prompts, run_dir: Path, state: dict, masters: list, derived: list) -> None:
    ok = bg.submit_image_generation(
        config, prompts, run_dir, masters, derived,
        state.get("ref_images", []), state.get("style_images", []), state.get("logo_images", []),
    )
    if not ok:
        raise RuntimeError("本任务已有后台任务或图片修改在运行，请稍后再试")


def start_generation(config, prompts, run_dir: Path) -> dict:
    """勾选场景一键生图：重建 jobs（jobs_gen+1，清理上一批 job 的对话历史）并提交后台管线。
    返回最新 state。冲突（管线/修改在跑）抛 RuntimeError。"""
    key = Path(run_dir).name
    if bg.is_busy(key):
        raise RuntimeError("本任务已有后台任务或图片修改在运行，请稍后再试")

    def mut(s):
        rows = [s["scenes"][i] for i in s.get("selected_scenes", []) if i < len(s.get("scenes", []))]
        if not rows:
            raise ValueError("请先勾选至少一个场景")
        s["jobs_gen"] = int(s.get("jobs_gen", 0)) + 1
        chats = s.setdefault("chats", {})
        for k in [k for k in list(chats) if k.startswith(("chat_prompt_", "chat_image_", "chat_copies_"))]:
            del chats[k]
        s["jobs"] = build_jobs(prompts, s, rows)

    state = runstate.update(run_dir, mut)
    masters = [i for i, j in enumerate(state["jobs"]) if not j.get("derived_from")]
    derived = [i for i, j in enumerate(state["jobs"]) if j.get("derived_from")]
    _submit_to_pipeline(config, prompts, run_dir, state, masters, derived)
    return state


def pending_indices(state: dict) -> tuple:
    """待生成/失败可重试的下标：(母版列表, 派生列表)。与 Streamlit 页逻辑一致。"""
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
    state = runstate.load(run_dir) or runstate.default_state()
    masters, derived = pending_indices(state)
    if not masters and not derived:
        return 0
    _submit_to_pipeline(config, prompts, run_dir, state, masters, derived)
    return len(masters) + len(derived)


def regenerate_job(config, prompts, run_dir: Path, i: int) -> None:
    """单张重生成：母版走生图，派生图走母版改尺寸。"""
    state = runstate.load(run_dir) or runstate.default_state()
    jobs = state.get("jobs", [])
    if not (0 <= i < len(jobs)):
        raise IndexError("job 下标越界")
    if jobs[i].get("derived_from"):
        _submit_to_pipeline(config, prompts, run_dir, state, [], [i])
    else:
        _submit_to_pipeline(config, prompts, run_dir, state, [i], [])


# ---------------------------------------------------------------- 图片对话修改（后台并发）
def submit_image_edit(config, prompts, run_dir: Path, i: int, feedback: str) -> None:
    """提交单张图后台修改，并把对话写进 state.chats（用户意见 + 排队回执）。"""
    state = runstate.load(run_dir) or runstate.default_state()
    jobs = state.get("jobs", [])
    if not (0 <= i < len(jobs)):
        raise IndexError("job 下标越界")
    if not has_image(jobs[i]):
        raise RuntimeError("找不到当前图片文件")
    if not bg.submit_image_edit(config, prompts, run_dir, i, feedback):
        raise RuntimeError("这张图已有修改在进行中，或本任务后台管线正在运行")
    g = int(state.get("jobs_gen", 0))
    append_chat(
        run_dir, f"chat_image_{g}_{i}",
        {"role": "user", "content": feedback},
        {"role": "assistant", "content": "🕐 已提交后台修改，完成后图片自动更新；期间可继续修改其它图或做别的操作"},
    )


def ack_image_edit(run_dir: Path, i: int) -> dict | None:
    """收割一张图的修改结果：清除状态记录并把结果写进对话历史。
    返回被清除的状态（{state, error}），无待收割记录返回 None。"""
    key = Path(run_dir).name
    st = bg.edit_status(key).get(i)
    if not st or st["state"] not in ("finished", "failed"):
        return None
    bg.clear_edit(key, i)
    state = runstate.load(run_dir) or runstate.default_state()
    g = int(state.get("jobs_gen", 0))
    msg = "✅ 修改完成，图片已更新" if st["state"] == "finished" else f"❌ 修改失败：{st['error']}"
    append_chat(run_dir, f"chat_image_{g}_{i}", {"role": "assistant", "content": msg})
    return st


# ---------------------------------------------------------------- 图片版本历史
def goto_version(run_dir: Path, i: int, new_idx: int) -> dict:
    """把第 i 张图切到版本 new_idx。锁内改盘（与并发的图片修改互不覆盖），返回最新 state。
    与 Streamlit 版一致：母版换版本后派生图失效待重做、copies 清空。"""
    hit = {}

    def mut(state):
        jobs = state.get("jobs", [])
        if i >= len(jobs):
            return
        job = jobs[i]
        if not runstate.goto_image_version(run_dir, job, new_idx):
            return
        job["rev"] = job.get("rev", 0) + 1
        job["copies"] = []
        if not job.get("derived_from"):
            for other in jobs:
                if other is not job and other.get("derived_from") == job["filename"]:
                    other["image_path"] = ""
                    other["copies"] = []
                    other["has_prev"] = False
        hit["filename"] = job["filename"]

    state = runstate.update(run_dir, mut)
    if not hit:
        raise FileNotFoundError("该版本的图片文件不存在或下标越界")
    return state


# ---------------------------------------------------------------- 文案
def start_copywriting(config, prompts, run_dir: Path, title_count: int | None = None) -> int:
    """对已出图且无文案的 job 批量生成文案（后台），返回提交张数。"""
    state = runstate.load(run_dir) or runstate.default_state()
    need = [i for i, j in enumerate(state.get("jobs", [])) if has_image(j) and not j.get("copies")]
    if not need:
        return 0
    count = int(title_count or state.get("title_count", 3))
    if title_count is not None and int(title_count) != int(state.get("title_count", 3)):
        runstate.update(run_dir, lambda s: s.__setitem__("title_count", int(title_count)))
    ok = bg.submit_copywriting(config, prompts, run_dir, need, count, state.get("product_info", ""))
    if not ok:
        raise RuntimeError("本任务已有后台任务在运行，请稍后再试")
    return len(need)


# ---------------------------------------------------------------- 导出
def export_run(run_dir: Path) -> dict:
    """导出交付表 xlsx，并打包 交付包.zip（图片 + manifest + 交付表），返回文件路径。"""
    state = runstate.load(run_dir) or runstate.default_state()
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
        manifest = Path(run_dir) / "manifest.json"
        if manifest.exists():
            zf.write(manifest, "manifest.json")
        zf.write(xlsx_path, xlsx_path.name)
    return {"xlsx": str(xlsx_path), "zip": str(zip_path), "images": len(exportable)}

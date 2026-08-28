"""后台任务执行器：生图 / 批量文案 两类耗时管线。

- 模块级单例线程池，整个 Streamlit 进程共享（页面重跑不会重建）；
- 线程内不访问 st.session_state（CLAUDE.md 决策 11）：入参在主线程取好传入，
  进度写本模块状态表，结果经 runstate.update() 锁内逐条落盘 state.json/manifest/数据库；
- 每个 run 同一时间只允许一个后台任务（submit 拒绝重复提交），UI 侧据此锁定该任务的编辑区；
- 「后台」指不阻塞页面、切任务/刷新页面不中断；关闭 Streamlit 进程则任务终止，
  已完成的子项均已落盘，重启后重新提交只补缺。
"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import db, imagen, llm, runstate, store
from core.logger import get_logger
from core.prompts import render

log = get_logger("tasks")

KIND_LABELS = {"prompts": "批量生成提示词", "images": "生图", "copies": "批量生成文案"}

_executor = ThreadPoolExecutor(max_workers=8)
_status: dict = {}
_guard = threading.Lock()


def status(run_key: str) -> dict | None:
    with _guard:
        s = _status.get(run_key)
        return dict(s) if s else None


def is_running(run_key: str) -> bool:
    s = status(run_key)
    return bool(s and s["state"] == "running")


def mark_consumed(run_key: str) -> None:
    with _guard:
        if run_key in _status:
            _status[run_key]["consumed"] = True


def _set(run_key: str, **kw) -> None:
    with _guard:
        if run_key in _status:
            _status[run_key].update(kw)


def _add_error(run_key: str, msg: str) -> None:
    log.error("run=%s %s", run_key, msg)
    with _guard:
        if run_key in _status:
            _status[run_key]["errors"].append(msg)


def _submit(kind: str, run_dir: Path, fn) -> bool:
    key = Path(run_dir).name
    with _guard:
        cur = _status.get(key)
        if cur and cur["state"] == "running":
            return False
        _status[key] = {
            "kind": kind, "state": "running", "done": 0, "total": 0,
            "text": "排队中…", "errors": [], "consumed": False,
        }

    def wrap():
        log.info("后台任务开始 kind=%s run=%s", kind, key)
        try:
            fn()
            _set(key, state="finished")
            s = status(key) or {}
            log.info("后台任务完成 kind=%s run=%s done=%s/%s 失败项=%d",
                     kind, key, s.get("done"), s.get("total"), len(s.get("errors", [])))
        except Exception as e:  # noqa: BLE001 —— 后台线程不得抛出到无人处
            log.exception("后台任务异常终止 kind=%s run=%s", kind, key)
            _add_error(key, f"任务异常终止：{type(e).__name__}: {e}")
            _set(key, state="failed")

    _executor.submit(wrap)
    return True


def _apply_image(run_dir: Path, i: int, png: bytes, image_prompt=None) -> None:
    """写盘并更新第 i 个 job；若为母版，其派生图失效待重做（与前台逻辑一致）。"""

    def mut(state):
        jobs = state.get("jobs", [])
        if i >= len(jobs):
            return
        job = jobs[i]
        path = store.save_image(Path(run_dir), job["filename"], png)
        job["image_path"] = str(path)
        job["copies"] = []
        job["rev"] = job.get("rev", 0) + 1
        job["has_prev"] = False
        if image_prompt is not None:
            job["image_prompt"] = image_prompt
        if not job.get("derived_from"):
            for other in jobs:
                if other is not job and other.get("derived_from") == job["filename"]:
                    other["image_path"] = ""
                    other["copies"] = []
                    other["has_prev"] = False

    state = runstate.update(run_dir, mut)
    jobs = state.get("jobs", [])
    if i < len(jobs):
        db.mark_scene_has_image(state.get("product_info", ""), jobs[i]["main_scene"], jobs[i]["sub_scene"])


# ---------------------------------------------------------------- 三条管线
def _prompt_vars(product_info: str, row: dict, ratio: str) -> dict:
    """场景行 → 提示词生成模板的变量；老格式场景（无 detail）逐项回退。"""
    d = row.get("detail") or {}
    return {
        "product_info": product_info,
        "main_scene": row.get("main_scene", ""),
        "sub_scene": row.get("sub_scene", ""),
        "audience": d.get("audience", ""),
        "selling_point": d.get("selling_point") or d.get("product_use") or row.get("description", ""),
        "visual_brief": d.get("visual_brief") or row.get("description", ""),
        "aspect_ratio": ratio,
        "headline": d.get("headline") or d.get("headline_angle", ""),
        "subheadline": d.get("subheadline", ""),
        "cta": d.get("cta", ""),
    }


def _new_job(row: dict, ratio: str, image_prompt: str, filename: str, derived_from: str) -> dict:
    return {
        "main_scene": row.get("main_scene", ""),
        "sub_scene": row.get("sub_scene", ""),
        "sub_scene_desc": row.get("description", "") or row.get("sub_scene_desc", ""),
        "ratio": ratio,
        "image_prompt": image_prompt,
        "filename": filename,
        "image_path": "",
        "copies": [],
        "derived_from": derived_from,
        "rev": 0,
        "has_prev": False,
    }


def submit_prompt_generation(config, prompts, run_dir, product_info, selected_rows, dual_mode, ratios) -> bool:
    """Step 2：把场景挖掘的全部变量喂给 Claude，为每个选中场景批量生成海报生图提示词，
    逐场景追加进 state["jobs"]。调用前 UI 须已把 state["jobs"] 清空并 persist。"""
    tpl = prompts["image_prompt_gen"]["template"]
    key = Path(run_dir).name

    def work():
        master_ratios = ["4:5"] if dual_mode else list(ratios)
        total = len(selected_rows) * len(master_ratios)
        _set(key, total=total, text="生成提示词…")
        done = 0
        for row in selected_rows:
            master_filename = ""
            new_jobs = []
            for ratio in master_ratios:
                done += 1
                _set(key, done=done, text=f"生成提示词 {done}/{total}：{row['sub_scene']}（{ratio}）")
                try:
                    prompt = render(tpl, _prompt_vars(product_info, row, ratio))
                    result = llm.call_json(config, prompt)
                    image_prompt = result.get("image_prompt", "")
                except Exception as e:  # noqa: BLE001
                    _add_error(key, f"「{row['sub_scene']}（{ratio}）」提示词生成失败：{e}")
                    image_prompt = ""
                master_filename = store.image_filename(row["main_scene"], row["sub_scene"], ratio)
                new_jobs.append(_new_job(row, ratio, image_prompt, master_filename, ""))
            if dual_mode and master_filename:
                new_jobs.append(
                    _new_job(row, "1:1", "", store.image_filename(row["main_scene"], row["sub_scene"], "1:1"), master_filename)
                )
            runstate.update(run_dir, lambda s, nj=new_jobs: s["jobs"].extend(nj))

    return _submit("prompts", run_dir, work)


def _ref_bundle(run_dir, ref_rel_paths, style_rel_paths, logo_rel_paths) -> tuple:
    """按「产品图 → 风格图 → Logo」固定顺序组装参考图，并生成随提示词追加的英文说明。

    只有产品参考图时不追加说明（保持决策 15 的原样直发）；出现风格图或 Logo 时
    必须用说明告诉模型各张参考图的身份，否则模型无法区分。"""
    product = runstate.load_ref_images(run_dir, ref_rel_paths)
    style = runstate.load_ref_images(run_dir, style_rel_paths)
    logo = runstate.load_ref_images(run_dir, logo_rel_paths)
    images = product + style + logo
    if not (style or logo):
        return images, ""

    def _rng(start: int, count: int) -> str:
        return f"image {start}" if count == 1 else f"images {start}-{start + count - 1}"

    lines = ["Reference images are provided in this exact order:"]
    idx = 1
    if product:
        lines.append(
            f"- {_rng(idx, len(product))}: product photo(s). Reproduce this exact product faithfully in the poster."
        )
        idx += len(product)
    if style:
        lines.append(
            f"- {_rng(idx, len(style))}: style reference poster(s). Match their overall visual style, "
            "color palette, lighting, mood, composition feel and typography treatment — but do NOT copy "
            "their content, subjects, products or text."
        )
        idx += len(style)
    if logo:
        lines.append(
            f"- {_rng(idx, len(logo))}: the brand logo. Place this exact logo, unchanged, in a clearly "
            "visible corner of the poster: legible, correct colors and proportions, not redrawn, distorted "
            "or recolored, with clean space around it. Do not invent any other logo."
        )
    return images, "\n\n" + "\n".join(lines)


def submit_image_generation(
    config, prompts, run_dir, master_indices, derived_indices,
    ref_rel_paths, style_rel_paths=None, logo_rel_paths=None,
) -> bool:
    """Step 3：母版并行生图（不设并发上限，决策 11），派生图依赖母版成品串行改尺寸。
    job 的 image_prompt（Step 2 生成的海报提示词）即最终提示词，直接发给生图模型；
    有风格/Logo 参考图时在发送瞬间追加参考图身份说明（job.image_prompt 本身不变）。"""
    adapt_tpl = prompts["ratio_adapt"]["template"]
    key = Path(run_dir).name

    def work():
        state = runstate.load(run_dir) or runstate.default_state()
        jobs = state.get("jobs", [])
        ref_images, ref_note = _ref_bundle(run_dir, ref_rel_paths, style_rel_paths or [], logo_rel_paths or [])
        total = len(master_indices) + len(derived_indices)
        _set(key, total=total, text="生图中…")
        done = 0

        tasks = []
        for i in master_indices:
            job = jobs[i]
            tasks.append((i, job["image_prompt"] + ref_note, job["ratio"]))
        if tasks:
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futures = {
                    pool.submit(imagen.generate_image, config, p, r, ref_images): i
                    for i, p, r in tasks
                }
                for fut in as_completed(futures):
                    i = futures[fut]
                    job = jobs[i]
                    done += 1
                    _set(key, done=done, text=f"生图 {done}/{total}：{job['sub_scene']}（{job['ratio']}）")
                    try:
                        _apply_image(run_dir, i, fut.result())
                    except Exception as e:  # noqa: BLE001
                        _add_error(key, f"「{job['sub_scene']}（{job['ratio']}）」生图失败：{e}")

        for i in derived_indices:
            # 重新读盘：母版结果由 _apply_image 写入了 state
            state = runstate.load(run_dir) or runstate.default_state()
            jobs = state.get("jobs", [])
            job = jobs[i]
            done += 1
            _set(key, done=done, text=f"改尺寸 {done}/{total}：{job['sub_scene']}（{job['ratio']}）")
            try:
                master = next(
                    (m for m in jobs if not m.get("derived_from") and m["filename"] == job.get("derived_from")),
                    None,
                )
                if not master or not master.get("image_path") or not Path(master["image_path"]).exists():
                    raise RuntimeError("4:5 母版尚未生成成功，无法改尺寸")
                master_bytes = Path(master["image_path"]).read_bytes()
                adapt_prompt = render(adapt_tpl, {"target_ratio": job["ratio"]})
                png = imagen.edit_image(config, master_bytes, adapt_prompt, job["ratio"])
                _apply_image(run_dir, i, png, image_prompt=master["image_prompt"])
            except Exception as e:  # noqa: BLE001
                _add_error(key, f"「{job['sub_scene']}（{job['ratio']}）」改尺寸失败：{e}")

    return _submit("images", run_dir, work)


def submit_copywriting(config, prompts, run_dir, indices, title_count, product_info) -> bool:
    """Step 4：对已出图的 job 逐张看图写文案。"""
    tpl = prompts["copywriting"]["template"]
    key = Path(run_dir).name

    def work():
        state = runstate.load(run_dir) or runstate.default_state()
        jobs = state.get("jobs", [])
        total = len(indices)
        _set(key, total=total, text="生成文案…")
        for n, i in enumerate(indices, start=1):
            job = jobs[i]
            _set(key, done=n, text=f"文案 {n}/{total}：{job['sub_scene']}（{job['ratio']}）")
            try:
                img_path = Path(job.get("image_path", ""))
                if not job.get("image_path") or not img_path.exists():
                    raise RuntimeError("图片文件不存在")
                prompt = render(
                    tpl,
                    {
                        "product_info": product_info,
                        "main_scene": job["main_scene"],
                        "sub_scene": job["sub_scene"],
                        "title_count": int(title_count),
                    },
                )
                result = llm.call_json(config, prompt, images=[(img_path.read_bytes(), "image/png")])
                copies = result.get("copies", [])

                def mut(state, i=i, copies=copies):
                    j = state["jobs"][i]
                    j["copies"] = copies
                    j["rev"] = j.get("rev", 0) + 1

                runstate.update(run_dir, mut)
            except Exception as e:  # noqa: BLE001
                _add_error(key, f"「{job['sub_scene']}（{job['ratio']}）」文案生成失败：{e}")

    return _submit("copies", run_dir, work)

"""后台任务执行器：生图 / 批量文案 两类耗时管线 + 单张图片修改（并发）。

- 模块级单例线程池，整个服务进程共享（必须单 worker，决策 13）；
- 入参在主线程取好传入，进度写本模块状态表，结果经 state_store 锁内逐条落库
  （PostgreSQL，决策 20 第二阶段）；
- 每个 run 同一时间只允许一个后台**管线**（submit 拒绝重复提交），UI 侧据此锁定该任务的编辑区；
- 单张图片修改（submit_image_edit）是独立轻量通道：不锁整页、多张图可并发修改
  （同一张图不允许重复提交），与管线互斥——管线运行中拒绝提交修改，反之亦然；
- 「后台」指不阻塞页面、切任务/刷新页面不中断；关闭服务进程则任务终止，
  已完成的子项均已落库，重启后重新提交只补缺。
"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import imagen, llm
from core.logger import get_logger
from core.prompts import render
from server.services import scene_lib_store
from server.services import state_store as st

log = get_logger("tasks")

KIND_LABELS = {"images": "生图", "copies": "批量生成文案"}

_executor = ThreadPoolExecutor(max_workers=8)
_status: dict = {}
_edit_status: dict = {}  # run_key -> {job_index: {"state": running/finished/failed, "error": str}}
_guard = threading.Lock()


def status(run_key: str) -> dict | None:
    with _guard:
        s = _status.get(run_key)
        return dict(s) if s else None


def is_running(run_key: str) -> bool:
    s = status(run_key)
    return bool(s and s["state"] == "running")


def edit_status(run_key: str) -> dict:
    """该 run 所有图片修改的状态快照 {job_index: {state, error}}。"""
    with _guard:
        return {i: dict(s) for i, s in (_edit_status.get(run_key) or {}).items()}


def edits_running(run_key: str) -> bool:
    with _guard:
        return any(s["state"] == "running" for s in (_edit_status.get(run_key) or {}).values())


def is_busy(run_key: str) -> bool:
    """管线或任一图片修改在跑。UI 的 persist 守卫用（决策 12 延伸）。"""
    return is_running(run_key) or edits_running(run_key)


def clear_edit(run_key: str, i: int) -> None:
    """UI 收割完某张图的修改结果后清除其状态记录。"""
    with _guard:
        (_edit_status.get(run_key) or {}).pop(i, None)


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
        # 与单张图片修改互斥：修改跑到一半时启动管线会用旧下标/旧图互相覆盖
        if any(s["state"] == "running" for s in (_edit_status.get(key) or {}).values()):
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
    """落库落盘第 i 个 job 的新图（含版本历史；母版出新图时派生图失效待重做），
    并给场景库打 has_image 标签（失败只记日志，不影响生图）。"""
    info = st.apply_image_result(run_dir, i, png, image_prompt=image_prompt)
    scene_lib_store.mark_scene_has_image(info["product_id"], info["main_scene"], info["sub_scene"])


# ---------------------------------------------------------------- 两条管线
def new_job(row: dict, ratio: str, image_prompt: str, filename: str, derived_from: str) -> dict:
    """构造一个 job（一张图）。工作流页勾选场景后本地渲染「生图总提示词」时调用。"""
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
    }


def _ref_bundle(run_dir, ref_rel_paths, style_rel_paths, logo_rel_paths) -> tuple:
    """按「产品图 → 风格图 → Logo」固定顺序组装参考图，并生成随提示词追加的英文说明。

    返回 (images, note, has_style)。只有产品参考图时不追加说明（保持决策 15 的
    原样直发）；出现风格图或 Logo 时必须用说明告诉模型各张参考图的身份，否则模型
    无法区分。has_style 供生图管线在发送瞬间填充提示词里的 {reference_style_image}。"""
    product = st.load_ref_images(run_dir, ref_rel_paths)
    style = st.load_ref_images(run_dir, style_rel_paths)
    logo = st.load_ref_images(run_dir, logo_rel_paths)
    images = product + style + logo
    if not (style or logo):
        return images, "", False

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
    return images, "\n\n" + "\n".join(lines), bool(style)


def submit_image_generation(
    config, prompts, run_dir, master_indices, derived_indices,
    ref_rel_paths, style_rel_paths=None, logo_rel_paths=None,
) -> bool:
    """Step 2 生图：母版并行生图（不设并发上限，决策 11），派生图依赖母版成品串行改尺寸。
    job 的 image_prompt（场景变量填入「生图总提示词」的渲染结果）即最终提示词，直接发给
    生图模型；发送瞬间做两件事、job.image_prompt 本身均不变：
    ① 按当时是否带风格参考图填充提示词中的 {reference_style_image} 占位符；
    ② 有风格/Logo 参考图时在末尾追加参考图身份英文说明（决策 15 例外条款）。"""
    adapt_tpl = prompts["ratio_adapt"]["template"]
    key = Path(run_dir).name

    def work():
        state = st.load(run_dir) or {}
        jobs = state.get("jobs", [])
        ref_images, ref_note, has_style = _ref_bundle(
            run_dir, ref_rel_paths, style_rel_paths or [], logo_rel_paths or []
        )
        style_txt = (
            "已随本提示词一并附上参考风格图（各张参考图的身份见文末英文说明）"
            if has_style
            else "未提供（跳过参考风格分析，直接根据场景变量设计）"
        )
        total = len(master_indices) + len(derived_indices)
        _set(key, total=total, text="生图中…")
        done = 0

        tasks = []
        for i in master_indices:
            job = jobs[i]
            prompt = job["image_prompt"].replace("{reference_style_image}", style_txt) + ref_note
            tasks.append((i, prompt, job["ratio"]))
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
            state = st.load(run_dir) or {}
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
    """Step 3：对已出图的 job 看图写文案。每张图相互独立，全部并行提交
    （与生图同策略，决策 11）；图片压缩后再发送（llm.vision_image），
    原先逐张串行 + 发原图 PNG，3 张图要 14 分钟（2026-08-28 实测）。"""
    tpl = prompts["copywriting"]["template"]
    key = Path(run_dir).name

    def work():
        state = st.load(run_dir) or {}
        jobs = state.get("jobs", [])
        total = len(indices)
        _set(key, total=total, text="生成文案…")

        def one(i):
            job = jobs[i]
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
            result = llm.call_json(config, prompt, images=[llm.vision_image(img_path.read_bytes())])
            return result.get("copies", [])

        done = 0
        with ThreadPoolExecutor(max_workers=total) as pool:
            futures = {pool.submit(one, i): i for i in indices}
            for fut in as_completed(futures):
                i = futures[fut]
                job = jobs[i]
                done += 1
                _set(key, done=done, text=f"文案 {done}/{total}：{job['sub_scene']}（{job['ratio']}）")
                try:
                    st.set_job_copies(run_dir, i, fut.result())
                except Exception as e:  # noqa: BLE001
                    _add_error(key, f"「{job['sub_scene']}（{job['ratio']}）」文案生成失败：{e}")

    return _submit("copies", run_dir, work)


def submit_image_edit(config, prompts, run_dir, i: int, feedback: str) -> bool:
    """单张图对话修改（后台并发）：不锁整页、只锁这张图，多张图可同时修改。
    管线运行中或同一张图已有修改在跑时拒绝（返回 False）。结果经 _apply_image
    落盘（含版本历史），UI 侧轮询 edit_status 收割。"""
    key = Path(run_dir).name
    with _guard:
        cur = _status.get(key)
        if cur and cur["state"] == "running":
            return False
        edits = _edit_status.setdefault(key, {})
        if edits.get(i, {}).get("state") == "running":
            return False
        edits[i] = {"state": "running", "error": ""}

    tpl = prompts["image_refine"]["template"]

    def _finish(state: str, error: str = ""):
        with _guard:
            if i in _edit_status.get(key, {}):
                _edit_status[key][i] = {"state": state, "error": error}

    def work():
        log.info("图片修改开始 run=%s job=%d", key, i)
        try:
            state = st.load(run_dir) or {}
            jobs = state.get("jobs", [])
            if i >= len(jobs):
                raise RuntimeError("任务列表已变化，找不到这张图")
            job = jobs[i]
            img_path = Path(job.get("image_path", ""))
            if not job.get("image_path") or not img_path.exists():
                raise RuntimeError("找不到当前图片文件")
            edit_prompt = render(tpl, {"feedback": feedback})
            png = imagen.edit_image(config, img_path.read_bytes(), edit_prompt, job["ratio"])
            _apply_image(run_dir, i, png)
            _finish("finished")
            log.info("图片修改完成 run=%s job=%d", key, i)
        except Exception as e:  # noqa: BLE001
            log.exception("图片修改失败 run=%s job=%d", key, i)
            _finish("failed", f"{type(e).__name__}: {e}")

    _executor.submit(work)
    return True

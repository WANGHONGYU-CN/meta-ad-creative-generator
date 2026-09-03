"""工作流操作接口：挖场景 / 生图 / 图片修改与版本 / 文案，以及各处对话修改。

后台语义与 Streamlit 版一致：
- 挖场景 / 生图 / 批量文案 都是后台执行，提交返回 202，进度经 GET /api/runs/{run}/status 轮询；
- 单张图修改是独立并发通道（多张同时改，同一张拒绝重复提交），完成后调 ack 收割；
- 对话式文字修改（场景/提示词/文案）为同步接口，前端在聊天框内等待即可。
"""
from fastapi import APIRouter, HTTPException

from core import runstate
from core import tasks as bg
from core.config import load_config
from core.prompts import load_prompts

from server import deps
from server.schemas import CopiesStart, Feedback, GotoVersion, JobPatch
from server.services import mining
from server.services import workflow as wf

router = APIRouter(prefix="/api/runs/{run}", tags=["workflow"])


def _ctx(run: str):
    run_dir = deps.get_run_dir(run)
    return run_dir, load_config(), load_prompts()


def _reject_if_busy(run: str, what: str = "操作"):
    if bg.is_busy(run) or mining.is_running(run):
        raise HTTPException(409, f"本任务有后台任务在运行，暂不能{what}，请稍后再试")


def _job_or_404(run_dir, i: int) -> dict:
    state = deps.load_state(run_dir)
    jobs = state.get("jobs", [])
    if not (0 <= i < len(jobs)):
        raise HTTPException(404, "图片不存在（下标越界）")
    return state


# ---------------------------------------------------------------- Step 1 挖场景
@router.post("/scenes/mine", status_code=202)
def mine_scenes(run: str):
    run_dir, config, prompts = _ctx(run)
    if not deps.load_state(run_dir).get("product_info", "").strip():
        raise HTTPException(400, "产品信息为空，请先填写")
    if not mining.submit(config, prompts, run_dir):
        raise HTTPException(409, "本任务已有后台任务在运行，请稍后再试")
    return {"ok": True}


@router.post("/scenes/mine/ack")
def ack_mining(run: str):
    deps.get_run_dir(run)
    mining.clear(run)
    return {"ok": True}


@router.post("/scenes/refine")
def refine_scenes(run: str, body: Feedback):
    run_dir, config, prompts = _ctx(run)
    _reject_if_busy(run, "修改场景")
    try:
        state = wf.refine_scenes(config, prompts, run_dir, body.feedback.strip())
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"state": deps.enrich_state(run, state)}


# ---------------------------------------------------------------- Step 2 生图
@router.post("/images/generate", status_code=202)
def generate_images(run: str):
    run_dir, config, prompts = _ctx(run)
    if mining.is_running(run):
        raise HTTPException(409, "场景挖掘进行中，请稍后再试")
    try:
        state = wf.start_generation(config, prompts, run_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"submitted": len(state.get("jobs", []))}


@router.post("/images/retry", status_code=202)
def retry_images(run: str):
    run_dir, config, prompts = _ctx(run)
    if mining.is_running(run):
        raise HTTPException(409, "场景挖掘进行中，请稍后再试")
    try:
        n = wf.retry_generation(config, prompts, run_dir)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if n == 0:
        raise HTTPException(400, "没有待生成/待重试的图")
    return {"submitted": n}


@router.post("/images/{i}/regenerate", status_code=202)
def regenerate_image(run: str, i: int):
    run_dir, config, prompts = _ctx(run)
    _job_or_404(run_dir, i)
    if mining.is_running(run):
        raise HTTPException(409, "场景挖掘进行中，请稍后再试")
    try:
        wf.regenerate_job(config, prompts, run_dir, i)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.patch("/jobs/{i}")
def patch_job(run: str, i: int, body: JobPatch):
    """手动编辑单张图的提示词 / 文案表格。管线运行中拒绝（与页面锁定语义一致）。"""
    run_dir = deps.get_run_dir(run)
    if bg.is_running(run):
        raise HTTPException(409, "后台任务运行中，暂不能编辑")
    _job_or_404(run_dir, i)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "没有要修改的字段")

    def mut(state):
        jobs = state.get("jobs", [])
        if i < len(jobs):
            job = jobs[i]
            if "image_prompt" in fields:
                job["image_prompt"] = str(fields["image_prompt"])
            if "copies" in fields:
                job["copies"] = fields["copies"]
            job["rev"] = job.get("rev", 0) + 1

    state = runstate.update(run_dir, mut)
    return {"job": deps.enrich_state(run, state)["jobs"][i]}


@router.post("/jobs/{i}/prompt/refine")
def refine_prompt(run: str, i: int, body: Feedback):
    run_dir, config, prompts = _ctx(run)
    _reject_if_busy(run, "修改提示词")
    _job_or_404(run_dir, i)
    try:
        state = wf.refine_job_prompt(config, prompts, run_dir, i, body.feedback.strip())
    except (ValueError, IndexError) as e:
        raise HTTPException(422, str(e))
    return {"job": deps.enrich_state(run, state)["jobs"][i]}


# ---------------------------------------------------------------- 图片修改 / 版本历史
@router.post("/jobs/{i}/image/edit", status_code=202)
def edit_image(run: str, i: int, body: Feedback):
    run_dir, config, prompts = _ctx(run)
    _job_or_404(run_dir, i)
    try:
        wf.submit_image_edit(config, prompts, run_dir, i, body.feedback.strip())
    except IndexError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/jobs/{i}/image/ack")
def ack_image_edit(run: str, i: int):
    """收割一张图的修改结果（清状态 + 写对话回执），返回该图最新数据。"""
    run_dir = deps.get_run_dir(run)
    result = wf.ack_image_edit(run_dir, i)
    if result is None:
        raise HTTPException(404, "这张图没有待收割的修改结果")
    state = deps.load_state(run_dir)
    job = deps.enrich_state(run, state)["jobs"][i] if i < len(state.get("jobs", [])) else None
    return {"result": result, "job": job}


@router.post("/jobs/{i}/image/goto")
def goto_version(run: str, i: int, body: GotoVersion):
    run_dir = deps.get_run_dir(run)
    if bg.is_running(run):
        raise HTTPException(409, "后台任务运行中，暂不能切换版本")
    _job_or_404(run_dir, i)
    try:
        state = wf.goto_version(run_dir, i, body.version)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"state": deps.enrich_state(run, state)}


# ---------------------------------------------------------------- Step 3 文案
@router.post("/copies/generate", status_code=202)
def generate_copies(run: str, body: CopiesStart):
    run_dir, config, prompts = _ctx(run)
    if mining.is_running(run) or bg.edits_running(run):
        raise HTTPException(409, "有后台任务/图片修改在运行，请稍后再试")
    try:
        n = wf.start_copywriting(config, prompts, run_dir, body.title_count)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if n == 0:
        raise HTTPException(400, "没有需要生成文案的图（均已有文案或尚未出图）")
    return {"submitted": n}


@router.post("/jobs/{i}/copies/refine")
def refine_copies(run: str, i: int, body: Feedback):
    run_dir, config, prompts = _ctx(run)
    _reject_if_busy(run, "修改文案")
    _job_or_404(run_dir, i)
    try:
        state = wf.refine_job_copies(config, prompts, run_dir, i, body.feedback.strip())
    except (ValueError, IndexError) as e:
        raise HTTPException(422, str(e))
    return {"job": deps.enrich_state(run, state)["jobs"][i]}

"""PostgreSQL 版任务状态仓库：取代 core/runstate.py 的 state.json 读写（决策 20 第二阶段）。

- 对外交换格式仍是「state dict」（product_info/scenes/selected_scenes/jobs/chats/...），
  路由与 deps.enrich_state 无需感知存储层；与旧版的刻意差异：
  chats key 去掉批次号（chat_prompt_{i} 等，i=job 的 seq），jobs_gen 概念删除
  （重建 jobs=删行重插，job 域对话随外键级联自动清理）。
- run 对外标识 = 目录名 "run_{id}"（runs.id）；图片/参考图二进制仍在 outputs/run_{id}/，
  库存相对路径。
- 写操作走每 run 进程内锁（保证「文件 ↔ 库」一致，UI 线程与后台线程共用），
  行级原子性由 DB 事务保证。单进程约束不变（决策 13）。
"""
import hashlib
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select

from core import store
from core.config import OUTPUTS_DIR
from core.logger import get_logger
from server.db.models import (
    ChatMessage, Copy, ImageVersion, Job, Product, RefAsset, Run, RunRefImage, Scene,
)
from server.db.session import get_session_factory

log = get_logger("state_store")

HIST_DIRNAME = ".hist"   # images/.hist/<文件名>/v{seq}.png 图片版本历史
HIST_LIMIT = 10          # 每张图保留的最大版本数（含当前版）
REF_DIRNAMES = {"style": "refs_style", "logo": "refs_logo"}

_RUN_NAME_RE = re.compile(r"^run_(\d+)$")
_locks: dict = {}
_locks_guard = threading.Lock()

DETAIL_TEXT_FIELDS = ("audience", "trigger", "pain_or_desire", "product_use")
SCORE_FIELDS = ("product_fit", "visual_clarity", "purchase_intent", "attention_emotion", "meta_safety")


def _session():
    return get_session_factory()()


def get_lock(run_id: int) -> threading.Lock:
    with _locks_guard:
        if run_id not in _locks:
            _locks[run_id] = threading.Lock()
        return _locks[run_id]


# ---------------------------------------------------------------- run 标识 / 目录
def run_name(run_id: int) -> str:
    return f"run_{run_id}"


def parse_run_name(name: str) -> int | None:
    m = _RUN_NAME_RE.match(str(name))
    return int(m.group(1)) if m else None


def run_dir_of(run_id: int) -> Path:
    return OUTPUTS_DIR / run_name(run_id)


def _rid(run_dir) -> int:
    rid = parse_run_name(Path(run_dir).name)
    if rid is None:
        raise ValueError(f"非法任务目录名：{Path(run_dir).name}")
    return rid


def run_exists(run_id: int) -> bool:
    with _session() as s:
        return s.get(Run, run_id) is not None


# ---------------------------------------------------------------- 产品
def list_products() -> list:
    with _session() as s:
        rows = s.execute(
            select(Product, func.count(Run.id))
            .outerjoin(Run, Run.product_id == Product.id)
            .group_by(Product.id)
            .order_by(Product.id.desc())
        ).all()
        return [
            {
                "id": p.id, "name": p.name, "info": p.info,
                "brand_name": p.brand_name, "ad_language": p.ad_language,
                "run_count": n,
            }
            for p, n in rows
        ]


def get_product(product_id: int) -> dict | None:
    with _session() as s:
        p = s.get(Product, product_id)
        if p is None:
            return None
        return {"id": p.id, "name": p.name, "info": p.info,
                "brand_name": p.brand_name, "ad_language": p.ad_language}


def create_product(name: str, info: str, brand_name: str = "", ad_language: str = "") -> dict:
    with _session() as s:
        if s.execute(select(Product).where(Product.name == name)).scalar_one_or_none():
            raise ValueError(f"产品名已存在：{name}")
        p = Product(name=name, info=info, brand_name=brand_name, ad_language=ad_language)
        s.add(p)
        s.commit()
        return {"id": p.id, "name": p.name, "info": p.info,
                "brand_name": p.brand_name, "ad_language": p.ad_language}


def patch_product(product_id: int, fields: dict) -> dict:
    with _session() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise KeyError("产品不存在")
        if "name" in fields and fields["name"] != p.name:
            if s.execute(select(Product).where(Product.name == fields["name"])).scalar_one_or_none():
                raise ValueError(f"产品名已存在：{fields['name']}")
            p.name = fields["name"]
        for k in ("info", "brand_name", "ad_language"):
            if k in fields:
                setattr(p, k, fields[k])
        s.commit()
        return {"id": p.id, "name": p.name, "info": p.info,
                "brand_name": p.brand_name, "ad_language": p.ad_language}


# ---------------------------------------------------------------- run 创建 / 列表
def create_run(
    product_id: int,
    brand_name: str | None = None,
    ad_language: str | None = None,
    ratio_choice: str = "",
    title_count: int = 3,
) -> str:
    """新建任务，返回对外任务名 run_{id}；brand/语言缺省继承产品默认值。"""
    with _session() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise KeyError("产品不存在")
        run = Run(
            product_id=product_id,
            brand_name=p.brand_name if brand_name is None else brand_name,
            ad_language=p.ad_language if ad_language is None else ad_language,
            ratio_choice=ratio_choice,
            title_count=title_count,
        )
        s.add(run)
        s.commit()
        rid = run.id
    (run_dir_of(rid) / "images").mkdir(parents=True, exist_ok=True)
    return run_name(rid)


def list_runs_overview() -> list:
    """任务列表（新→旧）：name / product_info / updated_at / job_count。"""
    with _session() as s:
        rows = s.execute(
            select(Run, Product.info, func.count(Job.id))
            .join(Product, Product.id == Run.product_id)
            .outerjoin(Job, Job.run_id == Run.id)
            .group_by(Run.id, Product.info)
            .order_by(Run.updated_at.desc(), Run.id.desc())
        ).all()
        return [
            {
                "name": run_name(r.id),
                "product_info": info,
                "updated_at": r.updated_at.astimezone().isoformat(timespec="seconds") if r.updated_at else "",
                "job_count": n,
            }
            for r, info, n in rows
        ]


# ---------------------------------------------------------------- state bundle 组装
def _scene_row(sc: Scene) -> dict:
    row = {"main_scene": sc.main_scene, "sub_scene": sc.sub_scene, "description": sc.description}
    detail = {k: getattr(sc, k) for k in DETAIL_TEXT_FIELDS if getattr(sc, k)}
    scores = {k: getattr(sc, k) for k in SCORE_FIELDS if getattr(sc, k) is not None}
    if scores:
        detail["score_breakdown"] = scores
    if sc.total_score is not None:
        detail["total_score"] = sc.total_score
    if detail:
        row["detail"] = detail
    return row


def _job_dict(run_dir: Path, job: Job, versions: list, master_filename: str) -> dict:
    hist = [v.rel_path for v in versions]
    hist_idx = next((k for k, v in enumerate(versions) if v.seq == job.cur_version_seq), None)
    if hist_idx is None:
        hist_idx = len(hist) - 1
    return {
        "main_scene": job.main_scene,
        "sub_scene": job.sub_scene,
        "sub_scene_desc": job.sub_scene_desc,
        "ratio": job.ratio,
        "image_prompt": job.image_prompt,
        "filename": job.filename,
        "image_path": str(run_dir / job.image_rel_path) if job.image_rel_path else "",
        "copies": None,  # 由调用方填充
        "derived_from": master_filename,
        "rev": job.rev,
        "hist": hist,
        "hist_idx": hist_idx,
    }


def _load_in_session(s, run_id: int) -> dict | None:
    run = s.get(Run, run_id)
    if run is None:
        return None
    product = s.get(Product, run.product_id)
    rdir = run_dir_of(run_id)

    scenes = s.execute(select(Scene).where(Scene.run_id == run_id).order_by(Scene.seq)).scalars().all()
    jobs = s.execute(select(Job).where(Job.run_id == run_id).order_by(Job.seq)).scalars().all()
    job_by_id = {j.id: j for j in jobs}
    seq_by_id = {j.id: j.seq for j in jobs}

    versions: dict = {}
    if jobs:
        for v in s.execute(
            select(ImageVersion).where(ImageVersion.job_id.in_(job_by_id)).order_by(ImageVersion.seq)
        ).scalars():
            versions.setdefault(v.job_id, []).append(v)
    copies: dict = {}
    if jobs:
        for c in s.execute(
            select(Copy).where(Copy.job_id.in_(job_by_id)).order_by(Copy.seq)
        ).scalars():
            copies.setdefault(c.job_id, []).append(
                {"angle": c.angle, "headline": c.headline, "primary_text": c.primary_text}
            )

    job_rows = []
    for j in jobs:
        master = job_by_id.get(j.derived_from_job_id) if j.derived_from_job_id else None
        row = _job_dict(rdir, j, versions.get(j.id, []), master.filename if master else "")
        row["copies"] = copies.get(j.id, [])
        job_rows.append(row)

    refs: dict = {"style": [], "logo": []}
    for r in s.execute(
        select(RunRefImage).where(RunRefImage.run_id == run_id).order_by(RunRefImage.kind, RunRefImage.seq)
    ).scalars():
        refs.setdefault(r.kind, []).append(r.rel_path)

    chats: dict = {}
    for m in s.execute(
        select(ChatMessage).where(ChatMessage.run_id == run_id).order_by(ChatMessage.id)
    ).scalars():
        if m.scope == "scenes":
            key = "chat_scenes"
        else:
            seq = seq_by_id.get(m.job_id)
            if seq is None:
                continue  # 孤儿消息（job 已删，理论上级联不会出现）
            key = f"chat_{m.scope}_{seq}"
        chats.setdefault(key, []).append({"role": m.role, "content": m.content})

    return {
        "product_id": run.product_id,
        "product_name": product.name if product else "",
        "product_info": product.info if product else "",
        "brand_name": run.brand_name,
        "ad_language": run.ad_language,
        "ratio_choice": run.ratio_choice,
        "title_count": run.title_count,
        "scenes": [_scene_row(sc) for sc in scenes],
        "selected_scenes": [i for i, sc in enumerate(scenes) if sc.is_selected],
        "jobs": job_rows,
        "ref_images": [],  # 旧产品参考图概念已随老数据废弃，字段保留供前端/enrich 兼容
        "style_images": refs.get("style", []),
        "logo_images": refs.get("logo", []),
        "chats": chats,
    }


def load(run_dir) -> dict | None:
    """按目录（或 run_{id} 名）载入完整 state bundle；run 不存在返回 None。"""
    rid = parse_run_name(Path(run_dir).name)
    if rid is None:
        return None
    with _session() as s:
        return _load_in_session(s, rid)


def _touch(s, run_id: int) -> None:
    run = s.get(Run, run_id)
    if run is not None:
        run.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------- run 字段 / 勾选
def patch_run_fields(run_dir, fields: dict) -> dict:
    """字段级修改：product_info 落在产品行，其余落在 run 行；返回最新 bundle。"""
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        run = s.get(Run, rid)
        if run is None:
            raise KeyError("任务不存在")
        if "product_info" in fields:
            product = s.get(Product, run.product_id)
            product.info = str(fields["product_info"])
        for k in ("brand_name", "ad_language", "ratio_choice"):
            if k in fields:
                setattr(run, k, str(fields[k]))
        if "title_count" in fields:
            run.title_count = int(fields["title_count"])
        if "selected_scenes" in fields:
            picked = {int(i) for i in fields["selected_scenes"]}
            scenes = s.execute(select(Scene).where(Scene.run_id == rid).order_by(Scene.seq)).scalars().all()
            for i, sc in enumerate(scenes):
                sc.is_selected = i in picked
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


# ---------------------------------------------------------------- 场景
def _scene_fields(row: dict) -> dict:
    d = row.get("detail") or {}
    scores = d.get("score_breakdown") or {}

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return {
        "main_scene": str(row.get("main_scene", "")),
        "sub_scene": str(row.get("sub_scene", "")),
        "description": str(row.get("description", "")),
        **{k: str(d.get(k, "") or "") for k in DETAIL_TEXT_FIELDS},
        **{k: _int(scores.get(k)) for k in SCORE_FIELDS},
        "total_score": _int(d.get("total_score")),
    }


def replace_scenes(
    run_dir, rows: list, *, clear_jobs: bool = False, clear_scene_chat: bool = False,
    select_all: bool = False,
) -> dict:
    """整批替换场景（挖掘/对话修改结果），勾选清零（或全选）；可连带清空 jobs 与场景对话。"""
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        s.execute(delete(Scene).where(Scene.run_id == rid))
        for i, row in enumerate(rows):
            s.add(Scene(run_id=rid, seq=i, is_selected=select_all, **_scene_fields(row)))
        if clear_jobs:
            s.execute(delete(Job).where(Job.run_id == rid))  # copies/versions/job 域对话级联删除
        if clear_scene_chat:
            s.execute(delete(ChatMessage).where(
                ChatMessage.run_id == rid, ChatMessage.scope == "scenes"))
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


# ---------------------------------------------------------------- jobs
def rebuild_jobs(run_dir, job_dicts: list) -> dict:
    """整批重建 jobs（勾选场景一键生图）。旧 job 行连带 copies/版本/对话级联删除；
    旧图片文件保留在磁盘（与旧版行为一致），新 job 未出图前 image_url 为空。"""
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        s.execute(delete(Job).where(Job.run_id == rid))
        s.flush()
        seen: set = set()
        rows: list = []
        for i, jd in enumerate(job_dicts):
            fn = jd.get("filename") or f"job_{i}.png"
            if fn in seen:  # 同名场景撞文件名：库有 UNIQUE(run_id, filename)，加序号避让
                stem, dot, ext = fn.rpartition(".")
                fn = f"{stem}_{i}.{ext}" if dot else f"{fn}_{i}"
            seen.add(fn)
            row = Job(
                run_id=rid, seq=i,
                main_scene=jd.get("main_scene", ""), sub_scene=jd.get("sub_scene", ""),
                sub_scene_desc=jd.get("sub_scene_desc", ""), ratio=jd.get("ratio", ""),
                image_prompt=jd.get("image_prompt", ""), filename=fn,
            )
            s.add(row)
            rows.append((row, jd.get("derived_from", "")))
        s.flush()
        by_filename = {r.filename: r for r, _ in rows}
        for row, derived_from in rows:
            if derived_from and derived_from in by_filename:
                row.derived_from_job_id = by_filename[derived_from].id
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


def _job_by_seq(s, run_id: int, seq: int) -> Job | None:
    return s.execute(
        select(Job).where(Job.run_id == run_id, Job.seq == seq)
    ).scalar_one_or_none()


def update_job(run_dir, seq: int, *, image_prompt: str | None = None,
               copies: list | None = None, bump_rev: bool = True) -> dict:
    """更新单个 job 的提示词/文案（手动编辑与对话修改共用）。返回最新 bundle。"""
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        job = _job_by_seq(s, rid, seq)
        if job is None:
            raise IndexError("job 下标越界")
        if image_prompt is not None:
            job.image_prompt = str(image_prompt)
        if copies is not None:
            s.execute(delete(Copy).where(Copy.job_id == job.id))
            for k, c in enumerate(copies, start=1):
                s.add(Copy(job_id=job.id, seq=k,
                           angle=str(c.get("angle", "")), headline=str(c.get("headline", "")),
                           primary_text=str(c.get("primary_text", ""))))
        if bump_rev:
            job.rev += 1
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


def set_job_copies(run_dir, seq: int, copies: list) -> dict:
    return update_job(run_dir, seq, copies=copies)


# ---------------------------------------------------------------- 对话历史
def append_chat(run_dir, scope: str, job_seq: int | None, *messages: dict) -> None:
    """追加对话消息。scope='scenes' 时 job_seq 传 None。"""
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        job_id = None
        if scope != "scenes":
            job = _job_by_seq(s, rid, int(job_seq))
            if job is None:
                log.warning("对话落库跳过：run=%s scope=%s job_seq=%s 不存在", rid, scope, job_seq)
                return
            job_id = job.id
        base = s.execute(
            select(func.coalesce(func.max(ChatMessage.seq), -1)).where(
                ChatMessage.run_id == rid, ChatMessage.scope == scope,
                ChatMessage.job_id.is_(None) if job_id is None else ChatMessage.job_id == job_id,
            )
        ).scalar()
        for k, m in enumerate(messages, start=1):
            s.add(ChatMessage(run_id=rid, scope=scope, job_id=job_id, seq=base + k,
                              role=m["role"], content=m["content"]))
        s.commit()


# ---------------------------------------------------------------- 图片版本历史 + 出图落盘
def _trim_versions(s, run_dir: Path, job: Job, versions: list) -> list:
    while len(versions) > HIST_LIMIT:
        v = versions.pop(0)
        (Path(run_dir) / v.rel_path).unlink(missing_ok=True)
        s.delete(v)
    return versions


def apply_image_result(run_dir, seq: int, png: bytes, image_prompt: str | None = None) -> dict:
    """一张图出图/修改成功后的统一落盘：写主图 + 追加版本链 + 更新 job 行；
    母版出新图时其派生图失效待重做（决策 9）。返回打 has_image 标签所需信息。"""
    run_dir = Path(run_dir)
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        job = _job_by_seq(s, rid, seq)
        if job is None:
            raise IndexError("任务列表已变化，找不到这张图")
        versions = list(s.execute(
            select(ImageVersion).where(ImageVersion.job_id == job.id).order_by(ImageVersion.seq)
        ).scalars())
        hdir = run_dir / "images" / HIST_DIRNAME / job.filename
        hdir.mkdir(parents=True, exist_ok=True)
        next_seq = (versions[-1].seq + 1) if versions else 0

        if not versions:
            # 首次进版本链：把已有主图收编，撤销才有得退
            cur = run_dir / "images" / job.filename
            if cur.exists():
                rel = f"images/{HIST_DIRNAME}/{job.filename}/v{next_seq}.png"
                (run_dir / rel).write_bytes(cur.read_bytes())
                v = ImageVersion(job_id=job.id, seq=next_seq, rel_path=rel)
                s.add(v)
                versions.append(v)
                next_seq += 1
        elif job.cur_version_seq is not None:
            # 回退到中间版本后再出新图：丢弃「后面」的版本（标准撤销语义）
            for v in [v for v in versions if v.seq > job.cur_version_seq]:
                (run_dir / v.rel_path).unlink(missing_ok=True)
                s.delete(v)
            versions = [v for v in versions if v.seq <= job.cur_version_seq]
            next_seq = job.cur_version_seq + 1

        rel = f"images/{HIST_DIRNAME}/{job.filename}/v{next_seq}.png"
        (run_dir / rel).write_bytes(png)
        v = ImageVersion(job_id=job.id, seq=next_seq, rel_path=rel)
        s.add(v)
        versions.append(v)
        _trim_versions(s, run_dir, job, versions)

        store.save_image(run_dir, job.filename, png)
        job.image_rel_path = f"images/{job.filename}"
        job.cur_version_seq = next_seq
        job.rev += 1
        s.execute(delete(Copy).where(Copy.job_id == job.id))
        if image_prompt is not None:
            job.image_prompt = image_prompt
        if job.derived_from_job_id is None:
            for other in s.execute(
                select(Job).where(Job.derived_from_job_id == job.id)
            ).scalars():
                other.image_rel_path = ""
                s.execute(delete(Copy).where(Copy.job_id == other.id))
        _touch(s, rid)
        run = s.get(Run, rid)
        product = s.get(Product, run.product_id)
        info = {"product_id": run.product_id, "product_info": product.info if product else "",
                "main_scene": job.main_scene, "sub_scene": job.sub_scene}
        s.commit()
        return info


def goto_image_version(run_dir, seq: int, new_idx: int) -> dict:
    """把第 seq 张图切到版本链的 new_idx（0 起）。母版切版本后派生图失效、copies 清空。
    版本文件缺失或下标越界抛 FileNotFoundError。返回最新 bundle。"""
    run_dir = Path(run_dir)
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        job = _job_by_seq(s, rid, seq)
        if job is None:
            raise FileNotFoundError("该版本的图片文件不存在或下标越界")
        versions = list(s.execute(
            select(ImageVersion).where(ImageVersion.job_id == job.id).order_by(ImageVersion.seq)
        ).scalars())
        if not (0 <= new_idx < len(versions)):
            raise FileNotFoundError("该版本的图片文件不存在或下标越界")
        p = run_dir / versions[new_idx].rel_path
        if not p.exists():
            raise FileNotFoundError("该版本的图片文件不存在或下标越界")
        store.save_image(run_dir, job.filename, p.read_bytes())
        job.image_rel_path = f"images/{job.filename}"
        job.cur_version_seq = versions[new_idx].seq
        job.rev += 1
        s.execute(delete(Copy).where(Copy.job_id == job.id))
        if job.derived_from_job_id is None:
            for other in s.execute(select(Job).where(Job.derived_from_job_id == job.id)).scalars():
                other.image_rel_path = ""
                s.execute(delete(Copy).where(Copy.job_id == other.id))
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


# ---------------------------------------------------------------- 参考图（任务内副本 + 图库回连）
def _asset_id_for(s, kind: str, data: bytes) -> int | None:
    digest = hashlib.sha256(data).hexdigest()
    row = s.execute(
        select(RefAsset).where(RefAsset.kind == kind, RefAsset.sha256 == digest)
    ).scalar_one_or_none()
    return row.id if row else None


def set_run_refs(run_dir, kind: str, files: list) -> dict:
    """整组替换该任务某类参考图（决策 17）。files=[(name, bytes), ...]；
    文件写 run 目录私有副本（协议不变），行记录回连图库 asset（删库图不影响任务）。"""
    if kind not in REF_DIRNAMES:
        raise ValueError(f"未知参考图类型：{kind}")
    run_dir = Path(run_dir)
    rid = _rid(run_dir)
    dirname = REF_DIRNAMES[kind]
    with get_lock(rid), _session() as s:
        refs_dir = run_dir / dirname
        refs_dir.mkdir(parents=True, exist_ok=True)
        for old in refs_dir.iterdir():
            if old.is_file():
                old.unlink()
        s.execute(delete(RunRefImage).where(RunRefImage.run_id == rid, RunRefImage.kind == kind))
        for i, (name, data) in enumerate(files):
            safe = f"{i}_{Path(name).name}"[:60]
            (refs_dir / safe).write_bytes(data)
            s.add(RunRefImage(run_id=rid, kind=kind, seq=i, rel_path=f"{dirname}/{safe}",
                              asset_id=_asset_id_for(s, kind, data)))
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


def remove_run_ref(run_dir, kind: str, rel: str) -> dict:
    run_dir = Path(run_dir)
    rid = _rid(run_dir)
    with get_lock(rid), _session() as s:
        (run_dir / rel).unlink(missing_ok=True)
        s.execute(delete(RunRefImage).where(
            RunRefImage.run_id == rid, RunRefImage.kind == kind, RunRefImage.rel_path == rel))
        _touch(s, rid)
        s.commit()
        return _load_in_session(s, rid)


def load_ref_images(run_dir, rel_paths: list) -> list:
    """读参考图为 (name, bytes, mime) 元组列表，供 imagen 图生图使用（原 runstate 同名函数）。"""
    mimes = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    out = []
    for rel in rel_paths or []:
        p = Path(run_dir) / rel
        if p.exists():
            out.append((p.name, p.read_bytes(), mimes.get(p.suffix.lower(), "image/png")))
    return out


def latest_run_refs(product_id: int, exclude_run_id: int) -> dict:
    """同产品最近一个带参考图的任务的参考图文件（继承用，决策 18）。
    返回 {"style": [(name, bytes)...], "logo": [...]}，找不到返回空。"""
    with _session() as s:
        candidates = s.execute(
            select(Run.id).where(Run.product_id == product_id, Run.id != exclude_run_id)
            .order_by(Run.id.desc())
        ).scalars().all()
        for rid in candidates:
            rows = s.execute(
                select(RunRefImage).where(RunRefImage.run_id == rid)
                .order_by(RunRefImage.kind, RunRefImage.seq)
            ).scalars().all()
            if not rows:
                continue
            out: dict = {"style": [], "logo": []}
            src_dir = run_dir_of(rid)
            for r in rows:
                p = src_dir / r.rel_path
                if p.exists():
                    # run 目录里的文件名形如 "0_原名.png"，去掉序号前缀
                    out[r.kind].append((p.name.split("_", 1)[-1], p.read_bytes()))
            if out["style"] or out["logo"]:
                return out
    return {"style": [], "logo": []}


def latest_run_brand(product_id: int, exclude_run_id: int) -> dict:
    """同产品最近填过的品牌名/广告语言（继承用）；找不到返回空 dict。"""
    out: dict = {}
    with _session() as s:
        for run in s.execute(
            select(Run).where(Run.product_id == product_id, Run.id != exclude_run_id)
            .order_by(Run.id.desc())
        ).scalars():
            if "brand_name" not in out and run.brand_name:
                out["brand_name"] = run.brand_name
            if "ad_language" not in out and run.ad_language:
                out["ad_language"] = run.ad_language
            if len(out) == 2:
                break
    return out

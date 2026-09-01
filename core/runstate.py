"""每个 run（任务）的完整工作状态 state.json 读写。

- state.json 保存工作流页的全部可恢复状态（产品信息、场景、jobs、对话历史等），
  是"任务切换 / 历史恢复 / 后台任务"的数据底座；
- manifest.json 仍按既有协议（CLAUDE.md「数据与接口协议」）由 state 派生，
  字段走白名单，协议不受 state 新增字段影响；
- 前台 UI 与后台线程都通过 update()/persist() 在同一把每 run 锁下读改写，
  避免互相覆盖（锁为进程内锁，本工具为单进程 Streamlit，足够）。
"""
import json
import threading
from datetime import datetime
from pathlib import Path

from core import store
from core.config import OUTPUTS_DIR
from core.logger import get_logger

log = get_logger("runstate")

STATE_NAME = "state.json"
REFS_DIRNAME = "refs"
STYLE_REFS_DIRNAME = "refs_style"  # 海报风格参考图
LOGO_REFS_DIRNAME = "refs_logo"    # 品牌 Logo 图
PREV_DIRNAME = ".prev"  # images/.prev/<filename> 老机制的单版回退图（已被版本历史取代，首次修改时自动收编）
HIST_DIRNAME = ".hist"  # images/.hist/<filename>/v{seq}.png 图片版本历史
HIST_LIMIT = 10         # 每张图保留的最大版本数（含当前版），超出删最老的

# manifest 协议字段白名单（不得增删，见 CLAUDE.md）
MANIFEST_JOB_KEYS = [
    "main_scene", "sub_scene", "sub_scene_desc", "ratio",
    "image_prompt", "filename", "image_path", "copies", "derived_from",
]

_locks: dict = {}
_locks_guard = threading.Lock()


def get_lock(run_dir) -> threading.Lock:
    key = str(run_dir)
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def default_state() -> dict:
    return {
        "product_info": "",
        "ratio_choice": "",
        "title_count": 3,
        "scenes": [],
        "selected_scenes": [],
        "jobs": [],
        "jobs_gen": 0,
        "ref_images": [],
        "style_images": [],
        "logo_images": [],
        "brand_name": "",
        "ad_language": "",
        "chats": {},
    }


def _read(run_dir: Path) -> dict | None:
    path = Path(run_dir) / STATE_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("state.json 读取失败 run=%s: %r", Path(run_dir).name, e)
        return None
    merged = default_state()
    merged.update(data)
    return merged


def _write(run_dir: Path, state: dict) -> None:
    (Path(run_dir) / STATE_NAME).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _manifest_from_state(state: dict) -> dict:
    return {
        "product_info": state.get("product_info", ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "jobs": [
            {k: job.get(k, [] if k == "copies" else "") for k in MANIFEST_JOB_KEYS}
            for job in state.get("jobs", [])
        ],
    }


def load(run_dir: Path) -> dict | None:
    with get_lock(run_dir):
        return _read(run_dir)


def persist(run_dir: Path, state: dict) -> None:
    """整体落盘：state.json + manifest.json（+SQLite 索引，经 store.save_manifest）。"""
    run_dir = Path(run_dir)
    with get_lock(run_dir):
        _write(run_dir, state)
        store.save_manifest(run_dir, _manifest_from_state(state))


def update(run_dir: Path, mutator) -> dict:
    """锁内读-改-写：后台线程逐条回填结果用。mutator(state) 原地修改。返回改后 state。"""
    run_dir = Path(run_dir)
    with get_lock(run_dir):
        state = _read(run_dir) or default_state()
        mutator(state)
        _write(run_dir, state)
        store.save_manifest(run_dir, _manifest_from_state(state))
        return state


def rebuild_from_manifest(run_dir: Path) -> dict | None:
    """老 run 没有 state.json 时，从 manifest.json 尽量重建可编辑状态。"""
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    state = default_state()
    state["product_info"] = manifest.get("product_info", "")
    jobs = []
    for j in manifest.get("jobs", []):
        job = {k: j.get(k, [] if k == "copies" else "") for k in MANIFEST_JOB_KEYS}
        job.update({"rev": 0, "has_prev": False})
        jobs.append(job)
    state["jobs"] = jobs
    scenes, seen = [], set()
    for job in jobs:
        key = (job["main_scene"], job["sub_scene"])
        if key not in seen:
            seen.add(key)
            scenes.append(
                {
                    "main_scene": job["main_scene"],
                    "sub_scene": job["sub_scene"],
                    "description": job.get("sub_scene_desc", ""),
                }
            )
    state["scenes"] = scenes
    state["selected_scenes"] = list(range(len(scenes)))
    state["jobs_gen"] = 1
    return state


def list_task_dirs() -> list:
    """可作为任务载入的 run 目录（有 state.json 或 manifest.json），新的在前。"""
    if not OUTPUTS_DIR.exists():
        return []
    dirs = [
        d
        for d in OUTPUTS_DIR.iterdir()
        if d.is_dir() and ((d / STATE_NAME).exists() or (d / "manifest.json").exists())
    ]
    return sorted(dirs, key=lambda d: d.name, reverse=True)


# ---------------------------------------------------------------- 参考图 / 回退图文件
def save_ref_images(run_dir: Path, files: list, dirname: str = REFS_DIRNAME) -> list:
    """把上传的参考图存到 run 目录 dirname/（整目录覆盖），返回相对路径列表。

    dirname：REFS_DIRNAME=产品参考图，STYLE_REFS_DIRNAME=风格参考图，LOGO_REFS_DIRNAME=Logo。"""
    refs_dir = Path(run_dir) / dirname
    refs_dir.mkdir(parents=True, exist_ok=True)
    for old in refs_dir.iterdir():
        if old.is_file():
            old.unlink()
    rels = []
    for idx, (name, data) in enumerate(files):
        safe = f"{idx}_{Path(name).name}"[:60]
        (refs_dir / safe).write_bytes(data)
        rels.append(f"{dirname}/{safe}")
    return rels


def load_ref_images(run_dir: Path, rel_paths: list) -> list:
    """读参考图为 (name, bytes, mime) 元组列表，供 imagen 图生图使用。"""
    mimes = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    out = []
    for rel in rel_paths or []:
        p = Path(run_dir) / rel
        if p.exists():
            out.append((p.name, p.read_bytes(), mimes.get(p.suffix.lower(), "image/png")))
    return out


def _prev_path(run_dir: Path, filename: str) -> Path:
    return Path(run_dir) / "images" / PREV_DIRNAME / filename


def save_prev_image(run_dir: Path, filename: str, data: bytes) -> None:
    p = _prev_path(run_dir, filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def load_prev_image(run_dir: Path, filename: str) -> bytes | None:
    p = _prev_path(run_dir, filename)
    return p.read_bytes() if p.exists() else None


def delete_prev_image(run_dir: Path, filename: str) -> None:
    _prev_path(run_dir, filename).unlink(missing_ok=True)


# ---------------------------------------------------------------- 图片版本历史
def apply_image_version(run_dir: Path, job: dict, png: bytes) -> Path:
    """把 png 落为该 job 的最新版本：写主图 images/<filename>，同时追加进版本链。

    job 的 hist（版本相对路径列表）/hist_idx（当前位置）/hist_seq（版本号发号器）
    为运行时字段（不进 manifest 白名单）。规则：
    - 首次调用把已有主图（和老机制的 .prev 回退图）收编为历史版本，撤销才有得退；
    - 若当前处于历史中间位置（回退过），先丢弃「后面」的版本（标准撤销语义）；
    - 版本数超过 HIST_LIMIT 删最老的。
    调用方须保证互斥（runstate.update 的 mutator 内，或前台单线程路径）。"""
    run_dir = Path(run_dir)
    filename = job["filename"]
    hdir = run_dir / "images" / HIST_DIRNAME / filename
    hdir.mkdir(parents=True, exist_ok=True)
    hist = list(job.get("hist") or [])
    seq = int(job.get("hist_seq", 0))

    def _push(data: bytes):
        nonlocal seq
        rel = f"images/{HIST_DIRNAME}/{filename}/v{seq}.png"
        (run_dir / rel).write_bytes(data)
        hist.append(rel)
        seq += 1

    if not hist:
        prev = load_prev_image(run_dir, filename)
        if prev is not None:
            _push(prev)
            delete_prev_image(run_dir, filename)
        cur = run_dir / "images" / filename
        if cur.exists():
            _push(cur.read_bytes())
    else:
        idx = int(job.get("hist_idx", len(hist) - 1))
        for rel in hist[idx + 1:]:
            (run_dir / rel).unlink(missing_ok=True)
        hist = hist[: idx + 1]
    _push(png)
    while len(hist) > HIST_LIMIT:
        (run_dir / hist.pop(0)).unlink(missing_ok=True)
    job["hist"], job["hist_idx"], job["hist_seq"] = hist, len(hist) - 1, seq
    return Path(store.save_image(run_dir, filename, png))


def goto_image_version(run_dir: Path, job: dict, new_idx: int) -> bool:
    """把主图切换到版本链的 new_idx（上一步/下一步/任意跳转），更新 image_path/hist_idx。
    版本文件缺失或下标越界返回 False，其余善后（rev/copies/派生图失效）由调用方处理。"""
    hist = job.get("hist") or []
    if not (0 <= new_idx < len(hist)):
        return False
    p = Path(run_dir) / hist[new_idx]
    if not p.exists():
        return False
    path = store.save_image(Path(run_dir), job["filename"], p.read_bytes())
    job["image_path"] = str(path)
    job["hist_idx"] = new_idx
    return True

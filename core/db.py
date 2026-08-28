"""SQLite 索引层：manifest.json 的可检索副本。

设计原则（见 CLAUDE.md 技术决策）：
- manifest.json 仍是权威数据，本库只是索引，可随时用 rebuild_from_outputs() 全量重建；
- 入库失败不得影响主流程（调用方需捕获异常，或用 sync_run_safe）。

表结构：runs（一个 run 目录一行）→ jobs（一张图一行）→ copies（一套文案一行）。
"""
import json
import sqlite3
from pathlib import Path

from core.config import OUTPUTS_DIR, PROJECT_ROOT
from core.logger import get_logger

log = get_logger("db")

DB_PATH = PROJECT_ROOT / "data" / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dir_name     TEXT UNIQUE NOT NULL,
    product_info TEXT DEFAULT '',
    updated_at   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    main_scene     TEXT DEFAULT '',
    sub_scene      TEXT DEFAULT '',
    sub_scene_desc TEXT DEFAULT '',
    ratio          TEXT DEFAULT '',
    image_prompt   TEXT DEFAULT '',
    filename       TEXT DEFAULT '',
    image_path     TEXT DEFAULT '',
    derived_from   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS copies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seq          INTEGER DEFAULT 1,
    angle        TEXT DEFAULT '',
    headline     TEXT DEFAULT '',
    primary_text TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_copies_job ON copies(job_id);
CREATE TABLE IF NOT EXISTS scene_lib (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_info      TEXT NOT NULL DEFAULT '',
    main_scene        TEXT NOT NULL DEFAULT '',
    sub_scene         TEXT NOT NULL DEFAULT '',
    description       TEXT DEFAULT '',
    detail            TEXT DEFAULT '',
    total_score       INTEGER,
    product_fit       INTEGER,
    visual_clarity    INTEGER,
    purchase_intent   INTEGER,
    attention_emotion INTEGER,
    meta_safety       INTEGER,
    has_image         INTEGER NOT NULL DEFAULT 0,
    in_ads            INTEGER NOT NULL DEFAULT 0,
    source_run        TEXT DEFAULT '',
    created_at        TEXT DEFAULT '',
    UNIQUE(product_info, main_scene, sub_scene)
);
CREATE INDEX IF NOT EXISTS idx_scene_lib_score ON scene_lib(total_score);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def sync_run(run_dir: Path, manifest: dict) -> None:
    """把一个 run 的 manifest 同步入库（幂等：先删旧 jobs 再整体重插）。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (dir_name, product_info, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(dir_name) DO UPDATE SET product_info=excluded.product_info, "
            "updated_at=excluded.updated_at",
            (
                run_dir.name,
                manifest.get("product_info", ""),
                manifest.get("updated_at", ""),
            ),
        )
        run_id = conn.execute(
            "SELECT id FROM runs WHERE dir_name = ?", (run_dir.name,)
        ).fetchone()["id"]
        conn.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
        for job in manifest.get("jobs", []):
            cur = conn.execute(
                "INSERT INTO jobs (run_id, main_scene, sub_scene, sub_scene_desc, "
                "ratio, image_prompt, filename, image_path, derived_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    job.get("main_scene", ""),
                    job.get("sub_scene", ""),
                    job.get("sub_scene_desc", ""),
                    job.get("ratio", ""),
                    job.get("image_prompt", ""),
                    job.get("filename", ""),
                    job.get("image_path", ""),
                    job.get("derived_from", ""),
                ),
            )
            job_id = cur.lastrowid
            for i, copy in enumerate(job.get("copies") or [], start=1):
                conn.execute(
                    "INSERT INTO copies (job_id, seq, angle, headline, primary_text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        i,
                        copy.get("angle", ""),
                        copy.get("headline", ""),
                        copy.get("primary_text", ""),
                    ),
                )


def sync_run_safe(run_dir: Path, manifest: dict) -> str:
    """供主流程调用的安全版本：任何异常只返回错误信息，不抛出。"""
    try:
        sync_run(run_dir, manifest)
        return ""
    except Exception as exc:  # noqa: BLE001 —— 索引层故障不得中断素材生产
        log.error("SQLite 入库失败 run=%s: %r", run_dir.name, exc)
        return f"{type(exc).__name__}: {exc}"


def list_runs(keyword: str = "") -> list:
    """按更新时间倒序列出 run；keyword 匹配产品信息 / 主场景 / 细分场景。"""
    with get_conn() as conn:
        if keyword:
            like = f"%{keyword}%"
            rows = conn.execute(
                "SELECT DISTINCT r.*, "
                "(SELECT COUNT(*) FROM jobs j2 WHERE j2.run_id = r.id) AS job_count "
                "FROM runs r LEFT JOIN jobs j ON j.run_id = r.id "
                "WHERE r.product_info LIKE ? OR j.main_scene LIKE ? OR j.sub_scene LIKE ? "
                "ORDER BY r.dir_name DESC",
                (like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.*, "
                "(SELECT COUNT(*) FROM jobs j2 WHERE j2.run_id = r.id) AS job_count "
                "FROM runs r ORDER BY r.dir_name DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_run_jobs(run_id: int) -> list:
    """某 run 的全部 jobs（含各自 copies），供历史页展示。"""
    with get_conn() as conn:
        jobs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        ]
        for job in jobs:
            job["copies"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT seq, angle, headline, primary_text FROM copies "
                    "WHERE job_id = ? ORDER BY seq",
                    (job["id"],),
                ).fetchall()
            ]
        return jobs


# ---------------------------------------------------------------- 场景分类库
def upsert_scene_rows(product_info: str, source_run: str, rows: list) -> None:
    """场景挖掘结果入库。按（产品+主场景+细分场景）去重；
    重复入库时更新描述/详情/分数，但保留 has_image / in_ads 两个标签。"""
    from datetime import datetime

    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        for row in rows:
            detail = row.get("detail") or {}
            scores = detail.get("score_breakdown") or {}
            conn.execute(
                "INSERT INTO scene_lib (product_info, main_scene, sub_scene, description, detail, "
                "total_score, product_fit, visual_clarity, purchase_intent, attention_emotion, meta_safety, "
                "source_run, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(product_info, main_scene, sub_scene) DO UPDATE SET "
                "description=excluded.description, detail=excluded.detail, "
                "total_score=excluded.total_score, product_fit=excluded.product_fit, "
                "visual_clarity=excluded.visual_clarity, purchase_intent=excluded.purchase_intent, "
                "attention_emotion=excluded.attention_emotion, meta_safety=excluded.meta_safety, "
                "source_run=excluded.source_run",
                (
                    product_info,
                    row.get("main_scene", ""),
                    row.get("sub_scene", ""),
                    row.get("description", ""),
                    json.dumps(detail, ensure_ascii=False) if detail else "",
                    detail.get("total_score"),
                    scores.get("product_fit"),
                    scores.get("visual_clarity"),
                    scores.get("purchase_intent"),
                    scores.get("attention_emotion"),
                    scores.get("meta_safety"),
                    source_run,
                    now,
                ),
            )


def upsert_scene_rows_safe(product_info: str, source_run: str, rows: list) -> str:
    """入库失败不得影响挖掘主流程。"""
    try:
        upsert_scene_rows(product_info, source_run, rows)
        return ""
    except Exception as exc:  # noqa: BLE001
        log.error("场景库入库失败 run=%s: %r", source_run, exc)
        return f"{type(exc).__name__}: {exc}"


def list_scene_lib(
    keyword: str = "",
    product: str = "",
    main_scenes: list | None = None,
    score_range: tuple | None = None,
    has_image=None,
    in_ads=None,
    order: str = "score",
) -> list:
    """场景库筛选查询。score_range=(lo, hi) 时排除无分数的老数据；tri-state 参数 None=不筛。"""
    where, params = [], []
    if keyword:
        like = f"%{keyword}%"
        where.append("(sub_scene LIKE ? OR main_scene LIKE ? OR description LIKE ? OR detail LIKE ?)")
        params += [like, like, like, like]
    if product:
        where.append("product_info = ?")
        params.append(product)
    if main_scenes:
        where.append(f"main_scene IN ({','.join('?' * len(main_scenes))})")
        params += list(main_scenes)
    if score_range:
        where.append("total_score IS NOT NULL AND total_score BETWEEN ? AND ?")
        params += [score_range[0], score_range[1]]
    if has_image is not None:
        where.append("has_image = ?")
        params.append(1 if has_image else 0)
    if in_ads is not None:
        where.append("in_ads = ?")
        params.append(1 if in_ads else 0)
    order_sql = (
        "total_score DESC NULLS LAST, main_scene, id DESC"
        if order == "score"
        else "id DESC"
    )
    sql = "SELECT * FROM scene_lib"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order_sql}"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scene_lib_products() -> list:
    with get_conn() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT product_info FROM scene_lib ORDER BY product_info").fetchall()]


def scene_lib_main_scenes(product: str = "") -> list:
    with get_conn() as conn:
        if product:
            rows = conn.execute(
                "SELECT DISTINCT main_scene FROM scene_lib WHERE product_info = ? ORDER BY main_scene",
                (product,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT main_scene FROM scene_lib ORDER BY main_scene").fetchall()
        return [r[0] for r in rows]


def delete_scene_lib(ids: list) -> None:
    if not ids:
        return
    with get_conn() as conn:
        conn.execute(f"DELETE FROM scene_lib WHERE id IN ({','.join('?' * len(ids))})", list(ids))


def set_scene_in_ads(scene_id: int, value: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE scene_lib SET in_ads = ? WHERE id = ?", (1 if value else 0, scene_id))


def mark_scene_has_image(product_info: str, main_scene: str, sub_scene: str) -> None:
    """出图成功时系统自动打标；场景不在库中则静默跳过。"""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE scene_lib SET has_image = 1 "
                "WHERE product_info = ? AND main_scene = ? AND sub_scene = ?",
                (product_info, main_scene, sub_scene),
            )
    except Exception as exc:  # noqa: BLE001 —— 打标失败不影响生图
        log.error("场景出图打标失败 %s/%s: %r", main_scene, sub_scene, exc)


def excluded_scene_names(product_info: str) -> list:
    """同产品已有的细分场景名，供场景挖掘的 excluded_scenes 去重。"""
    with get_conn() as conn:
        return [r[0] for r in conn.execute(
            "SELECT sub_scene FROM scene_lib WHERE product_info = ? ORDER BY id",
            (product_info,)).fetchall()]


def rebuild_from_outputs() -> dict:
    """扫描 outputs/*/manifest.json 全量重建（幂等，可反复执行）。

    返回 {"imported": n, "errors": [(dir_name, msg), ...]}。
    """
    imported, errors = 0, []
    if not OUTPUTS_DIR.exists():
        return {"imported": 0, "errors": []}
    for manifest_path in sorted(OUTPUTS_DIR.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            sync_run(manifest_path.parent, manifest)
            imported += 1
        except Exception as exc:  # noqa: BLE001 —— 单个 run 损坏不影响其余导入
            errors.append((manifest_path.parent.name, f"{type(exc).__name__}: {exc}"))
    return {"imported": imported, "errors": errors}

"""全局参考图库：风格图 / Logo 跨任务复用。

文件仍存 data/ref_assets/{kind}/（gitignore），元数据在 ref_assets 表（决策 20）：
- 收录：上传参考图时按内容 sha256 去重收录一份（`add()`），同一张图只占一份；
- 列表/删除以库表为准；任务持有自己的私有副本（run_ref_images 行回连 asset），
  删库内图不影响任何已生成任务。
"""
import hashlib
from pathlib import Path

from sqlalchemy import delete, select

from core.config import PROJECT_ROOT
from core.logger import get_logger

log = get_logger("assets")

ASSETS_DIR = PROJECT_ROOT / "data" / "ref_assets"
KINDS = ("style", "logo")


def _session():
    from server.db.session import get_session_factory

    return get_session_factory()()


def _kind_dir(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"未知参考图类型：{kind}")
    return ASSETS_DIR / kind


def display_name(path: str) -> str:
    """库文件名去掉哈希前缀，还原上传时的原名。"""
    name = Path(path).name
    return name.split("_", 1)[-1] if "_" in name else name


def add(kind: str, name: str, data: bytes) -> str:
    """收录一张图（内容 sha256 去重），返回库内文件绝对路径（已存在则返回既有文件）。"""
    from server.db.models import RefAsset

    d = _kind_dir(kind)
    d.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    safe = Path(name).name[:48] or "img.png"
    fname = f"{digest[:12]}_{safe}"
    with _session() as s:
        row = s.execute(
            select(RefAsset).where(RefAsset.kind == kind, RefAsset.sha256 == digest)
        ).scalar_one_or_none()
        if row is not None:
            p = ASSETS_DIR / row.rel_path
            if not p.exists():  # 行在文件丢：补写文件
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            return str(p)
        rel = f"{kind}/{fname}"
        (d / fname).write_bytes(data)
        s.add(RefAsset(kind=kind, sha256=digest, orig_name=safe, rel_path=rel))
        s.commit()
        return str(ASSETS_DIR / rel)


def list_assets(kind: str) -> list:
    """按收录时间新→旧返回库内文件绝对路径列表（以库表为准，文件缺失的行跳过）。"""
    from server.db.models import RefAsset

    _kind_dir(kind)
    with _session() as s:
        rows = s.execute(
            select(RefAsset).where(RefAsset.kind == kind).order_by(RefAsset.id.desc())
        ).scalars().all()
    return [str(ASSETS_DIR / r.rel_path) for r in rows if (ASSETS_DIR / r.rel_path).exists()]


def remove(path: str) -> None:
    """从库里删除一张图（文件 + 元数据行；防呆：只允许删 ref_assets 目录内的文件）。"""
    from server.db.models import RefAsset

    p = Path(path).resolve()
    if ASSETS_DIR.resolve() not in p.parents:
        raise ValueError(f"拒绝删除库外文件：{path}")
    rel = str(p.relative_to(ASSETS_DIR.resolve()))
    with _session() as s:
        s.execute(delete(RefAsset).where(RefAsset.rel_path == rel))
        s.commit()
    p.unlink(missing_ok=True)

"""全局参考图库：风格图 / Logo 跨任务复用（data/ref_assets/，gitignore）。

- 收录：上传参考图时按内容 sha256 去重收录一份（`add()`），同一张图存多少次只占一份
- 首次使用：自动从历史任务 outputs/*/refs_style|refs_logo 一次性导入（`ensure_backfill()`）
- 展示/复用层：任务仍在自己的 run 目录保存参考图副本（协议不变），
  从库里删除历史图只影响库的展示，不影响任何已生成任务
"""
import hashlib
from pathlib import Path

from core.config import OUTPUTS_DIR, PROJECT_ROOT
from core.logger import get_logger

log = get_logger("assets")

ASSETS_DIR = PROJECT_ROOT / "data" / "ref_assets"
# kind -> run 目录里的来源目录名
KIND_SOURCE_DIRS = {"style": "refs_style", "logo": "refs_logo"}
_BACKFILL_MARK = ".backfilled"


def _kind_dir(kind: str) -> Path:
    if kind not in KIND_SOURCE_DIRS:
        raise ValueError(f"未知参考图类型：{kind}")
    return ASSETS_DIR / kind


def display_name(path: str) -> str:
    """库文件名去掉哈希前缀，还原上传时的原名。"""
    name = Path(path).name
    return name.split("_", 1)[-1] if "_" in name else name


def add(kind: str, name: str, data: bytes) -> str:
    """收录一张图，按内容去重；返回库内文件路径（已存在则返回既有文件）。"""
    d = _kind_dir(kind)
    d.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:12]
    for p in d.glob(f"{digest}_*"):
        return str(p)
    safe = Path(name).name[:48] or "img.png"
    p = d / f"{digest}_{safe}"
    p.write_bytes(data)
    return str(p)


def ensure_backfill() -> None:
    """首次调用时把历史任务里的参考图全部导入库（幂等，用标记文件跳过后续调用）。"""
    mark = ASSETS_DIR / _BACKFILL_MARK
    if mark.exists():
        return
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    imported = 0
    for kind, dirname in KIND_SOURCE_DIRS.items():
        for f in sorted(OUTPUTS_DIR.glob(f"*/{dirname}/*")):
            if not f.is_file():
                continue
            try:
                # run 目录里的文件名形如 "0_原名.png"，去掉序号前缀
                add(kind, f.name.split("_", 1)[-1], f.read_bytes())
                imported += 1
            except OSError as e:
                log.warning("参考图库导入失败 %s: %s", f, e)
    mark.write_text("done", encoding="utf-8")
    log.info("参考图库首次导入完成，共处理 %d 个历史文件", imported)


def list_assets(kind: str) -> list:
    """按收录时间新→旧返回库内文件路径列表。"""
    d = _kind_dir(kind)
    if not d.exists():
        return []
    files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    return [str(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]


def remove(path: str) -> None:
    """从库里删除一张图（防呆：只允许删 ref_assets 目录内的文件）。"""
    p = Path(path).resolve()
    if ASSETS_DIR.resolve() not in p.parents:
        raise ValueError(f"拒绝删除库外文件：{path}")
    p.unlink(missing_ok=True)

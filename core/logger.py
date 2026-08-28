"""统一日志：logs/app.log，按天滚动保留 14 天。

- 所有模块通过 get_logger("模块名") 取 logger，落同一个文件，带线程名
  （后台任务在线程里跑，排查时靠线程名区分）；
- 日志系统自身故障（建目录失败等）绝不影响主流程——降级为丢弃日志。
"""
import logging
from logging.handlers import TimedRotatingFileHandler

from core.config import PROJECT_ROOT

LOGS_DIR = PROJECT_ROOT / "logs"
_ROOT_NAME = "meta"

_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"


def _ensure_configured() -> None:
    root = logging.getLogger(_ROOT_NAME)
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    root.propagate = False
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            LOGS_DIR / "app.log", when="midnight", backupCount=14, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    except OSError:
        root.addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    _ensure_configured()
    return logging.getLogger(f"{_ROOT_NAME}.{name}")

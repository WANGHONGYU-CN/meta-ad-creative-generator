"""场景挖掘后台执行（API 层专用）。

Streamlit 版挖场景是前台同步转圈（1-3 分钟）；Web 版改为后台线程 + 轮询，
浏览器不用挂一个长请求。状态表独立于 core/tasks.py 的管线状态（挖掘发生在
生图之前，二者互斥由路由层检查），结果经 runstate.update 锁内落盘。
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import db, llm, runstate
from core.logger import get_logger
from core.prompts import render

from server.services import workflow as wf

log = get_logger("mining")

_executor = ThreadPoolExecutor(max_workers=4)
_status: dict = {}  # run_key -> {"state": running/finished/failed, "error": str, "count": int}
_guard = threading.Lock()


def status(run_key: str) -> dict | None:
    with _guard:
        s = _status.get(run_key)
        return dict(s) if s else None


def is_running(run_key: str) -> bool:
    s = status(run_key)
    return bool(s and s["state"] == "running")


def clear(run_key: str) -> None:
    """客户端收割完结果后清除状态记录（不清也不影响下次提交）。"""
    with _guard:
        s = _status.get(run_key)
        if s and s["state"] != "running":
            _status.pop(run_key, None)


def submit(config: dict, prompts: dict, run_dir: Path) -> bool:
    """提交挖掘。已有挖掘在跑或本任务后台管线/图片修改在跑时返回 False。"""
    import json as _json

    from core import tasks as bg

    key = Path(run_dir).name
    with _guard:
        cur = _status.get(key)
        if cur and cur["state"] == "running":
            return False
        if bg.is_busy(key):
            return False
        _status[key] = {"state": "running", "error": "", "count": 0}

    def _finish(state: str, error: str = "", count: int = 0):
        with _guard:
            _status[key] = {"state": state, "error": error, "count": count}

    def work():
        log.info("场景挖掘开始 run=%s", key)
        try:
            state = runstate.load(run_dir) or runstate.default_state()
            product_info = state.get("product_info", "")
            if not product_info.strip():
                raise ValueError("产品信息为空，请先填写")
            excluded = db.excluded_scene_names(product_info)
            prompt = render(
                prompts["scene_mining"]["template"],
                {
                    "product_info": product_info,
                    "excluded_scenes": _json.dumps(excluded, ensure_ascii=False),
                },
            )
            result = llm.call_json(config, prompt)
            rows = wf.scene_rows_from_result(result)
            if not rows:
                raise ValueError("模型返回的场景列表为空")

            def mut(s):
                s["scenes"] = rows
                s["selected_scenes"] = []
                s["jobs"] = []
                (s.get("chats") or {}).pop("chat_scenes", None)

            runstate.update(run_dir, mut)
            db.upsert_scene_rows_safe(product_info, key, rows)
            _finish("finished", count=len(rows))
            log.info("场景挖掘完成 run=%s 场景数=%d", key, len(rows))
        except Exception as e:  # noqa: BLE001 —— 后台线程不得抛出到无人处
            log.exception("场景挖掘失败 run=%s", key)
            _finish("failed", error=f"{type(e).__name__}: {e}")

    _executor.submit(work)
    return True

"""场景挖掘后台执行（API 层专用）。

后台线程 + 轮询，浏览器不用挂长请求。状态表独立于 core/tasks.py 的管线状态
（挖掘发生在生图之前，二者互斥由路由层检查），结果经 state_store 落库（PostgreSQL）。
excluded_scenes 按 product_id 精确取场景库（不再靠 product_info 全文匹配）。
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import llm
from core.logger import get_logger
from core.prompts import render

from server.services import scene_lib_store
from server.services import state_store as st
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
            state = st.load(run_dir) or {}
            product_info = state.get("product_info", "")
            if not product_info.strip():
                raise ValueError("产品信息为空，请先填写")
            excluded = scene_lib_store.excluded_scene_names(state["product_id"])
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
            st.replace_scenes(run_dir, rows, clear_jobs=True, clear_scene_chat=True)
            scene_lib_store.upsert_scenes_safe(state["product_id"], st.parse_run_name(key), rows)
            _finish("finished", count=len(rows))
            log.info("场景挖掘完成 run=%s 场景数=%d", key, len(rows))
        except Exception as e:  # noqa: BLE001 —— 后台线程不得抛出到无人处
            log.exception("场景挖掘失败 run=%s", key)
            _finish("failed", error=f"{type(e).__name__}: {e}")

    _executor.submit(work)
    return True

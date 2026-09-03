"""场景库：勾选场景创建独立生图任务（从 pages_/scene_library.py 抽取，无 UI 依赖）。"""
import json
from pathlib import Path

from core import db, runstate, store
from core.config import OUTPUTS_DIR


def detail_dict(row: dict) -> dict:
    try:
        return json.loads(row["detail"]) if row.get("detail") else {}
    except json.JSONDecodeError:
        return {}


def inherit_ref_images(run_dir: Path, product_info: str, state: dict) -> None:
    """把同产品最近任务的 参考图（风格图/Logo）和 品牌名/广告语言 继承进新任务。

    按 product_info 全等匹配（与场景去重同一取舍，决策 18）；参考图取最近一个带图的
    任务，品牌名/广告语言取最近一个填过的任务（可以来自不同任务）；匹配不到就留空。"""
    need_refs, need_brand, need_lang = True, True, True
    for sp in sorted(OUTPUTS_DIR.glob("*/state.json"), reverse=True):  # 目录名以时间开头，新→旧
        if sp.parent == run_dir:
            continue
        try:
            s = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if s.get("product_info") != product_info:
            continue
        if need_brand and s.get("brand_name"):
            state["brand_name"] = s["brand_name"]
            need_brand = False
        if need_lang and s.get("ad_language"):
            state["ad_language"] = s["ad_language"]
            need_lang = False
        if need_refs and (s.get("style_images") or s.get("logo_images")):
            for attr, dirname in (
                ("style_images", runstate.STYLE_REFS_DIRNAME),
                ("logo_images", runstate.LOGO_REFS_DIRNAME),
            ):
                files = []
                for rel in s.get(attr) or []:
                    p = sp.parent / rel
                    if p.exists():
                        # run 目录里的文件名形如 "0_原名.png"，去掉序号前缀
                        files.append((p.name.split("_", 1)[-1], p.read_bytes()))
                if files:
                    state[attr] = runstate.save_ref_images(run_dir, files, dirname=dirname)
            need_refs = False
        if not (need_refs or need_brand or need_lang):
            return


def create_task_from_scenes(scene_ids: list) -> str:
    """用场景库里选中的场景新建独立生图任务，返回新任务名（run 目录名）。
    所选场景必须属于同一产品。"""
    if not scene_ids:
        raise ValueError("未选择任何场景")
    rows = [r for r in db.list_scene_lib() if r["id"] in set(scene_ids)]
    if len(rows) != len(set(scene_ids)):
        raise ValueError("部分场景不存在（可能已被删除），请刷新后重试")
    products = {r["product_info"] for r in rows}
    if len(products) > 1:
        raise ValueError("所选场景属于不同产品，请只勾选同一产品的场景")
    scenes = []
    for r in rows:
        row = {
            "main_scene": r["main_scene"],
            "sub_scene": r["sub_scene"],
            "description": r["description"],
        }
        d = detail_dict(r)
        if d:
            row["detail"] = d
        scenes.append(row)
    product_info = products.pop()
    run_dir = store.create_run_dir(product_info[:20] or "场景库任务")
    state = runstate.default_state()
    state.update(
        {
            "product_info": product_info,
            "scenes": scenes,
            "selected_scenes": list(range(len(scenes))),
            "jobs_gen": 0,
        }
    )
    inherit_ref_images(run_dir, product_info, state)
    runstate.persist(run_dir, state)
    return run_dir.name

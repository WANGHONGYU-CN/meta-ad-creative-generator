"""参考图接口：任务内风格图/Logo 上传与删除 + 全局参考图库（浏览/应用/删除）。

- 上传/应用 = 整组替换该任务对应类别的参考图（决策 17）；
- 上传同时按内容 sha256 收录进全局图库（文件 + ref_assets 表，决策 18/20）；
- 删库内图不影响任务（任务持有私有副本，行内 asset_id 置空）。
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile

from core import assets

from server import deps
from server.schemas import ApplyAssets
from server.services import state_store as st

router = APIRouter(prefix="/api", tags=["refs"])

# kind -> (state 字段名, 是否多张)
KINDS = {"style": ("style_images", True), "logo": ("logo_images", False)}


def _kind_or_400(kind: str):
    if kind not in KINDS:
        raise HTTPException(400, f"未知参考图类型：{kind}（可用：style / logo）")
    return KINDS[kind]


def _refs_payload(run: str, kind: str, state: dict) -> dict:
    attr = KINDS[kind][0]
    enriched = deps.enrich_state(run, state)
    return {attr: enriched[attr], f"{attr}_urls": enriched[f"{attr}_urls"]}


@router.post("/runs/{run}/refs/{kind}")
async def upload_refs(run: str, kind: str, files: list[UploadFile]):
    _, multi = _kind_or_400(kind)
    run_dir = deps.get_run_dir(run)
    if not files:
        raise HTTPException(400, "未收到文件")
    if not multi and len(files) > 1:
        raise HTTPException(400, "Logo 只能上传 1 张")
    payload = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        name = f.filename or "img.png"
        payload.append((name, data))
        assets.add(kind, name, data)  # 同步收录进全局图库（内容去重）
    if not payload:
        raise HTTPException(400, "文件内容为空")
    state = st.set_run_refs(run_dir, kind, payload)
    return _refs_payload(run, kind, state)


@router.delete("/runs/{run}/refs/{kind}")
def delete_ref(run: str, kind: str, rel: str):
    """删除任务内单张参考图（rel 为 bundle 里记录的相对路径）。"""
    _kind_or_400(kind)
    run_dir = deps.get_run_dir(run)
    dirname = st.REF_DIRNAMES[kind]
    if not rel.startswith(f"{dirname}/") or "/.." in rel or rel.startswith(".."):
        raise HTTPException(400, "非法路径")
    state = st.remove_run_ref(run_dir, kind, rel)
    return _refs_payload(run, kind, state)


@router.post("/runs/{run}/refs/{kind}/apply")
def apply_assets(run: str, kind: str, body: ApplyAssets):
    """把全局图库里选中的图应用到任务（整组替换）。"""
    _, multi = _kind_or_400(kind)
    run_dir = deps.get_run_dir(run)
    if not multi and len(body.paths) > 1:
        raise HTTPException(400, "Logo 只能选 1 张")
    lib = set(assets.list_assets(kind))
    files = []
    for p in body.paths:
        if p not in lib:
            raise HTTPException(404, f"图库中不存在：{p}")
        files.append((assets.display_name(p), Path(p).read_bytes()))
    state = st.set_run_refs(run_dir, kind, files)
    return _refs_payload(run, kind, state)


@router.get("/assets/{kind}")
def list_assets(kind: str):
    _kind_or_400(kind)
    out = []
    for p in assets.list_assets(kind):
        name = Path(p).name
        out.append(
            {
                "path": p,
                "name": assets.display_name(p),
                "url": f"{deps.ASSETS_URL}/{kind}/{quote(name)}",
            }
        )
    return out


@router.delete("/assets/{kind}")
def delete_asset(kind: str, path: str):
    _kind_or_400(kind)
    try:
        assets.remove(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}

"""Meta 素材工厂 Web API 入口。

启动（开发）：
    ~/venvs/meta-creative-tool/bin/uvicorn server.main:app --port 8000 --reload
启动（日常）：
    ~/venvs/meta-creative-tool/bin/uvicorn server.main:app --port 8000

⚠ 必须单进程（不要加 --workers）：runstate 的每 run 锁与 core/tasks.py 的
后台线程池都是进程内的，多 worker 会互相覆盖任务状态（CLAUDE.md 决策 12/13）。
与 Streamlit 版共用同一套数据（outputs/、data/、config.json、prompts.json），
但两个进程的锁不互通，迁移期同一时间只开一边。

前端构建产物放 web/dist/ 时自动托管（浏览器直接访问本端口）；
未构建时仅提供 API（文档见 /docs）。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core import assets
from core.config import OUTPUTS_DIR, PROJECT_ROOT

from server.routers import library, prompts_admin, refs, runs, settings_admin, workflow_ops

app = FastAPI(
    title="Meta 素材工厂 API",
    description="产品信息 → AI 挖场景 → 场景变量直填生图 → 看图写文案 → 导出交付包",
    version="0.1.0",
)

# 本地工具：前端开发服务器（Vite 默认 5173）跨端口访问放行
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(workflow_ops.router)
app.include_router(refs.router)
app.include_router(prompts_admin.router)
app.include_router(settings_admin.router)
app.include_router(library.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def _startup():
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    assets.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    assets.ensure_backfill()  # 参考图库首次自动导入历史任务的风格图/Logo


# 图片等文件直出：outputs/（任务产物）与 data/ref_assets/（全局参考图库）
app.mount("/files/outputs", StaticFiles(directory=OUTPUTS_DIR, check_dir=False), name="outputs")
app.mount("/files/assets", StaticFiles(directory=assets.ASSETS_DIR, check_dir=False), name="assets")

# 前端构建产物（web/dist，阶段二产出）：静态资源直出 + SPA 回退——
# /scenes 等前端路由刷新时也要回到 index.html，由前端路由接管
_dist = PROJECT_ROOT / "web" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets", check_dir=False), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (_dist / path).resolve()
        if path and f.is_file() and f.is_relative_to(_dist.resolve()):
            return FileResponse(f)
        return FileResponse(_dist / "index.html")

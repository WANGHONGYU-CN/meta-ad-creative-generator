"""全量导出 12 张表的 Model——import 本包即让 Base.metadata 完整（Alembic env.py 依赖这一点）。"""
from server.db.models.chats import ChatMessage
from server.db.models.jobs import Copy, ImageVersion, Job
from server.db.models.products import Product
from server.db.models.prompts import PromptDefault, PromptTemplate
from server.db.models.refs import RefAsset, RunRefImage
from server.db.models.runs import Run
from server.db.models.scene_lib import SceneLibEntry
from server.db.models.scenes import Scene

__all__ = [
    "ChatMessage", "Copy", "ImageVersion", "Job", "Product",
    "PromptDefault", "PromptTemplate", "RefAsset", "RunRefImage",
    "Run", "SceneLibEntry", "Scene",
]

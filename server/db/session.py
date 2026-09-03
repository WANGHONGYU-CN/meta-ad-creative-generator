"""engine / Session 工厂。

DATABASE_URL 必须由环境变量提供（不内置带密码的 fallback）：
    export DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/meta_creative"
缺失时抛 RuntimeError 明确报错。engine 惰性创建——import 本模块不要求环境就绪。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_session_factory = None


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "环境变量 DATABASE_URL 未设置。请在 ~/.bashrc 配置，例如：\n"
            '  export DATABASE_URL="postgresql+psycopg://meta:<密码>@localhost:5432/meta_creative"'
        )
    return url


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), pool_pre_ping=True)
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def get_session():
    """FastAPI 依赖（第二阶段接入路由用）：yield 一个 Session，用完关闭。"""
    session: Session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()

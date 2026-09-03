"""提示词双表：prompt_templates 当前生效版（管理页编辑对象）、prompt_defaults 出厂默认（只读基线）。

「恢复默认」= 把 prompt_defaults 对应 key 整行拷回 prompt_templates。
出厂内容由 0002 seed migration 写入；运行时不再读 prompts.json / core.prompts.DEFAULT_PROMPTS。
"""
from sqlalchemy import DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, PKMixin


class _PromptColumns(PKMixin):
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    template: Mapped[str] = mapped_column(Text, nullable=False)


class PromptTemplate(_PromptColumns, Base):
    __tablename__ = "prompt_templates"

    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(), sort_order=90,
    )


class PromptDefault(_PromptColumns, Base):
    __tablename__ = "prompt_defaults"

    seeded_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), sort_order=90
    )

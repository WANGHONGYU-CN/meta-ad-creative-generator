"""场景分类库：跨任务积累。唯一键（产品, 主场景, 细分场景）沿用原 upsert 语义；
excluded_scenes 改按 product_id 精确取（不再靠 product_info 全文全等匹配）。"""
from sqlalchemy import (
    BigInteger, Boolean, ForeignKey, Index, SmallInteger, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, PKMixin, TimestampMixin


class SceneLibEntry(PKMixin, TimestampMixin, Base):
    __tablename__ = "scene_lib"
    __table_args__ = (
        UniqueConstraint("product_id", "main_scene", "sub_scene"),
        Index("ix_scene_lib_total_score", "total_score"),
        Index("ix_scene_lib_source_run_id", "source_run_id"),
    )

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    main_scene: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    sub_scene: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    audience: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    trigger: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    pain_or_desire: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    product_use: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    product_fit: Mapped[int | None] = mapped_column(SmallInteger)
    visual_clarity: Mapped[int | None] = mapped_column(SmallInteger)
    purchase_intent: Mapped[int | None] = mapped_column(SmallInteger)
    attention_emotion: Mapped[int | None] = mapped_column(SmallInteger)
    meta_safety: Mapped[int | None] = mapped_column(SmallInteger)
    total_score: Mapped[int | None] = mapped_column(SmallInteger)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    in_ads: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    source_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("runs.id", ondelete="SET NULL")
    )

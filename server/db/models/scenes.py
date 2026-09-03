"""任务内挖掘出的场景（含 6 字段详情与五维评分）。is_selected 取代旧 selected_scenes 下标数组。"""
from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, PKMixin


class Scene(PKMixin, Base):
    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 挖掘结果内顺序
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
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

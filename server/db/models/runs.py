"""任务（run）：文件目录为 outputs/run_{id}/，图片等二进制仍在文件系统，库存相对路径。"""
from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, PKMixin, TimestampMixin


class Run(PKMixin, TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_product_id", "product_id"),
        Index("ix_runs_updated_at", "updated_at"),
    )

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    # 建任务时从产品默认值拷入，可按任务修改
    brand_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ad_language: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ratio_choice: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    title_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("3"))

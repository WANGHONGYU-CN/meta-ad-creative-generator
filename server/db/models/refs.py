"""参考图：ref_assets 全局图库元数据（内容 sha256 去重）+ run_ref_images 任务持有的副本。

删图库图不影响任务（asset_id SET NULL）；seq 保序——生图传参顺序有意义（决策 15）。
"""
from sqlalchemy import (
    BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, CreatedAtMixin, PKMixin


class RefAsset(PKMixin, CreatedAtMixin, Base):
    __tablename__ = "ref_assets"
    __table_args__ = (
        UniqueConstraint("kind", "sha256"),
        CheckConstraint("kind IN ('style', 'logo')", name="kind_valid"),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)  # 完整 64 位内容哈希
    orig_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)  # data/ref_assets/{kind}/...


class RunRefImage(PKMixin, Base):
    __tablename__ = "run_ref_images"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", "seq"),
        CheckConstraint("kind IN ('style', 'logo')", name="kind_valid"),
        Index("ix_run_ref_images_asset_id", "asset_id"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)  # 相对 run 目录，如 refs_style/0_x.png
    asset_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ref_assets.id", ondelete="SET NULL")
    )

"""一张图一行（jobs）+ 版本链（image_versions）+ 文案（copies）。

jobs 的场景字段是建图时的快照（有意冗余，不做 scene 外键）；
derived_from_job_id 自引用记录双尺寸派生（母版→派生，取代旧 derived_from 文件名串）；
cur_version_seq 指向 image_versions.seq（取代旧 hist_idx），版本数上限裁剪由业务层执行。
"""
from sqlalchemy import (
    BigInteger, ForeignKey, Index, Integer, SmallInteger, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, CreatedAtMixin, PKMixin, TimestampMixin


class Job(PKMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("run_id", "filename"),
        UniqueConstraint("run_id", "seq"),
        Index("ix_jobs_derived_from_job_id", "derived_from_job_id"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    main_scene: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    sub_scene: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    sub_scene_desc: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ratio: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    image_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    image_rel_path: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    derived_from_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="SET NULL")
    )
    rev: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))  # 图片 URL 缓存戳
    cur_version_seq: Mapped[int | None] = mapped_column(Integer)


class ImageVersion(PKMixin, CreatedAtMixin, Base):
    __tablename__ = "image_versions"
    __table_args__ = (UniqueConstraint("job_id", "seq"),)

    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 即 v{seq}.png 的 seq
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)


class Copy(PKMixin, Base):
    __tablename__ = "copies"
    __table_args__ = (UniqueConstraint("job_id", "seq"),)

    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    angle: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    headline: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    primary_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

"""四处对话修改的历史。scenes 域挂 run（job_id 为 NULL），其余域挂具体 job；
重新生成 jobs = 删旧行插新行，对话随 job 外键 CASCADE 自动清理（取代旧 jobs_gen 批次号）。"""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, CreatedAtMixin, PKMixin


class ChatMessage(PKMixin, CreatedAtMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("scope IN ('scenes', 'prompt', 'image', 'copies')", name="scope_valid"),
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        # job_id 可空，普通 UNIQUE 约束管不住 NULL 重复，用 COALESCE 表达式唯一索引
        Index(
            "uq_chat_messages_pos",
            "run_id", "scope", text("COALESCE(job_id, 0)"), "seq",
            unique=True,
        ),
        Index("ix_chat_messages_job_id", "job_id"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 对话内顺序
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

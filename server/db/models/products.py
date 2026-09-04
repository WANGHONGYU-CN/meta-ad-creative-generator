"""产品维表：用户显式创建，场景去重 / 参考图与品牌信息继承 / 未来投放数据都挂产品 id。"""
from sqlalchemy import Text, text
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base, PKMixin, TimestampMixin


class Product(PKMixin, TimestampMixin, Base):
    __tablename__ = "products"

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # 短名，界面下拉用
    info: Mapped[str] = mapped_column(Text, nullable=False)               # 产品信息全文（挖场景输入）
    brand_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ad_language: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

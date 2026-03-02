from datetime import datetime
from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Item(Base):
    """
    DB 연결 확인용 범용 아이템 테이블.
    name(이름)과 description(설명)을 저장.
    """
    __tablename__ = "items"
    __table_args__ = {"schema": "temp_schema"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

from sqlalchemy import String, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column as col
from app.db import Base
from app.models import Record


class Announcement(Record, Base):
    __tablename__ = 'announcements'
    title_zh: Mapped[str] = col(String(200))
    title_en: Mapped[str] = col(String(200))
    body_zh: Mapped[str] = col(Text)
    body_en: Mapped[str] = col(Text)
    active: Mapped[bool] = col(Boolean, default=True)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))

from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    session_string: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="main")        # main | worker
    work_mode: Mapped[str] = mapped_column(String(24), default="chat_reply")  # chat_reply | worker_dm
    match_mode: Mapped[str] = mapped_column(String(16), default="mixed")  # mixed | phrase
    parent_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    keywords: Mapped[list["Keyword"]] = relationship("Keyword", back_populates="account", cascade="all, delete-orphan")
    prompts: Mapped[list["Prompt"]] = relationship("Prompt", back_populates="account", cascade="all, delete-orphan")
    logs: Mapped[list["Log"]] = relationship("Log", back_populates="account", cascade="all, delete-orphan")


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(512))
    triggers_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship("Account", back_populates="keywords")


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(32))  # chat | dm | worker_dm
    content: Mapped[str] = mapped_column(Text)

    account: Mapped["Account"] = relationship("Account", back_populates="prompts")


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    keyword: Mapped[str | None] = mapped_column(String(512), nullable=True)
    incoming: Mapped[str] = mapped_column(Text)
    outgoing: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship("Account", back_populates="logs")

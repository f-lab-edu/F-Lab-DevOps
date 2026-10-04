from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# Connection pool budget is configured per environment.
# Production uses at most 13 Pods (HPA max 10 plus rolling-update surge):
# Primary: (2 + 1) × 13 = 39 connections; Replica: (3 + 1) × 13 = 52 connections.
# When no replica URL is configured, both sessions share the write pool.


# ── Write Engine (Primary) ────────────────────────────────────
write_engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_WRITE_POOL_SIZE,
    max_overflow=settings.DATABASE_WRITE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
    pool_recycle=1800,  # 30분마다 연결 재생성 — RDS 재시작·IAM토큰 만료 대비
)

# ── Read Engine (Replica) ─────────────────────────────────────
# Replica를 사용하지 않으면 별도 읽기 풀을 만들지 않아 Primary 연결 예산을 보존한다.
if settings.DATABASE_READ_URL and settings.DATABASE_READ_URL != settings.DATABASE_URL:
    read_engine = create_engine(
        settings.DATABASE_READ_URL,
        pool_pre_ping=True,
        pool_size=settings.DATABASE_READ_POOL_SIZE,
        max_overflow=settings.DATABASE_READ_MAX_OVERFLOW,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
        pool_recycle=1800,  # Replica 재시작 시 stale connection 방지
    )
else:
    read_engine = write_engine

WriteSession = sessionmaker(bind=write_engine, autocommit=False, autoflush=False)
ReadSession = sessionmaker(bind=read_engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_write_db():
    """POST, DELETE 등 쓰기 작업 — Primary로 연결."""
    db = WriteSession()
    try:
        yield db
    finally:
        db.close()


def get_read_db():
    """GET 등 읽기 작업 — Replica로 연결."""
    db = ReadSession()
    try:
        yield db
    finally:
        db.close()


# 하위 호환: 기존 get_db() 참조 코드가 있다면 write로 연결
get_db = get_write_db

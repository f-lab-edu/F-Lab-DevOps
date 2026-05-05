from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings

# Connection Pool 설계 기준
# RDS db.t3.micro max_connections = LEAST({DBInstanceClassMemory/9531392}, 5000) ≈ 85
# Pod 2개 × (pool_size=5 + max_overflow=10) = 최대 30 연결 → 여유 충분


# ── Write Engine (Primary) ────────────────────────────────────
write_engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,          # 연결 풀 크기 — RDS t3.micro max_connections ≈ 85
    max_overflow=10,      # pool_size 초과 시 임시 추가 연결 (최대 15개 총합)
    pool_timeout=30,      # 연결 획득 대기 최대 시간 (초)
    pool_recycle=1800,    # 30분마다 연결 재생성 — RDS 재시작·IAM토큰 만료 대비
)

# ── Read Engine (Replica) ─────────────────────────────────────
# DATABASE_READ_URL이 없으면 Primary와 동일하게 연결 (dev 환경 호환)
_read_url = settings.DATABASE_READ_URL or settings.DATABASE_URL
read_engine = create_engine(
    _read_url,
    pool_pre_ping=True,   # 연결 유지 확인 (비활성화 시 연결 끊김 가능성 증가)
    pool_size=10,         # Read Replica: 읽기 트래픽이 많으므로 pool 크게
    max_overflow=20,      # pool_size 초과 시 임시 추가 연결 (최대 30개 총합)
    pool_timeout=30,      # 연결 획득 대기 최대 시간 (초)
    pool_recycle=1800,    # Replica 재시작 시 stale connection 방지
)

WriteSession = sessionmaker(bind=write_engine, autocommit=False, autoflush=False)
ReadSession  = sessionmaker(bind=read_engine,  autocommit=False, autoflush=False)


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
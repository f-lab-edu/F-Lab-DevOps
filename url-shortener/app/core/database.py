from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings


# ── 1. 엔진 생성 ──────────────────────────────────────────────
# create_engine: PostgreSQL에 실제로 연결하는 객체(연결 풀)를 만듦.
# pool_pre_ping=True: 연결이 끊겼을 때 자동으로 재연결을 시도.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
)

# ── 2. 세션 팩토리 ────────────────────────────────────────────
# 요청마다 새 DB 세션(연결 통로)을 만들어주는 공장.
# autocommit=False: 명시적으로 commit() 을 호출해야 DB에 반영.
# autoflush=False : commit 전에 자동으로 flush(임시 반영)하지 않음.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# ── 3. ORM Base 클래스 ────────────────────────────────────────
# 모든 ORM 모델(테이블 정의)이 이 클래스를 상속.
class Base(DeclarativeBase):
    pass


# ── 4. DB 세션 의존성 함수 ─────────────────────────────────────
def get_db():
    """
    FastAPI의 Depends() 로 사용하는 세션 제공 함수.
    요청이 시작되면 세션을 열고, 요청이 끝나면 자동으로 닫음.

    사용 예시:
        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db          # 라우터 함수에 세션을 전달
    finally:
        db.close()        # 요청 완료 후 반드시 세션 종료

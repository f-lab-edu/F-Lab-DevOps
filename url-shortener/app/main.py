from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.routes import health, item
from app.core.database import Base, engine


@asynccontextmanager # 비동기 context manager를 만들기 위한 데코레이터
async def lifespan(app: FastAPI):
    """
    앱 시작/종료 시 실행되는 이벤트 핸들러.
    DB 테이블 생성을 여기서 수행해 DB 준비 후 실행을 보장.
    """
    # 시작 시: DB 테이블 생성 (이미 있으면 건너뜀)
    Base.metadata.create_all(bind=engine)
    yield
    # 종료 시: 필요한 정리 작업 추가 가능


app = FastAPI(
    title="Infra Practice",
    description="인프라 실습용 API. /healthz(헬스체크), /items(DB 읽기·쓰기·삭제 확인)",
    version="1.0.0",
    lifespan=lifespan, # 앱 시작/종료 시 실행되는 이벤트 핸들러
)

app.include_router(health.router) # 헬스체크 엔드포인트
app.include_router(item.router) # DB 읽기·쓰기·삭제 확인 엔드포인트

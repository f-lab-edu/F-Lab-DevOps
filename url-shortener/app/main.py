import logging
import time

from contextlib import asynccontextmanager
from app.api.routes import health, item
from app.core.database import Base, write_engine

logging.basicConfig(level=logging.INFO)

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from prometheus_client import make_asgi_app
from app.core.metrics import http_request_total


@asynccontextmanager  # 비동기 context manager를 만들기 위한 데코레이터
async def lifespan(app: FastAPI):
    """
    앱 시작/종료 시 실행되는 이벤트 핸들러.
    DB 테이블 생성을 여기서 수행해 DB 준비 후 실행을 보장.
    """
    # 시작 시: DB 테이블 생성 (이미 있으면 건너뜀)
    # Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=write_engine)  # Primary에 테이블 생성
    yield
    # 종료 시: 필요한 정리 작업 추가 가능


app = FastAPI(
    title="Infra Practice",
    description="인프라 실습용 API. /healthz(헬스체크), /items(DB 읽기·쓰기·삭제 확인)",
    version="1.0.0",
    lifespan=lifespan,  # 앱 시작/종료 시 실행되는 이벤트 핸들러
)

# --- /metrics 엔드포인트 마운트 -----------------------------------
# prometheus_client의 ASGI 앱을 /metrics 경로에 마운트
# Prometheus가 이 엔드포인트를 30초마다 scrape
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# --- HTTP 요청 미들웨어 -------------------------------------------
# 모든 요청에 대해 http_request_total 카운터 증가
@app.middleware("http")
async def record_http_metrics(request: Request, call_next):
    response = await call_next(request)

    # /metrics 자체 요청은 카운터에서 제외 (무한 루프 방지)
    if request.url.path != "/metrics":
        http_request_total.labels(
            method=request.method,
            path=request.url.path,
            status_code=str(response.status_code),
        ).inc()

    return response


app.include_router(health.router)  # 헬스체크 엔드포인트
app.include_router(item.router)  # DB 읽기·쓰기·삭제 확인 엔드포인트

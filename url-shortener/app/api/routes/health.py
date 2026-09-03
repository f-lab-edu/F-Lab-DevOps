from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_write_db

router = APIRouter()
WriteDb = Annotated[Session, Depends(get_write_db)]


@router.get("/healthz", tags=["health"])
def healthcheck():
    """
    서버 상태를 확인하는 헬스체크 엔드포인트.
    """
    return {"status": "ok", "version": "v40"}


@router.get("/readyz", tags=["health"])
def readiness(db: WriteDb):
    """트래픽을 받을 준비가 됐는지 Primary DB 연결까지 확인한다."""
    try:
        db.execute(text("select 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="primary database unavailable") from exc
    return {"status": "ready"}


@router.get("/error-test")
def error_test():
    """[테스트용] 강제로 500 에러 발생"""
    raise HTTPException(status_code=500, detail="의도적 에러 — Aleㅛrt 테스트용")

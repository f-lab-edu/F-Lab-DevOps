from fastapi import HTTPException
from fastapi import APIRouter

router = APIRouter()

@router.get("/healthz", tags=["health"])
def healthcheck():
    """
    서버 상태를 확인하는 헬스체크 엔드포인트.
    """
    return {"status": "ok", "version": "v30"}

@router.get("/error-test")
def error_test():
    """[테스트용] 강제로 500 에러 발생"""
    raise HTTPException(status_code=500, detail="의도적 에러 — Alert 테스트용")
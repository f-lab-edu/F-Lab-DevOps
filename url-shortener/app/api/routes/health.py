from fastapi import APIRouter

router = APIRouter()

@router.get("/healthz", tags=["health"])
def healthcheck():
    """
    서버 상태를 확인하는 헬스체크 엔드포인트.
    """
    return {"status": "ok", "version": "v11"}
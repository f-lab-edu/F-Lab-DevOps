import redis
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    """
    Redis 클라이언트를 반환합니다.

    - REDIS_URL이 설정되지 않으면 None 반환 (dev 환경 호환)
    - Redis가 다운되어 있으면 None 반환 (graceful degradation)
      → 캐시 없이 DB에서 직접 조회
    """
    global _redis_client

    if not settings.REDIS_URL:
        return None

    if _redis_client is None:
        try:
            client = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,    # bytes 대신 str 반환
                socket_connect_timeout=2, # 연결 타임아웃 2초
                socket_timeout=2,
            )
            client.ping()               # 실제 연결 테스트 (실패 시 except로 이동)
            _redis_client = client
        except Exception as e:
            # Redis 연결 실패 시 None 반환 → 캐시 없이 DB 직접 조회
            # _redis_client는 None으로 유지 → 다음 요청에서 재시도
            logger.warning(f"Redis 연결 실패, 캐시 비활성화: {e}")
            return None

    return _redis_client
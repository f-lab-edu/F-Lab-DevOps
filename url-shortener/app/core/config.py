from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    .env 파일에서 환경변수를 읽어 애플리케이션 설정으로 제공.
    pydantic-settings 가 자동으로 .env 파일을 파싱.
    """

    # 데이터베이스 연결 URL
    # 형식: postgresql://유저:비밀번호@호스트:포트/DB이름
    DATABASE_URL: str = ""

    # Read Replica 연결 URL (비어있으면 DATABASE_URL로 fallback)
    DATABASE_READ_URL: str = ""

    # Redis URL. 비어있으면 캐시 비활성화 (운영 환경에서 설정)
    REDIS_URL: str = ""

    # 실행 환경 (development / production)
    APP_ENV: str = "development"

    # 디버그 모드 여부
    DEBUG: bool = False

    class Config:
        # 이 파일과 같은 경로의 .env 를 자동으로 읽습니다
        env_file = ".env"
        env_file_encoding = "utf-8"


# 앱 전체에서 공유할 settings 인스턴스
# 다른 파일에서: from app.core.config import settings
settings = Settings()

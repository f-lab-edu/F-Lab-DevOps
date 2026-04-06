# ── 베이스 이미지 ────────────────────────────────────
# ---- stage 1: 빌드 단계 -------------------------------------------------------
# python:3.11-slim : Python 3.11 이 설치된 경량(slim) Linux 이미지
# slim 을 쓰는 이유: 불필요한 패키지가 없어 이미지 크기가 작고 보안에 유리하다고 함
FROM python:3.11 AS builder

# ── 작업 디렉토리 설정 ──────────────────────────────
# 이후 모든 명령(COPY, RUN, CMD)이 /build 경로에서 실행됨.
WORKDIR /build

# ── 패키지 목록 먼저 복사 (레이어 캐시 최적화) ──────
# 코드보다 requirements.txt 를 먼저 복사하는 이유:
# Docker 는 변경된 레이어부터 다시 빌드함.
# 코드는 자주 바뀌지만 패키지 목록은 잘 안 바뀌므로,
# requirements.txt 가 그대로면 pip install 단계를 건너뛰어 빌드가 빠름.
COPY requirements.txt .

# ── 패키지 설치 ──────────────────────────────────────
# --no-cache-dir: pip 캐시를 저장하지 않아 이미지 크기를 줄임.
# RUN pip install --no-cache-dir -r requirements.txt # 현재 Python 환경(site-packages)에 직접 설치
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt # 패키지를 /install 디렉토리에 따로 설치
# 개선:
# RUN --mount=type=cache,target=/root/.cache/pip \
#          pip install --no-cache-dir --target=/install -r requirements.txt

# ---- stage 2: 실행 단계 -------------------------------------------------------
FROM python:3.11-slim
WORKDIR /app
# ── 애플리케이션 코드 복사 ──────────────────────────
COPY --from=builder /install /usr/local
COPY ./app ./app

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# ── 포트 노출 (문서용) ──────────────────────────────
# 실제로 포트를 열어주진 않지만, 이 컨테이너가 8000번을 사용함을 명시.
EXPOSE 8000

# ── 컨테이너 시작 명령 ──────────────────────────────
# 컨테이너가 실행되면 uvicorn 으로 FastAPI 서버를 구동.
# --host 0.0.0.0: 컨테이너 외부에서 접근 가능하도록 모든 인터페이스에 바인딩
# --port 8000   : 8000번 포트 사용
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

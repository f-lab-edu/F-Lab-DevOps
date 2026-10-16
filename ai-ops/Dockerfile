# 저장소의 앱과 같은 검증된 Python 베이스 digest를 사용한다.
FROM python:3.11.16-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9 AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --target=/install -r requirements.lock

FROM python:3.11.16-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/ai-ops/src:/opt/ai-ops/dependencies \
    AI_OPS_RUNBOOK_ROOT=/opt/ai-ops/runbooks \
    HOME=/tmp
WORKDIR /opt/ai-ops
COPY --from=dependencies /install ./dependencies
COPY src/ ./src/
COPY runbooks/ ./runbooks/
COPY config/metric-queries.json ./config/metric-queries.json
# 키·로컬 조사 기록·사이트·평가 자료는 이미지에 넣지 않는다.
RUN mkdir /data && chown 10001:10001 /data \
    && python -c 'from ai_ops.replay_tools import _runbook; [_runbook(name) for name in ("cache", "database", "deployment")]'
USER 10001:10001
ENTRYPOINT ["python", "-m", "ai_ops"]
CMD ["--help"]

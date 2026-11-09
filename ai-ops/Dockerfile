# AI 실행에 필요한 라이브러리만 제공하는 공식 Alpine 베이스를 고정한다.
FROM python:3.11.17-alpine3.24@sha256:5fb4bc53dd0180955e31e5245b648bbb24a4ac4ab03a1de81513642325220bcd AS base
# 고정 베이스에 남아 있는 zlib 지적을 배포판의 보안 수정 패키지로 해결한다.
RUN apk add --no-cache --upgrade zlib=1.4-r0 musl=1.2.6-r3
# 패키지 제거·수정 버전과 Python 필수 기능을 빌드 중에도 확인한다.
RUN python - <<'PY_CHECK'
import pathlib, ssl, sqlite3, zlib
packages = {}
for entry in pathlib.Path('/lib/apk/db/installed').read_text().split('\n\n'):
    fields = dict(line.split(':', 1) for line in entry.splitlines() if ':' in line)
    if 'P' in fields:
        packages[fields['P']] = fields['V']
assert packages['zlib'] == '1.4-r0'
assert packages['libssl3'] == packages['libcrypto3'] == '3.5.9-r0'
assert packages['musl'] == '1.2.6-r3'
assert not any(name in packages for name in ('glibc', 'pcre2'))
assert ssl.OPENSSL_VERSION.startswith("OpenSSL 3.5.9 ")
assert zlib.decompress(zlib.compress(b'ai-ops-runtime')) == b'ai-ops-runtime'
with sqlite3.connect(':memory:') as database:
    assert database.execute('SELECT 1').fetchone() == (1,)
assert ssl.create_default_context().get_ca_certs()
PY_CHECK

FROM base AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --target=/install -r requirements.lock

FROM base AS runtime
# 빌드 도구는 의존성 단계에서만 사용하고 실행 이미지에서 제거한다.
RUN python -m pip uninstall --yes pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.11/ensurepip
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

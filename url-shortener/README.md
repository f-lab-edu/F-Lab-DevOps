# url-shortener

FastAPI + PostgreSQL 로컬 개발 환경 셋업 가이드.
아래 순서대로 따라하면 동일한 환경에서 동일한 테스트를 재현할 수 있다.

---

## 사전 요구사항

- Python 3.11+
- Docker Desktop
- PostgreSQL (로컬 설치 및 실행 중)
  - 호스트: `localhost:5432`
  - DB 이름: `tempdb` (없으면 직접 생성)

---

## 1. 클론 및 .env 설정

```bash
git clone <repo-url>
cd url-shortener

# .env 파일 생성
cp .env.example .env
```

`.env` 파일을 열어 실행 방식에 맞게 `DATABASE_URL` 수정:

> `.env` 파일에는 실제 비밀번호와 같은 보안상 이유로 **.env.example 파일**에 형식으로 기재해 push

```bash
# A. uvicorn 직접 실행 시
DATABASE_URL=postgresql://postgres:비밀번호@localhost:5432/tempdb

# B. docker run 단독 실행 시
DATABASE_URL=postgresql://postgres:비밀번호@host.docker.internal:5432/tempdb

# C. docker-compose 실행 시 (아래 C 방법 사용 시에만)
DATABASE_URL=postgresql://urluser:urlpass@db:5432/urldb

APP_ENV=development
DEBUG=True
```

> ⚠️ docker run(B)에서 `localhost`를 쓰면 컨테이너 자신을 가리키므로 반드시 `host.docker.internal` 사용

---

## 2. 실행

### A. uvicorn 직접 실행 (가장 빠른 확인)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### B. docker run (컨테이너 단독 실행)

`.env`의 `DATABASE_URL` 호스트를 `host.docker.internal`로 변경한 뒤:

```bash
docker build -t url-shortener:local .
docker run -d -p 8001:8000 --env-file .env --name url-api url-shortener:local
```

> 포트 8001 사용 이유: uvicorn이 이미 8000을 점유하고 있을 경우 충돌 방지

### C. docker-compose (API + PostgreSQL 함께)

```bash
docker-compose up -d
```

---

## 3. 동작 확인 (테스트 재현)

실행 방식에 따라 포트가 다르다. `{port}`를 아래 표에서 확인 후 대입:

| 실행 방식 | 포트 |
|-----------|------|
| A. uvicorn | 8000 |
| B. docker run | 8001 |
| C. docker-compose | 8000 |

### 헬스체크

```bash
curl http://localhost:{port}/healthz
# 기대 응답: {"status":"ok"}
```

### DB 쓰기 확인

```bash
curl -X POST http://localhost:{port}/items \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "description": "DB 연결 확인"}'
# 기대 응답: {"id":1,"name":"test","description":"DB 연결 확인","created_at":"..."}
```

### DB 읽기 확인

```bash
curl http://localhost:{port}/items
# 기대 응답: [{"id":1,...}]
```

### Swagger UI

브라우저에서 `http://localhost:{port}/docs` 접속 → 전체 API 목록 및 직접 테스트 가능

---

## 4. 컨테이너 정리

```bash
# docker run 사용 시
docker stop url-api && docker rm url-api

# docker-compose 사용 시
docker-compose down
```

---

## 트러블슈팅

**DATABASE_URL 관련 오류 (`connection refused` 등)**
- uvicorn: `localhost:5432` 로 설정됐는지 확인
- docker run: `host.docker.internal:5432` 로 설정됐는지 확인
- docker-compose: `db:5432` 로 설정됐는지 확인, `docker-compose ps` 로 db 서비스 `healthy` 상태 확인

**docker run 실행 후 API 응답 없음**
- `--env-file .env` 옵션이 누락된 경우 → 컨테이너 내부에 환경변수가 없어 앱이 종료됨
- `docker logs url-api` 로 오류 메시지 확인

# Prometheus로 애플리케이션 상태를 관측하기 위한 메트릭 정의

from prometheus_client import Counter, Histogram

# 초기화에는 고정된 route template과 계측 조합만 사용한다.

# ── HTTP 요청 카운터 ──────────────────────────────────────────
# labels: method(GET/POST), path(/items, /items/{id}), status_code(200/404/500)
http_request_total = Counter(
    "http_request_total",
    "HTTP 요청 총 수",
    ["method", "path", "status_code"],
)

# ── 캐시 hit/miss 카운터 ─────────────────────────────────────
# labels: endpoint (list_items / get_item)
cache_hit_total = Counter(
    "cache_hit_total",
    "캐시 히트 총 수",
    ["endpoint"],
)

cache_miss_total = Counter(
    "cache_miss_total",
    "캐시 미스 총 수",
    ["endpoint"],
)

# hit/miss 외에 Redis 오류와 의도적인 cache bypass를 구분한다.
# cache_miss_total은 "Redis는 정상이나 key가 없음"만 의미하므로 장애 지표로
# 사용하지 않는다.
cache_operation_total = Counter(
    "cache_operation_total",
    "캐시 조회 결과 총 수",
    ["endpoint", "result"],  # hit / miss / error / unavailable / bypass / consistency_primary
)

# ── HTTP 응답 레이턴시 ────────────────────────────────────────
# 실제 URL 대신 route template(/items/{item_id})을 label로 사용해 cardinality를 제한한다.
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간 (초)",
    ["method", "route", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

# ── DB 쿼리 레이턴시 Histogram ────────────────────────────────
# buckets: 1ms ~ 1s 구간으로 P50/P95/P99 측정
# labels: operation (select_one / select_all / insert / delete)
db_query_latency_seconds = Histogram(
    "db_query_latency_seconds",
    "DB 쿼리 레이턴시 (초)",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)


# 실제 계측 지점의 유한한 값만 초기화한다. URL/아이템 ID로 조합을 만들지 않는다.
CACHE_ENDPOINTS = ("list_items", "get_item")
CACHE_RESULTS = ("hit", "miss", "error", "unavailable", "bypass", "consistency_primary")
DB_OPERATIONS = ("select_one", "select_all", "insert", "delete")
HTTP_METRIC_LABELS = (
    ("GET", "/healthz", ("200", "500")),
    ("GET", "/readyz", ("200", "500", "503")),
    ("GET", "/error-test", ("500",)),
    ("GET", "/items", ("200", "422", "500")),
    ("POST", "/items", ("201", "422", "500")),
    ("GET", "/items/_db", ("200", "500")),
    ("GET", "/items/{item_id}", ("200", "404", "422", "500")),
    ("DELETE", "/items/{item_id}", ("204", "404", "422", "500")),
)


def initialize_metric_labels() -> None:
    """Expose zero-valued children without inventing requests or latency samples.

    Repeated calls preserve observations. Unexpected responses remain measurable
    by the middleware; this list only bounds startup initialization.
    """
    for endpoint in CACHE_ENDPOINTS:
        cache_hit_total.labels(endpoint=endpoint)
        cache_miss_total.labels(endpoint=endpoint)
        for result in CACHE_RESULTS:
            cache_operation_total.labels(endpoint=endpoint, result=result)
    for operation in DB_OPERATIONS:
        db_query_latency_seconds.labels(operation=operation)
    for method, route, statuses in HTTP_METRIC_LABELS:
        for status in statuses:
            http_request_total.labels(method=method, path=route, status_code=status)
            http_request_duration_seconds.labels(
                method=method, route=route, status_code=status
            )

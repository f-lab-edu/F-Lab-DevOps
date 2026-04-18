from prometheus_client import Counter, Histogram

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

# ── DB 쿼리 레이턴시 Histogram ────────────────────────────────
# buckets: 1ms ~ 1s 구간으로 P50/P95/P99 측정
# labels: operation (select_one / select_all / insert / delete)
db_query_latency_seconds = Histogram(
    "db_query_latency_seconds",
    "DB 쿼리 레이턴시 (초)",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

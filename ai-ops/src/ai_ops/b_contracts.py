"""B 수집 설정과 읽기 전용 연결 경계. 이 모듈 자체는 네트워크에 접속하지 않는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import Field, StrictInt, StrictStr, model_validator

from ai_ops.contracts import K8sName, Sha256, StrictModel, Target, sha256_json
from ai_ops.evidence import ALLOWED_QUERY_IDS, QUERY_OUTCOMES, _json_object


QueryId = Literal["cache_outcomes", "cache_hit_ratio", "http_error_ratio", "api_latency", "db_latency"]
Reference = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")]


class ArgoBinding(StrictModel):
    namespace: K8sName
    application: K8sName
    repo_url: StrictStr = Field(pattern=r"^https://[A-Za-z0-9./_-]+\.git$", max_length=512)
    path: StrictStr = Field(pattern=r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$", max_length=256)
    destination_server: StrictStr = "https://kubernetes.default.svc"


class TargetBinding(StrictModel):
    alias: Reference
    target: Target
    pod_selector: dict[StrictStr, StrictStr] = Field(min_length=1, max_length=10)
    container: K8sName
    kubernetes_connection: Reference
    prometheus_connection: Reference
    argo: ArgoBinding
    routes: list[StrictStr] = Field(min_length=1, max_length=32)
    operations: list[Literal["select_one", "select_all", "insert", "delete"]] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def check_scope(self) -> TargetBinding:
        if self.target.environment != "practice":
            raise ValueError("first B bindings require practice environment")
        for key, value in self.pod_selector.items():
            # 첫 수집기는 앱의 단순한 동등 일치 label만 지원한다.
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", key):
                raise ValueError("unsupported selector key")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", value):
                raise ValueError("unsupported selector value")
        if len(set(self.routes)) != len(self.routes) or len(set(self.operations)) != len(self.operations):
            raise ValueError("duplicate query dimensions")
        if any(not re.fullmatch(r"/[a-zA-Z0-9_{}/.-]{0,120}", route) for route in self.routes):
            raise ValueError("route must be a registered route template")
        return self


class BindingRegistry(StrictModel):
    schema_version: Literal[1]
    bindings: list[TargetBinding] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_bindings(self) -> BindingRegistry:
        if len({item.alias for item in self.bindings}) != len(self.bindings):
            raise ValueError("duplicate binding alias")
        if len({sha256_json(item.target.model_dump()) for item in self.bindings}) != len(self.bindings):
            raise ValueError("duplicate binding target")
        return self

    def resolve(self, target: Target) -> TargetBinding:
        for binding in self.bindings:
            if binding.target == target:
                return binding
        raise ValueError("target is not registered in B bindings")


class Connection(StrictModel):
    kind: Literal["kubernetes", "prometheus"]
    base_url: StrictStr | None = None
    ca_file: StrictStr | None = None
    token_file: StrictStr | None = None

    @model_validator(mode="after")
    def validate_address(self) -> Connection:
        if self.base_url is not None:
            parsed = urlsplit(self.base_url)
            try:
                port = parsed.port
            except ValueError:
                raise ValueError("invalid connection port") from None
            schemes = {"https"} if self.kind == "kubernetes" else {"http", "https"}
            if (parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment or port == 0
                    or any(char.isspace() for char in self.base_url)):
                raise ValueError("connection must be a trusted origin without credentials or path")
        for value in (self.ca_file, self.token_file):
            if value is not None and (not value.startswith("/") or "\x00" in value or ".." in value.split("/")):
                raise ValueError("credential and CA references must be absolute file paths")
        return self


class CollectionLimits(StrictModel):
    lookback_seconds: StrictInt = Field(default=900, ge=60, le=900)
    deadline_seconds: StrictInt = Field(default=60, ge=1, le=60)
    request_timeout_seconds: StrictInt = Field(default=5, ge=1, le=10)
    max_pods: StrictInt = Field(default=12, ge=1, le=12)
    max_events: StrictInt = Field(default=20, ge=1, le=20)
    log_lines_per_pod: StrictInt = Field(default=200, ge=1, le=200)
    raw_log_bytes: StrictInt = Field(default=65536, ge=1, le=65536)
    response_bytes: StrictInt = Field(default=262144, ge=1, le=262144)
    total_response_bytes: StrictInt = Field(default=4194304, ge=1, le=4194304)
    payload_bytes: StrictInt = Field(default=65536, ge=1, le=65536)
    bundle_bytes: StrictInt = Field(default=4194304, ge=1, le=4194304)
    max_items: StrictInt = Field(default=256, ge=1, le=256)
    future_tolerance_seconds: StrictInt = Field(default=30, ge=0, le=30)


class ScrapePolicy(StrictModel):
    interval_seconds: Literal[30] = 30
    minimum_samples: StrictInt = Field(default=8, ge=1, le=10)
    max_age_seconds: StrictInt = Field(default=60, ge=30, le=60)


class BRuntime(StrictModel):
    schema_version: Literal[1]
    collector_version: Literal["captured-snapshot-v1"] = "captured-snapshot-v1"
    llm_enabled: Literal[False] = False
    per_incident_budget_usd: Annotated[float, Field(gt=0, le=0.10)] = 0.10
    daily_budget_usd: Annotated[float, Field(gt=0, le=1.0)] = 1.0
    connections: dict[Reference, Connection] = Field(min_length=1, max_length=20)
    limits: CollectionLimits = Field(default_factory=CollectionLimits)
    scrape: ScrapePolicy = Field(default_factory=ScrapePolicy)


class MetricQuery(StrictModel):
    query_id: QueryId
    unit: Literal["count", "ratio", "seconds"]
    dimension: Literal["route", "operation"] | None
    expressions: dict[StrictStr, StrictStr] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def check_expressions(self) -> MetricQuery:
        if set(self.expressions) != QUERY_OUTCOMES[self.query_id]:
            raise ValueError("metric expressions must cover the allowed outcomes")
        expected = {"api_latency": "route", "db_latency": "operation"}.get(self.query_id)
        if self.dimension != expected:
            raise ValueError("wrong metric dimension")
        expected_unit = ("seconds" if self.query_id.endswith("latency") else
                         "count" if self.query_id == "cache_outcomes" else "ratio")
        if self.unit != expected_unit:
            raise ValueError("wrong metric unit")
        for expression in self.expressions.values():
            validate_template(expression, dimension=self.dimension is not None)
        return self


def validate_template(expression: str, *, dimension: bool) -> None:
    placeholders = set(re.findall(r"@[a-z_]+@", expression))
    expected = {"@namespace@", "@service@"} | ({"@dimension@"} if dimension else set())
    if len(expression) > 4096 or placeholders != expected:
        raise ValueError("invalid metric template placeholders")


class MetricCatalog(StrictModel):
    schema_version: Literal[1]
    version: Literal["instant-5m-v1"]
    queries: list[MetricQuery] = Field(min_length=5, max_length=5)
    quality_expressions: dict[Literal["up", "scrape_samples", "last_scrape", "scrape_health"], StrictStr]
    content_hash: Sha256

    @model_validator(mode="after")
    def check_catalog(self) -> MetricCatalog:
        if {query.query_id for query in self.queries} != ALLOWED_QUERY_IDS:
            raise ValueError("catalog must contain each fixed query once")
        if set(self.quality_expressions) != {"up", "scrape_samples", "last_scrape", "scrape_health"}:
            raise ValueError("missing scrape quality expressions")
        for expression in self.quality_expressions.values():
            validate_template(expression, dimension=False)
        if sha256_json(self.model_dump(mode="json", exclude={"content_hash"})) != self.content_hash:
            raise ValueError("metric catalog hash mismatch")
        return self

    def render(self, query_id: QueryId, binding: TargetBinding, *, dimension: str | None = None) -> dict[str, str]:
        """호출자는 query ID만 고르고 치환값은 등록 대상과 유한 목록에서 얻는다."""
        query = next((item for item in self.queries if item.query_id == query_id), None)
        if query is None:
            raise ValueError("metric query ID is not allowed")
        allowed = binding.routes if query.dimension == "route" else binding.operations
        if dimension is not None and (query.dimension is None or dimension not in allowed):
            raise ValueError("metric dimension is not registered")
        suffix = f',{query.dimension}="{dimension}"' if dimension is not None else ""
        return {name: expression.replace("@namespace@", binding.target.namespace)
                .replace("@service@", binding.target.service).replace("@dimension@", suffix)
                for name, expression in query.expressions.items()}


@dataclass(frozen=True)
class BSettings:
    bindings: BindingRegistry
    runtime: BRuntime
    metrics: MetricCatalog
    config_hash: str


def load_b_settings(bindings: Path, runtime: Path, metrics: Path) -> BSettings:
    """로컬 신뢰 설정을 크기·중복 키·연결 종류·내용 해시와 함께 검사한다."""
    parsed = []
    for path, model in ((bindings, BindingRegistry), (runtime, BRuntime), (metrics, MetricCatalog)):
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("B settings file exceeds 64 KiB")
        try:
            parsed.append(model.model_validate(_json_object(raw)))
        except ValueError:
            raise ValueError("invalid B settings file") from None
    registry, execution, catalog = parsed
    for binding in registry.bindings:
        for ref, kind in ((binding.kubernetes_connection, "kubernetes"),
                          (binding.prometheus_connection, "prometheus")):
            if ref not in execution.connections or execution.connections[ref].kind != kind:
                raise ValueError("binding references an absent or wrong-kind connection")
    return BSettings(registry, execution, catalog, sha256_json(
        [item.model_dump(mode="json") for item in parsed]
    ))


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return monotonic()


@dataclass(frozen=True)
class ReadRequest:
    """연결 별칭과 상대 API 경로만 전달하는 GET 요청. 인증값은 전달하지 않는다."""
    connection: str
    path: str
    params: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", self.connection):
            raise ValueError("invalid connection reference")
        if not re.fullmatch(r"/(?:api|apis)/[a-zA-Z0-9/_.-]+", self.path) or ".." in self.path:
            raise ValueError("read request requires a relative API path")


@dataclass(frozen=True)
class ReadResponse:
    status_code: int
    body: bytes = field(repr=False)


class ReadTransport(Protocol):
    def get(self, request: ReadRequest, *, timeout_seconds: float, max_bytes: int) -> ReadResponse:
        """읽기 상한·리다이렉트 금지·인증값 미기록을 지키는 구현을 요구한다."""
        ...

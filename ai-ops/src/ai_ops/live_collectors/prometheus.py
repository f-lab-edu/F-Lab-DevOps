"""고정 instant 조회·scrape 품질을 확인하고 A metric samples로 변환한다."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ai_ops.b_contracts import ReadRequest, ReadTransport
from ai_ops.collection import ReadFailure, SnapshotBuilder
from ai_ops.live_collectors.common import add, read_json


def _query(builder, transport, expression):
    at = builder.started_at.isoformat().replace("+00:00", "Z")
    value = read_json(builder, transport, ReadRequest(builder.binding.prometheus_connection,
                      "/api/v1/query", (("query", expression), ("time", at))))
    if isinstance(value, ReadFailure):
        return value
    try:
        if value.get("status") != "success" or value.get("warnings") or value.get("infos"):
            raise ValueError("partial or invalid metric response")
        data = value["data"]
        if data["resultType"] != "vector" or not isinstance(data["result"], list):
            raise ValueError("instant vector required")
        result = []
        for series in data["result"]:
            labels, sample = series["metric"], series["value"]
            if not isinstance(labels, dict) or not isinstance(sample, list) or len(sample) != 2:
                raise ValueError("invalid instant sample")
            if any(not isinstance(key, str) or not isinstance(val, str) or len(key) > 256 or len(val) > 512
                   for key, val in labels.items()):
                raise ValueError("invalid metric labels")
            timestamp, text = sample
            if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or not isinstance(text, str):
                raise ValueError("invalid sample timestamp or value")
            if abs(timestamp - builder.started_at.timestamp()) > 0.001:
                raise ValueError("sample evaluation time mismatch")
            number = float(text)
            if not math.isfinite(number) or number < 0 or number > 10**15:
                raise ValueError("non-finite or negative sample")
            result.append((labels, number))
        return result
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return ReadFailure("invalid", "invalid_metric")


def _quality(builder, transport):
    scope = {"namespace": builder.binding.target.namespace, "service": builder.binding.target.service}
    vectors = {}
    for name, expression in builder.settings.metrics.quality_expressions.items():
        expression = expression.replace("@namespace@", scope["namespace"]).replace("@service@", scope["service"])
        result = _query(builder, transport, expression)
        if isinstance(result, ReadFailure):
            return result
        if not result:
            return ReadFailure("missing", "scrape_unavailable")
        vector = {}
        for labels, value in result:
            if any(labels.get(key) != expected for key, expected in scope.items()):
                return ReadFailure("invalid", "scope_mismatch")
            identity = tuple(sorted((key, val) for key, val in labels.items() if key != "__name__"))
            if identity in vector:
                return ReadFailure("invalid", "invalid_metric")
            vector[identity] = value
        vectors[name] = vector
    if len(next(iter(vectors.values()))) > builder.settings.runtime.limits.max_pods:
        return ReadFailure("missing", "truncated")
    keys = set(vectors["up"])
    if not keys or any(set(vector) != keys for vector in vectors.values()):
        return ReadFailure("missing", "scrape_unavailable")
    policy = builder.settings.runtime.scrape
    if any(vectors["up"][key] != 1 or vectors["scrape_health"][key] != 1
           or vectors["scrape_samples"][key] < policy.minimum_samples
           or not 0 <= builder.started_at.timestamp() - vectors["last_scrape"][key] <= policy.max_age_seconds
           for key in keys):
        return ReadFailure("missing", "scrape_unavailable")
    return None


def collect_prometheus(builder: SnapshotBuilder, transport: ReadTransport, *, route: str | None = None,
                       operation: str | None = None) -> None:
    # 직접 호출해도 dimension을 네트워크 요청 전에 검사한다.
    for query, dimension in (("api_latency", route), ("db_latency", operation)):
        builder.settings.metrics.render(query, builder.binding, dimension=dimension)
    quality = _quality(builder, transport)
    at = builder.started_at.isoformat().replace("+00:00", "Z")
    for query in builder.settings.metrics.queries:
        dimension = route if query.dimension == "route" else operation if query.dimension == "operation" else None
        context = {"dimension": f"{query.dimension}={dimension}"} if dimension is not None else {}
        if quality is not None:
            add(builder, "metric-" + query.query_id, "metric", failure=quality, context=context)
            continue
        samples = []
        missing_outcomes = []
        failure = None
        expressions = builder.settings.metrics.render(query.query_id, builder.binding, dimension=dimension)
        for outcome in sorted(expressions, key=lambda key: key not in {"requests", "count"}):
            expression = expressions[outcome]
            result = _query(builder, transport, expression)
            if isinstance(result, ReadFailure):
                failure = result
                break
            if not result and query.query_id == "cache_outcomes":
                missing_outcomes.append(outcome)
                continue
            if len(result) != 1 or result[0][0]:
                # 집계 결과는 label 없는 한 sample만 허용한다. 빈 응답을 0으로 채우지 않는다.
                failure = ReadFailure("missing" if not result else "invalid", "empty_response" if not result else "invalid_metric")
                break
            samples.append({"at": at, "outcome": outcome, "value": result[0][1]})
            if outcome in {"requests", "count"} and result[0][1] == 0:
                failure = ReadFailure("missing", "no_traffic")
                break
        if missing_outcomes:
            context["missing_outcomes"] = missing_outcomes
            add(builder, "metric-cache-outcome-gaps", "metric", failure=ReadFailure("missing", "empty_response"), context=context)
            if not samples:
                failure = ReadFailure("missing", "empty_response")
        if failure is None and query.query_id != "cache_outcomes":
            traffic = next(sample["value"] for sample in samples if sample["outcome"] in {"count", "requests"})
            if traffic == 0:
                failure = ReadFailure("missing", "no_traffic")
        if failure is not None:
            add(builder, "metric-" + query.query_id, "metric", failure=failure, context=context)
        else:
            add(builder, "metric-" + query.query_id, "metric", at=at,
                payload={"query_id": query.query_id, "unit": query.unit, "samples": samples}, context=context)

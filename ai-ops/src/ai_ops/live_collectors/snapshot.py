"""모델·저장소에 접근하지 않고 세 읽기 수집기의 snapshot을 만든다."""

from ai_ops.b_contracts import BSettings, Clock, ReadTransport
from ai_ops.collection import CapturedSnapshot, SnapshotBuilder
from ai_ops.contracts import Target
from ai_ops.live_collectors.deployment import collect_deployment
from ai_ops.live_collectors.kubernetes import collect_kubernetes
from ai_ops.live_collectors.prometheus import collect_prometheus
from ai_ops.live_collectors.transport import HTTPReadTransport


def collect_snapshot(settings: BSettings, target: Target, *, transport: ReadTransport | None = None,
                     clock: Clock | None = None, route: str | None = None,
                     operation: str | None = None) -> CapturedSnapshot:
    """명시적으로 호출한 경우만 수집한다. 기본 예제의 비어 있는 연결은 거부한다."""
    builder = SnapshotBuilder(settings, target, clock=clock)
    for query, dimension in (("api_latency", route), ("db_latency", operation)):
        settings.metrics.render(query, builder.binding, dimension=dimension)
    if transport is None:
        if any(settings.runtime.connections[ref].base_url is None for ref in
               (builder.binding.kubernetes_connection, builder.binding.prometheus_connection)):
            raise ValueError("live connections must be configured explicitly")
        transport = HTTPReadTransport(settings.runtime)
    workload = collect_kubernetes(builder, transport)
    collect_deployment(builder, transport, workload)
    collect_prometheus(builder, transport, route=route, operation=operation)
    return builder.finish()

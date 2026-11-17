"""Verify the exported zero baseline without a database, Redis, or network."""
import runpy
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

import prometheus_client as prom
from prometheus_client.parser import text_string_to_metric_families


class MetricInitializationTest(unittest.TestCase):
    def setUp(self):
        self.registry = prom.CollectorRegistry()
        with patch.object(prom, 'Counter', partial(prom.Counter, registry=self.registry)), \
             patch.object(prom, 'Histogram', partial(prom.Histogram, registry=self.registry)):
            self.metrics = runpy.run_path(str(Path(__file__).parents[1] / 'app/core/metrics.py'))

    def samples(self):
        return [s for f in text_string_to_metric_families(
            prom.generate_latest(self.registry).decode()) for s in f.samples]

    def value(self, name, **labels):
        return self.registry.get_sample_value(name, labels)

    def initialize(self):
        self.metrics['initialize_metric_labels']()

    def test_uninitialized_is_absent_not_zero(self):
        self.assertIsNone(self.value('cache_operation_total', endpoint='get_item', result='error'))
        self.assertIsNone(self.value('http_request_total', method='GET', path='/items', status_code='500'))
        self.assertIsNone(self.value('db_query_latency_seconds_count', operation='select_one'))

    def test_idle_export_has_all_cache_outcomes_without_requests(self):
        self.initialize()
        samples = [s for s in self.samples() if s.name == 'cache_operation_total']
        self.assertEqual(len(samples), 12)
        for endpoint in ('list_items', 'get_item'):
            for result in ('hit', 'miss', 'error', 'unavailable', 'bypass', 'consistency_primary'):
                self.assertEqual(self.value('cache_operation_total', endpoint=endpoint, result=result), 0)
            for name in ('cache_hit_total', 'cache_miss_total'):
                self.assertEqual(self.value(name, endpoint=endpoint), 0)
        self.assertEqual(sum(s.value for s in samples), 0)  # no fake denominator

    def test_histogram_initialization_does_not_observe_zero_latency(self):
        self.initialize()
        samples = self.samples()
        counts = [s for s in samples if s.name.endswith('_count')]
        self.assertEqual(len(counts), 26)  # 22 HTTP + four DB operations
        for s in samples:
            if not s.name.endswith('_created'):
                self.assertEqual(s.value, 0, s)
        self.assertEqual(self.value('db_query_latency_seconds_bucket', operation='select_one', le='+Inf'), 0)

    def test_repeated_initialization_preserves_actual_observations(self):
        self.initialize()
        self.metrics['cache_operation_total'].labels(endpoint='get_item', result='hit').inc()
        self.metrics['http_request_total'].labels(method='GET', path='/items/{item_id}', status_code='200').inc()
        self.metrics['http_request_duration_seconds'].labels(method='GET', route='/items/{item_id}', status_code='200').observe(0.2)
        self.metrics['db_query_latency_seconds'].labels(operation='select_one').observe(0.01)
        before = self.samples()
        self.initialize()
        self.assertEqual(self.samples(), before)
        self.assertEqual(self.value('http_request_total', method='GET', path='/items/{item_id}', status_code='200'), 1)
        self.assertEqual(self.value('db_query_latency_seconds_count', operation='select_one'), 1)
        self.assertEqual(self.value('cache_operation_total', endpoint='get_item', result='error'), 0)

    def test_startup_uses_bounded_templates_and_does_not_fill_unknown_series(self):
        self.initialize()
        samples = self.samples()
        http = [s for s in samples if s.name == 'http_request_total']
        self.assertEqual(len(http), 22)
        self.assertEqual({s.labels['path'] for s in http}, {
            '/healthz', '/readyz', '/error-test', '/items', '/items/_db', '/items/{item_id}'})
        self.assertIsNone(self.value('http_request_total', method='GET', path='/items/42', status_code='200'))
        self.assertIsNone(self.value('cache_operation_total', endpoint='unknown', result='hit'))
        self.assertIsNone(self.value('db_query_latency_seconds_count', operation='unknown'))
        # An uninitialized real status must still be observable by the middleware.
        self.metrics['http_request_total'].labels(method='GET', path='/items', status_code='503').inc()
        self.assertEqual(self.value('http_request_total', method='GET', path='/items', status_code='503'), 1)


if __name__ == '__main__':
    unittest.main()

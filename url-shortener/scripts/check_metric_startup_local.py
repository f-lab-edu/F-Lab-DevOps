"""Exercise lifespan and real ASGI metrics export with DB startup replaced locally."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Synthetic settings; never load a real database/Redis connection from .env.
os.environ.update(DATABASE_URL='postgresql://fixture:fixture@127.0.0.1:1/fixture',
                  DATABASE_READ_URL='', REDIS_URL='', ENABLE_FAULT_INJECTION='false')
from app.main import Base, app, write_engine
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families


def samples(text):
    return [s for f in text_string_to_metric_families(text) for s in f.samples
            if s.name.startswith(('http_request_', 'cache_', 'db_query_'))]


def main():
    with patch.object(Base.metadata, 'create_all') as create, \
         patch.object(write_engine, 'connect', side_effect=AssertionError('DB access forbidden')), \
         patch('socket.create_connection', side_effect=AssertionError('Network access forbidden')), \
         TestClient(app) as client:
        create.assert_called_once_with(bind=write_engine)
        first = client.get('/metrics/')
        assert first.status_code == 200
        idle = samples(first.text)
        assert len([s for s in idle if s.name == 'cache_operation_total']) == 12
        assert len([s for s in idle if s.name == 'http_request_total']) == 22
        assert all(s.value == 0 for s in idle if not s.name.endswith('_created'))
        assert samples(client.get('/metrics/').text) == idle
        assert client.get('/healthz').status_code == 200
        assert client.get('/items/not-an-integer').status_code == 422
        assert client.get('/nonexistent-private-path').status_code == 404
        after = samples(client.get('/metrics/').text)
        http = [s for s in after if s.name == 'http_request_total']
        assert sum(s.value for s in http) == 3
        assert any(s.labels['path'] == '/items/{item_id}' and s.labels['status_code'] == '422' and s.value == 1 for s in http)
        assert any(s.labels['path'] == 'unmatched' and s.value == 1 for s in http)
        assert 'nonexistent-private-path' not in str(after) and 'not-an-integer' not in str(after)
        assert all(s.value == 0 for s in after if s.name.startswith('db_query_') and not s.name.endswith('_created'))
    print(json.dumps({'startup_initialized': True, 'idle_counts_zero': True,
                      'scrape_not_counted': True, 'route_templates_preserved': True,
                      'db_network_calls': 0, 'observed_local_requests': 3}))


if __name__ == '__main__':
    main()

import unittest

from app.core.cache_consistency import (
    advance_generations,
    begin_primary_read_window,
    current_generation,
    requires_primary_read,
    versioned_cache_key,
)


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def exists(self, key: str) -> int:
        return int(key in self.values)

    def setex(self, key: str, _ttl_seconds: int, value: str):
        self.values[key] = value

    def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
        return 0


class CacheConsistencyTest(unittest.TestCase):
    def test_previous_generation_cannot_be_used_after_a_write(self):
        cache = FakeRedis()
        base_key = "item:42"
        generation_key = "item:42:version"

        previous_key = versioned_cache_key(base_key, current_generation(cache, generation_key))
        cache.setex(previous_key, 300, '{"id": 42}')

        advance_generations(cache, (generation_key,), ())

        current_key = versioned_cache_key(base_key, current_generation(cache, generation_key))
        self.assertNotEqual(previous_key, current_key)
        self.assertIsNone(cache.get(current_key))
        self.assertIsNotNone(cache.get(previous_key))

    def test_recent_write_marker_requires_primary_read(self):
        cache = FakeRedis()
        marker_key = "item:42:primary-read"

        self.assertFalse(requires_primary_read(cache, marker_key))
        begin_primary_read_window(cache, (marker_key,), 60)

        self.assertTrue(requires_primary_read(cache, marker_key))


if __name__ == "__main__":
    unittest.main()

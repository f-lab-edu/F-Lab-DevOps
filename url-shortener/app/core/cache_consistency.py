def versioned_cache_key(base_key: str, generation: str) -> str:
    """현재 세대만 읽도록 데이터 캐시 키를 만든다."""
    return f"{base_key}:v{generation}"


def current_generation(cache, generation_key: str) -> str:
    """세대가 아직 없으면 첫 세대인 0을 사용한다."""
    return cache.get(generation_key) or "0"


def requires_primary_read(cache, marker_key: str) -> bool:
    """최근 쓰기 이후 Replica 대신 Primary를 조회해야 하는지 확인한다."""
    return bool(cache.exists(marker_key))


def begin_primary_read_window(cache, marker_keys: tuple[str, ...], ttl_seconds: int) -> None:
    """쓰기 전부터 Replica 지연 구간을 Primary 조회 구간으로 표시한다."""
    for marker_key in marker_keys:
        cache.setex(marker_key, ttl_seconds, "1")


def advance_generations(
    cache,
    generation_keys: tuple[str, ...],
    legacy_cache_keys: tuple[str, ...],
) -> None:
    """쓰기 성공 뒤 현재 세대를 변경해 이전 요청의 캐시 재삽입을 격리한다."""
    for generation_key in generation_keys:
        cache.incr(generation_key)
    if legacy_cache_keys:
        cache.delete(*legacy_cache_keys)

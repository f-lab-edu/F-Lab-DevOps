import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import get_redis
from app.core.cache_consistency import (
    advance_generations,
    begin_primary_read_window,
    current_generation,
    requires_primary_read,
    versioned_cache_key,
)
from app.core.config import settings
from app.core.database import get_read_db, get_write_db
from app.core.metrics import (
    cache_hit_total,
    cache_miss_total,
    cache_operation_total,
    db_query_latency_seconds,
)
from app.models.item import Item

router = APIRouter(prefix="/items", tags=["items"])
logger = logging.getLogger(__name__)

ITEM_TTL = 300  # 단건 조회 캐시 TTL: 5분
LIST_TTL = 60  # 목록 조회 캐시 TTL: 1분 (변경 가능성 높아 짧게)
LIST_KEY = "items:all"
LIST_GENERATION_KEY = "items:version"
LIST_PRIMARY_READ_MARKER_KEY = "items:primary-read"

WriteDb = Annotated[Session, Depends(get_write_db)]
ReadDb = Annotated[Session, Depends(get_read_db)]
Week12BypassCache = Annotated[
    bool | None,
    Header(alias="X-Week12-Bypass-Cache"),
]
Week12ReadDelayMs = Annotated[
    int | None,
    Header(alias="X-Week12-Read-Delay-Ms"),
]


def _week12_fault_options(
    bypass_cache: bool | None,
    read_delay_ms: int | None,
) -> tuple[bool, int]:
    """비운영 Week 12 실습용 옵션을 안전한 범위로 제한한다."""
    if not settings.ENABLE_FAULT_INJECTION:
        return False, 0

    bounded_delay_ms = min(
        max(read_delay_ms or 0, 0),
        max(settings.MAX_FAULT_DELAY_MS, 0),
    )
    return bool(bypass_cache), bounded_delay_ms


def _inject_read_delay(db: Session, delay_ms: int) -> None:
    """애플리케이션이 사용하는 Read DB 세션 안에서 지연을 발생시킨다."""
    if delay_ms > 0:
        db.execute(
            text("select pg_sleep(:delay_seconds)"),
            {"delay_seconds": delay_ms / 1000},
        )

# ── 스키마 ────────────────────────────────────────────────────


class ItemCreate(BaseModel):
    name: str
    description: str | None = None


class ItemResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: str

    @classmethod
    def from_orm_custom(cls, item: Item) -> "ItemResponse":
        return cls(
            id=item.id,
            name=item.name,
            description=item.description,
            created_at=item.created_at.isoformat() if item.created_at else "",
        )


class DbProbe(BaseModel):
    in_recovery: bool
    server_addr: str | None
    db: str
    user: str


class DbProbeResponse(BaseModel):
    write: DbProbe
    read: DbProbe


def _probe_db(db: Session) -> DbProbe:
    row = db.execute(
        text(
            """
            select
              pg_is_in_recovery() as in_recovery,
              inet_server_addr()::text as server_addr,
              current_database() as db,
              current_user as "user"
            """
        )
    ).mappings().one()

    return DbProbe(
        in_recovery=bool(row["in_recovery"]),
        server_addr=row["server_addr"],
        db=str(row["db"]),
        user=str(row["user"]),
    )


# ── POST: 아이템 생성 — 목록 캐시 무효화 ──────────────────────
@router.post("", response_model=ItemResponse, status_code=201)
def create_item(body: ItemCreate, db: WriteDb):
    """[Primary] 아이템 생성 — 목록 캐시 세대를 증가시킨다."""
    start = time.perf_counter()
    cache = get_redis()

    if cache:
        try:
            begin_primary_read_window(
                cache,
                (LIST_PRIMARY_READ_MARKER_KEY,),
                settings.REPLICA_CONSISTENCY_WINDOW_SECONDS,
            )
        except RedisError as e:
            logger.warning(f"Primary 조회 창 설정 실패: {e}")
            cache = None

    record = Item(name=body.name, description=body.description)
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(f"db_route=primary operation=insert item_id={record.id} name={body.name}")

    db_query_latency_seconds.labels(operation="insert").observe(
        time.perf_counter() - start
    )

    if cache:
        try:
            advance_generations(cache, (LIST_GENERATION_KEY,), (LIST_KEY,))
        except RedisError as e:
            logger.warning(f"목록 캐시 세대 증가 실패: {e}")

    return ItemResponse.from_orm_custom(record)


# ── GET 목록: Cache-Aside ────────────────────────────────────────
@router.get("", response_model=list[ItemResponse])
def list_items(
    read_db: ReadDb,
    write_db: WriteDb,
    x_week12_bypass_cache: Week12BypassCache = None,
    x_week12_read_delay_ms: Week12ReadDelayMs = None,
):
    """목록 Cache-Aside — 최근 쓰기 직후에만 Primary를 조회한다."""
    bypass_cache, read_delay_ms = _week12_fault_options(
        x_week12_bypass_cache,
        x_week12_read_delay_ms,
    )
    cache = None if bypass_cache else get_redis()
    read_from_primary = False
    cache_key = LIST_KEY

    if bypass_cache:
        cache_operation_total.labels(endpoint="list_items", result="bypass").inc()
    elif cache is None:
        cache_operation_total.labels(endpoint="list_items", result="unavailable").inc()

    if cache:
        try:
            generation = current_generation(cache, LIST_GENERATION_KEY)
            cache_key = versioned_cache_key(LIST_KEY, generation)
            read_from_primary = requires_primary_read(cache, LIST_PRIMARY_READ_MARKER_KEY)
            if read_from_primary:
                cache_operation_total.labels(
                    endpoint="list_items", result="consistency_primary"
                ).inc()
                logger.info("cache_bypass endpoint=list_items reason=recent_write")
            else:
                cached = cache.get(cache_key)
                if cached:
                    cache_hit_total.labels(endpoint="list_items").inc()
                    cache_operation_total.labels(endpoint="list_items", result="hit").inc()
                    logger.info("cache_hit endpoint=list_items")
                    return [ItemResponse(**i) for i in json.loads(cached)]
                cache_miss_total.labels(endpoint="list_items").inc()
                cache_operation_total.labels(endpoint="list_items", result="miss").inc()
                logger.info("cache_miss endpoint=list_items")
        except (RedisError, json.JSONDecodeError) as e:
            cache_operation_total.labels(endpoint="list_items", result="error").inc()
            logger.warning(f"캐시 조회 실패, DB 직접 조회: {e}")
            cache = None
            read_from_primary = True

    db = write_db if read_from_primary else read_db
    start = time.perf_counter()
    _inject_read_delay(db, read_delay_ms)
    items = db.query(Item).all()
    db_query_latency_seconds.labels(operation="select_all").observe(
        time.perf_counter() - start
    )

    db_route = "primary" if read_from_primary else "replica"
    logger.info(f"db_route={db_route} operation=select_all count={len(items)}")

    result = [ItemResponse.from_orm_custom(i) for i in items]

    if cache:
        try:
            cache.setex(cache_key, LIST_TTL, json.dumps([r.model_dump() for r in result]))
        except RedisError as e:
            logger.warning(f"캐시 저장 실패 (무시): {e}")

    return result


# ── GET /_db: Write/Read 분리 진단 ───────────────────────────────
# /{item_id} 보다 먼저 등록해야 라우트 충돌 방지
@router.get("/_db", response_model=DbProbeResponse)
def probe_db(
    write_db: WriteDb,
    read_db: ReadDb,
):
    """
    [진단] write/read 세션이 각각 Primary/Replica로 붙는지 확인.
    - Replica면 pg_is_in_recovery() = true
    - Primary면 pg_is_in_recovery() = false
    """
    return DbProbeResponse(
        write=_probe_db(write_db),
        read=_probe_db(read_db),
    )


# ── GET 단건: Cache-Aside ────────────────────────────────────────
@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: int,
    read_db: ReadDb,
    write_db: WriteDb,
    x_week12_bypass_cache: Week12BypassCache = None,
    x_week12_read_delay_ms: Week12ReadDelayMs = None,
):
    """단건 Cache-Aside — 최근 쓰기 직후에만 Primary를 조회한다."""
    bypass_cache, read_delay_ms = _week12_fault_options(
        x_week12_bypass_cache,
        x_week12_read_delay_ms,
    )
    cache = None if bypass_cache else get_redis()
    item_key = f"item:{item_id}"
    generation_key = f"{item_key}:version"
    primary_read_marker_key = f"{item_key}:primary-read"
    cache_key = item_key
    read_from_primary = False

    if bypass_cache:
        cache_operation_total.labels(endpoint="get_item", result="bypass").inc()
    elif cache is None:
        cache_operation_total.labels(endpoint="get_item", result="unavailable").inc()

    if cache:
        try:
            generation = current_generation(cache, generation_key)
            cache_key = versioned_cache_key(item_key, generation)
            read_from_primary = requires_primary_read(cache, primary_read_marker_key)
            if read_from_primary:
                cache_operation_total.labels(
                    endpoint="get_item", result="consistency_primary"
                ).inc()
                logger.info(
                    f"cache_bypass endpoint=get_item item_id={item_id} reason=recent_write"
                )
            else:
                cached = cache.get(cache_key)
                if cached:
                    cache_hit_total.labels(endpoint="get_item").inc()
                    cache_operation_total.labels(endpoint="get_item", result="hit").inc()
                    logger.info(f"cache_hit endpoint=get_item item_id={item_id}")
                    return ItemResponse(**json.loads(cached))
                cache_miss_total.labels(endpoint="get_item").inc()
                cache_operation_total.labels(endpoint="get_item", result="miss").inc()
                logger.info(f"cache_miss endpoint=get_item item_id={item_id}")
        except (RedisError, json.JSONDecodeError) as e:
            cache_operation_total.labels(endpoint="get_item", result="error").inc()
            logger.warning(f"캐시 조회 실패, DB 직접 조회: {e}")
            cache = None
            read_from_primary = True

    db = write_db if read_from_primary else read_db
    start = time.perf_counter()
    _inject_read_delay(db, read_delay_ms)
    record = db.query(Item).filter(Item.id == item_id).first()
    db_query_latency_seconds.labels(operation="select_one").observe(
        time.perf_counter() - start
    )

    if not record:
        raise HTTPException(status_code=404, detail=f"id={item_id} 아이템을 찾을 수 없습니다.")

    db_route = "primary" if read_from_primary else "replica"
    logger.info(f"db_route={db_route} operation=select_one item_id={item_id}")

    result = ItemResponse.from_orm_custom(record)

    if cache:
        try:
            cache.setex(cache_key, ITEM_TTL, json.dumps(result.model_dump()))
        except RedisError as e:
            logger.warning(f"캐시 저장 실패 (무시): {e}")

    return result


# ── DELETE: 캐시 무효화 필수 ────────────────────────────────────
@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: WriteDb):
    """[Primary] 아이템 삭제 — 단건과 목록 캐시 세대를 증가시킨다."""
    start = time.perf_counter()
    record = db.query(Item).filter(Item.id == item_id).first()

    if not record:
        raise HTTPException(status_code=404, detail=f"id={item_id} 아이템을 찾을 수 없습니다.")

    cache = get_redis()
    if cache:
        try:
            begin_primary_read_window(
                cache,
                (f"item:{item_id}:primary-read", LIST_PRIMARY_READ_MARKER_KEY),
                settings.REPLICA_CONSISTENCY_WINDOW_SECONDS,
            )
        except RedisError as e:
            logger.warning(f"Primary 조회 창 설정 실패: {e}")
            cache = None

    db.delete(record)
    db.commit()

    logger.info(f"db_route=primary operation=delete item_id={item_id}")

    db_query_latency_seconds.labels(operation="delete").observe(
        time.perf_counter() - start
    )

    if cache:
        try:
            advance_generations(
                cache,
                (f"item:{item_id}:version", LIST_GENERATION_KEY),
                (f"item:{item_id}", LIST_KEY),
            )
        except RedisError as e:
            logger.warning(f"캐시 세대 증가 실패: {e}")

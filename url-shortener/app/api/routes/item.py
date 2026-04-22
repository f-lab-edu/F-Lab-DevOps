import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import get_redis
from app.core.database import get_write_db, get_read_db
from app.core.metrics import (
    cache_hit_total,
    cache_miss_total,
    db_query_latency_seconds,
)
from app.models.item import Item

router = APIRouter(prefix="/items", tags=["items"])
logger = logging.getLogger(__name__)

ITEM_TTL   = 300   # 단건 조회 캐시 TTL: 5분
LIST_TTL   = 60    # 목록 조회 캐시 TTL: 1분 (변경 가능성 높아 짧게)
LIST_KEY   = "items:all"

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
def create_item(body: ItemCreate, db: Session = Depends(get_write_db)):
    """[Primary] 아이템 생성 — 목록 캐시 무효화."""
    start = time.perf_counter()

    record = Item(name=body.name, description=body.description)
    db.add(record)
    db.commit()
    db.refresh(record)

    db_query_latency_seconds.labels(operation="insert").observe(
        time.perf_counter() - start
    )

    # 목록 캐시 무효화 (새 항목이 추가됐으므로 state)
    cache = get_redis()
    if cache:
        try:
            cache.delete(LIST_KEY)
        except Exception as e:
            logger.warning(f"캐시 무효화 실패 (무시): {e}")

    return ItemResponse.from_orm_custom(record)


# ── GET 목록: Cache-Aside ────────────────────────────────────────
@router.get("", response_model=list[ItemResponse])
def list_items(db: Session = Depends(get_read_db)):
    """[Replica] 아이템 목록 — Cache-Aside (TTL: 1분)."""
    cache = get_redis()

    if cache:
        try:
            cached = cache.get(LIST_KEY)
            if cached:
                cache_hit_total.labels(endpoint="list_items").inc()
                return [ItemResponse(**i) for i in json.loads(cached)]
            cache_miss_total.labels(endpoint="list_items").inc()
        except Exception as e:
            logger.warning(f"캐시 조회 실패, DB 직접 조회: {e}")

    start = time.perf_counter()
    items = db.query(Item).all()
    db_query_latency_seconds.labels(operation="select_all").observe(
        time.perf_counter() - start
    )

    result = [ItemResponse.from_orm_custom(i) for i in items]

    if cache:
        try:
            cache.setex(LIST_KEY, LIST_TTL, json.dumps([r.model_dump() for r in result]))
        except Exception as e:
            logger.warning(f"캐시 저장 실패 (무시): {e}")

    return result


# ── GET /_db: Write/Read 분리 진단 ───────────────────────────────
# /{item_id} 보다 먼저 등록해야 라우트 충돌 방지
@router.get("/_db", response_model=DbProbeResponse)
def probe_db(
    write_db: Session = Depends(get_write_db),
    read_db: Session = Depends(get_read_db),
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
def get_item(item_id: int, db: Session = Depends(get_read_db)):
    """[Replica] 아이템 단건 조회 — Cache-Aside (TTL: 5분)."""
    cache = get_redis()
    cache_key = f"item:{item_id}"

    if cache:
        try:
            cached = cache.get(cache_key)
            if cached:
                cache_hit_total.labels(endpoint="get_item").inc()
                return ItemResponse(**json.loads(cached))
            cache_miss_total.labels(endpoint="get_item").inc()
        except Exception as e:
            logger.warning(f"캐시 조회 실패, DB 직접 조회: {e}")

    start = time.perf_counter()
    record = db.query(Item).filter(Item.id == item_id).first()
    db_query_latency_seconds.labels(operation="select_one").observe(
        time.perf_counter() - start
    )

    if not record:
        raise HTTPException(status_code=404, detail=f"id={item_id} 아이템을 찾을 수 없습니다.")

    result = ItemResponse.from_orm_custom(record)

    if cache:
        try:
            cache.setex(cache_key, ITEM_TTL, json.dumps(result.model_dump()))
        except Exception as e:
            logger.warning(f"캐시 저장 실패 (무시): {e}")

    return result


# ── DELETE: 캐시 무효화 필수 ────────────────────────────────────
@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_write_db)):
    """[Primary] 아이템 삭제 — 단건 + 목록 캐시 무효화."""
    start = time.perf_counter()
    record = db.query(Item).filter(Item.id == item_id).first()

    if not record:
        raise HTTPException(status_code=404, detail=f"id={item_id} 아이템을 찾을 수 없습니다.")

    db.delete(record)
    db.commit()

    db_query_latency_seconds.labels(operation="delete").observe(
        time.perf_counter() - start
    )

    cache = get_redis()
    if cache:
        try:
            cache.delete(f"item:{item_id}")   # 단건 캐시
            cache.delete(LIST_KEY)            # 목록 캐시
        except Exception as e:
            logger.warning(f"캐시 무효화 실패 (무시): {e}")

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_write_db, get_read_db
from app.models.item import Item

router = APIRouter(prefix="/items", tags=["items"])


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


# ── 엔드포인트 ───────────────────────────────────────────────

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


@router.post("", response_model=ItemResponse, status_code=201)
def create_item(body: ItemCreate, db: Session = Depends(get_write_db)):
    """[DB 쓰기 — Primary] 아이템을 DB에 저장."""
    record = Item(name=body.name, description=body.description)
    db.add(record)
    db.commit()
    db.refresh(record)
    return ItemResponse.from_orm_custom(record)


@router.get("", response_model=list[ItemResponse])
def list_items(db: Session = Depends(get_read_db)): 
    """[DB 읽기 — Replica] 저장된 아이템 목록을 반환."""
    return [ItemResponse.from_orm_custom(i) for i in db.query(Item).all()]


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(item_id: int, db: Session = Depends(get_read_db)): 
    """[DB 단건 읽기] ID로 특정 아이템을 조회."""
    record = db.query(Item).filter(Item.id == item_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"id={item_id} 아이템을 찾을 수 없습니다.")
    return ItemResponse.from_orm_custom(record)


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_write_db)): 
    """[DB 삭제] ID로 특정 아이템을 삭제합니다."""
    record = db.query(Item).filter(Item.id == item_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"id={item_id} 아이템을 찾을 수 없습니다.")
    db.delete(record)
    db.commit()

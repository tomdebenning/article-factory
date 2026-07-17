from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.routes.admin import require_api_key
from article_factory.schemas import StandingOrderBody
from article_factory.services.assignment_desk import (
    list_standing_orders_for_desk,
    standing_order_payload,
    upsert_standing_order,
)
from article_factory.services.shift_windows import SHIFT_ORDER

router = APIRouter(prefix="/api/standing-orders", dependencies=[Depends(require_api_key)])


@router.get("")
def list_standing_orders(
    desk_path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> dict:
    orders = list_standing_orders_for_desk(db, desk_path=desk_path)
    by_shift = {order.shift_key: standing_order_payload(order) for order in orders}
    return {
        "desk_path": desk_path.strip(),
        "shifts": [by_shift.get(key) or {"desk_path": desk_path.strip(), "shift_key": key, "topics": [], "target_count": None} for key in SHIFT_ORDER],
    }


@router.put("")
def put_standing_order(body: StandingOrderBody, db: Session = Depends(get_db)) -> dict:
    shift_key = body.shift_key.strip().lower()
    if shift_key not in SHIFT_ORDER:
        raise HTTPException(status_code=400, detail="Invalid shift key")
    if not body.desk_path.strip():
        raise HTTPException(status_code=400, detail="desk_path is required")
    target = body.target_count
    if target is not None and target < 0:
        raise HTTPException(status_code=400, detail="target_count must be zero or greater")
    order = upsert_standing_order(
        db,
        desk_path=body.desk_path,
        shift_key=shift_key,
        topics=body.topics,
        target_count=target,
    )
    db.commit()
    db.refresh(order)
    return {"order": standing_order_payload(order)}

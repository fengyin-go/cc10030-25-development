"""复检办理接口：筛选清单、安排回执、受理核验、周期更正与办理号恢复。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import RecheckArrangePayload, RecheckCyclePayload
from app.services import recheck

router = APIRouter(prefix="/api/recheck", tags=["复检办理"])


@router.get("")
def list_due_rechecks(
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
    include_outside: bool = Query(default=False),
    include_overdue: bool = Query(default=False),
    status: str | None = Query(default=None),
    vessel: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> dict[str, Any]:
    """未来 N 日复检清单：按复检期限升序排序，首条即最紧急的冷却复检。"""
    return recheck.locate_due_rechecks(
        days,
        include_outside=include_outside,
        include_overdue=include_overdue,
        status=status,
        vessel=vessel,
        keyword=keyword,
    )


@router.get("/list")
def list_alias(
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
    include_outside: bool = Query(default=False),
    status: str | None = Query(default=None),
    vessel: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> dict[str, Any]:
    """复检办理清单别名接口，语义与 /api/recheck 一致。"""
    return recheck.locate_due_rechecks(
        days,
        include_outside=include_outside,
        status=status,
        vessel=vessel,
        keyword=keyword,
    )


@router.get("/due")
def due_alias(
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """到期复检定位别名接口。"""
    return recheck.locate_due_rechecks(days, status=status)


@router.get("/inspection/{inspection_id}")
def inspection_recheck(
    inspection_id: int,
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
) -> dict[str, Any]:
    """检验页：返回该检验当前的复检办理、状态和三种结果说明。"""
    from app.store import store
    inspection = store.find(recheck.INSPECTION_MODULE, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail=f"检验记录 {inspection_id} 不存在或已归档")
    with recheck._LOCK:
        handling = recheck._latest_handling(inspection_id=inspection_id)
        maintenance = recheck._maintenance_for_inspection(inspection)
        if handling is not None:
            recheck.refresh_handling_status(handling, maintenance=maintenance, days=days)
    return {
        "ok": handling is not None,
        "inspection": inspection,
        "maintenance": maintenance,
        "handling": handling,
        "办理号": handling.get("办理号", "") if handling else "",
        "受理编号": handling.get("受理编号", "") if handling else "",
        "status": handling.get("status", recheck.STATUS_UNSCHEDULED) if handling else recheck.STATUS_UNSCHEDULED,
        "receipt": handling.get("回执", {}) if handling else {},
    }


@router.get("/handlings")
def list_handlings(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
) -> dict[str, Any]:
    items, total = recheck.list_handlings(status=status, page=page, size=size, days=days)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/handlings/{handling_id}")
def get_handling(
    handling_id: int,
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
) -> dict[str, Any]:
    from app.store import store
    with recheck._LOCK:
        handling = store.find(recheck.RECHECK_MODULE, handling_id)
        if handling is None:
            raise HTTPException(status_code=404, detail=f"复检办理 {handling_id} 不存在或已归档")
        recheck.refresh_handling_status(handling, days=days)
    return handling


@router.get("/maintenance/{maintenance_id}")
def recover_for_maintenance(
    maintenance_id: int,
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
) -> dict[str, Any]:
    """从原保养工单恢复复检办理号、原检验和原保养周期。"""
    result = recheck.recover_for_maintenance(maintenance_id)
    handling, error, status = recheck.verification_context(
        maintenance_id, days=days)
    result["verification"] = {
        "status": status,
        "message": error,
        "can_verify": not error and status == recheck.STATUS_ACCEPTED,
        "handling_id": int(handling.get("id", 0)) if handling else None,
        "受理编号": handling.get("受理编号", "") if handling else "",
    }
    return result


@router.get("/maintenance/{maintenance_id}/verification")
def maintenance_verification(
    maintenance_id: int,
    handling_id: int | None = Query(default=None),
    days: int = Query(default=recheck.WINDOW_DAYS, ge=0, le=365),
) -> dict[str, Any]:
    """保养页核验入口：返回是否可核验、当前三种状态和原检验证据。"""
    handling, error, status = recheck.verification_context(
        maintenance_id, handling_id=handling_id, days=days)
    return {
        "ok": not error,
        "can_verify": not error and status == recheck.STATUS_ACCEPTED,
        "message": error,
        "status": status,
        "handling": handling,
        "受理编号": handling.get("受理编号", "") if handling else "",
        "evidence": handling.get("证据", {}) if handling else {},
    }


@router.post("/arrange")
def arrange(payload: RecheckArrangePayload) -> dict[str, Any]:
    """发出复检安排请求：并发/重复提交同一复检只登记一次。"""
    return recheck.arrange_recheck(
        inspection_id=payload.inspection_id,
        maintenance_id=payload.maintenance_id,
        handling_id=payload.handling_id,
        days=payload.days,
        snapshot=payload.snapshot,
        expected_due=payload.expected_due,
        remark=payload.remark,
    )


@router.post("/{handling_id}/accept")
def accept(
    handling_id: int,
    days: int | None = Query(default=None, ge=0, le=365),
) -> dict[str, Any]:
    """受理安排：从有受理编号的冷却项目取实际证据。"""
    return recheck.accept_recheck(handling_id, days=days)


@router.post("/maintenance/{maintenance_id}/cycle")
def correct_cycle(maintenance_id: int, payload: RecheckCyclePayload) -> dict[str, Any]:
    """更正保养周期/补报下次保养日期，并把关联复检重新定位到窗口内外。"""
    return recheck.update_maintenance_cycle(
        maintenance_id,
        next_date=payload.next_date,
        cycle=payload.cycle,
        days=payload.days,
    )

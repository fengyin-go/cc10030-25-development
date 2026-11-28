"""船舶检验领域服务：检验记录本身 + 复检办理（安排、受理、重试）的业务入口。"""
from __future__ import annotations

from typing import Any

from app.services import recheck
from app.services.base import BaseModuleService


class InspectionService(BaseModuleService):
    MODULE = "inspection"
    ENTITY = "检验记录"
    OPERATION_TERM = "确认检验"
    LIST_FIELDS = ["船舶编号", "检验类型", "检验机构", "检验日期", "有效期至", "检验结论", "缺陷项目", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "检验类型", "检验机构"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已过期"]
    ACTION_RULES = {"确认检验": "已确认", "安排复检": "待确认", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    def handle_action(
        self,
        entry_id: int,
        action: str,
        payload: Any | None = None,
    ) -> dict[str, Any]:
        if action in ("安排复检", "重试安排", "受理复检"):
            entry = self.get_entry(entry_id)
            if entry is None:
                return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在或已归档"}

            days = getattr(payload, "days", None)
            expected_due = getattr(payload, "expected_due", None)
            snapshot = getattr(payload, "snapshot", None)
            handling_id = getattr(payload, "handling_id", None)

            if action == "受理复检":
                if handling_id is None:
                    latest = recheck._latest_handling(inspection_id=entry_id)
                    handling_id = int(latest.get("id", 0)) if latest else None
                if handling_id is None:
                    return {"ok": False, "message": "该检验尚无复检安排请求，不能受理"}
                result = recheck.accept_recheck(handling_id, days=days)
            elif action == "重试安排":
                result = recheck.retry_arrange(
                    inspection_id=entry_id,
                    handling_id=handling_id,
                    days=days,
                    expected_due=expected_due,
                    snapshot=snapshot,
                )
            else:
                result = recheck.arrange_recheck(
                    inspection_id=entry_id,
                    handling_id=handling_id,
                    days=days,
                    expected_due=expected_due,
                    snapshot=snapshot,
                )

            handling = result.get("handling")
            if handling:
                # 原检验记录只做反查标注，不复制一条新的检验记录。
                entry["复检办理号"] = handling.get("办理号", "")
                entry["复检受理编号"] = handling.get("受理编号", "")
                entry["复检安排状态"] = result.get("status", "")
                if result.get("status") == recheck.STATUS_REQUESTED:
                    # 安排请求发出后回到“待确认”，沿用原检验的动作语义。
                    entry["status"] = "待确认"
                    entry["pending"] = True
                    entry["abnormal"] = False
            return result
        return super().handle_action(entry_id, action, payload)

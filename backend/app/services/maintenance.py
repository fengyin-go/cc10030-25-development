"""船舶保养领域服务：保养核验入口关联复检办理清单，周期更正后重试安排。"""
from __future__ import annotations

from typing import Any

from app.services import recheck
from app.services.base import BaseModuleService


class MaintenanceService(BaseModuleService):
    MODULE = "maintenance"
    ENTITY = "保养记录"
    OPERATION_TERM = "核验保养"
    LIST_FIELDS = ["船舶编号", "保养项目", "保养周期", "上次保养", "下次保养", "负责人员", "保养状态", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "保养项目", "保养周期"]
    STATUS_ORDER = ["草稿", "待核验", "已核验", "已逾期"]
    ACTION_RULES = {"核验保养": "已核验", "调整周期": "待核验", "标记逾期": "已逾期"}
    NEGATIVE_ACTIONS = []

    def handle_action(
        self,
        entry_id: int,
        action: str,
        payload: Any | None = None,
    ) -> dict[str, Any]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在或已归档"}

        days = getattr(payload, "days", None)
        handling_id = getattr(payload, "handling_id", None)
        values = getattr(payload, "values", None) or {}

        if action == "核验保养":
            # 保养工单的核验入口必须先关联已受理的复检办理，再取原检验证据。
            return recheck.verify_maintenance_with_recheck(
                entry_id, handling_id=handling_id, days=days)

        if action == "调整周期":
            next_date = getattr(payload, "next_date", None) or values.get("下次保养")
            cycle = getattr(payload, "cycle", None) or values.get("保养周期")
            result = recheck.update_maintenance_cycle(
                entry_id, next_date=next_date, cycle=cycle, days=days)
            if result.get("ok"):
                entry["status"] = "待核验"
                entry["pending"] = True
                entry["abnormal"] = False
                entry["保养状态"] = "待核验"
            return result

        if action in ("重试安排", "安排复检"):
            return recheck.retry_arrange(
                maintenance_id=entry_id,
                handling_id=handling_id,
                days=days,
                expected_due=getattr(payload, "expected_due", None),
                snapshot=getattr(payload, "snapshot", None),
            )

        return super().handle_action(entry_id, action, payload)

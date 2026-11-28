"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None
    # 复检办理动作的附加回执：安排回执、办理号、受理编号和三种办理状态。
    receipt: dict[str, Any] | None = None
    handling: dict[str, Any] | None = None
    status: str | None = None
    duplicate: bool = False
    data: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    # 复检办理动作的可选定位参数：清单快照、预期复检期限、办理号和窗口天数。
    snapshot: str | None = None
    expected_due: str | None = None
    handling_id: int | None = None
    days: int | None = None
    next_date: str | None = None
    cycle: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class RecheckArrangePayload(BaseModel):
    inspection_id: int | None = None
    maintenance_id: int | None = None
    handling_id: int | None = None
    days: int | None = None
    snapshot: str | None = None
    expected_due: str | None = None
    remark: str = ""


class RecheckCyclePayload(BaseModel):
    next_date: str | None = None
    cycle: str | None = None
    days: int | None = None

"""复检办理领域服务：把船舶检验与保养计划串成一条可核验的办理链。

业务要点：
- 按保养计划的“下次保养”日期定位未来 N 日内需要复检的检验项目，并按到期日排序；
- 同一检验（同一保养计划）并发提交安排只登记一条复检办理，绝不复制检验记录；
- 办理状态区分：已发出安排请求 / 已受理（可核验） / 等待周期更正；
- 保养日期被补报后，原先排在窗口内的检验会移出窗口，未受理的旧安排不能继续核验；
- 受理时从原检验记录（冷却项目）取实际证据，并保留原检验与原保养周期快照；
- 办理号可以从原保养工单反查恢复，重复安排永远返回同一个办理号。
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

# 清单窗口天数：需求里的“未来 7 日”。
WINDOW_DAYS = 7

# 三种办理结果，前端和接口都直接使用这三个词。
STATUS_REQUESTED = "已发出安排请求"
STATUS_ACCEPTED = "已受理"
STATUS_WAITING = "等待周期更正"
STATUS_UNSCHEDULED = "未安排"

RECHECK_MODULE = "recheck"
INSPECTION_MODULE = "inspection"
MAINTENANCE_MODULE = "maintenance"

_LOCK = threading.RLock()
_WINDOW_VERSION = 0
_SEQUENCE = 0
_ACCEPT_SEQUENCE = 0
# 清单快照：记录生成时每个检验对应的复检期限，用于识别“旧清单”。
_SNAPSHOTS: dict[str, dict[int, str]] = {}

_PROJECT_TOKENS = (
    "冷却", "消防", "船体", "救生", "电气", "主机", "辅机", "舵", "锚",
    "锅炉", "防污", "通信", "导航", "液压", "空调", "泵",
)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def today() -> date:
    """统一取当天日期，方便测试时只替换这一处。"""
    return date.today()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_cycle_days(value: Any) -> int | None:
    """把“180天 / 6个月 / 1年”解析成天数。"""
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    amount = int(digits)
    if "年" in text:
        return amount * 365
    if "月" in text:
        return amount * 30
    return amount


def _window_days(days: int | None) -> int:
    try:
        value = int(days if days is not None else WINDOW_DAYS)
    except (TypeError, ValueError):
        value = WINDOW_DAYS
    return max(0, value)


def _next_id() -> int:
    rows = store.rows(RECHECK_MODULE)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_number(prefix: str) -> str:
    global _SEQUENCE, _ACCEPT_SEQUENCE
    if prefix == "SL":
        _ACCEPT_SEQUENCE += 1
        seq = _ACCEPT_SEQUENCE
    else:
        _SEQUENCE += 1
        seq = _SEQUENCE
    return f"{prefix}-{today().strftime('%Y%m%d')}-{seq:03d}"


def _snapshot(days: int) -> str:
    return f"v{_WINDOW_VERSION}-{today().isoformat()}-{days}"


def window_info(days: int | None = None) -> dict[str, Any]:
    days = _window_days(days)
    start = today()
    return {
        "days": days,
        "window_days": days,
        "window_start": start.isoformat(),
        "deadline": (start + timedelta(days=days)).isoformat(),
        "snapshot": _snapshot(days),
        "window_version": _WINDOW_VERSION,
        "generated_at": _now(),
    }


def _maintenance_due(maintenance: dict[str, Any] | None) -> date | None:
    if not maintenance:
        return None
    due = parse_date(maintenance.get("下次保养"))
    if due is not None:
        return due
    last = parse_date(maintenance.get("上次保养"))
    cycle_days = parse_cycle_days(maintenance.get("保养周期"))
    if last is not None and cycle_days is not None:
        return last + timedelta(days=cycle_days)
    return None


def _project_matches(inspection_type: str, project: str) -> bool:
    left = str(inspection_type or "").strip()
    right = str(project or "").strip()
    if not left or not right:
        return True
    if left in right or right in left:
        return True
    return any(token in left and token in right for token in _PROJECT_TOKENS)


def _maintenance_for_inspection(inspection: dict[str, Any]) -> dict[str, Any] | None:
    linked_id = inspection.get("关联保养") or inspection.get("保养编号")
    if linked_id not in (None, ""):
        linked = store.find(MAINTENANCE_MODULE, _to_int(linked_id))
        if linked is not None:
            return linked

    vessel = str(inspection.get("船舶编号", ""))
    candidates = [
        row for row in store.rows(MAINTENANCE_MODULE)
        if str(row.get("船舶编号", "")) == vessel
    ]
    matched = [
        row for row in candidates
        if _project_matches(str(inspection.get("检验类型", "")), str(row.get("保养项目", "")))
    ]
    pool = matched or candidates
    if not pool:
        return None
    # 有多个保养计划时，优先取最近到期的那个。
    pool.sort(key=lambda row: (_maintenance_due(row) or date.max, int(row.get("id", 0))))
    return pool[0]


def _handlings(maintenance_id: int | None = None) -> list[dict[str, Any]]:
    rows = list(store.rows(RECHECK_MODULE))
    if maintenance_id is not None:
        rows = [row for row in rows if _to_int(row.get("保养编号")) == maintenance_id]
    rows.sort(key=lambda row: int(row.get("id", 0)))
    return rows


def _latest_handling(
    inspection_id: int | None = None,
    maintenance_id: int | None = None,
) -> dict[str, Any] | None:
    rows = list(store.rows(RECHECK_MODULE))
    if inspection_id is not None:
        rows = [row for row in rows if _to_int(row.get("检验编号")) == inspection_id]
    if maintenance_id is not None:
        rows = [row for row in rows if _to_int(row.get("保养编号")) == maintenance_id]
    if not rows:
        return None
    rows.sort(key=lambda row: int(row.get("id", 0)))
    return rows[-1]


def _is_accepted(handling: dict[str, Any] | None) -> bool:
    return bool(handling) and str(handling.get("status")) == STATUS_ACCEPTED


def _stale_snapshot(snapshot: str | None, inspection_id: int, due: date | None) -> bool:
    """旧清单判定：快照里记录的复检期限与当前保养计划不一致即视为过期。"""
    if not snapshot:
        return False
    recorded = _SNAPSHOTS.get(snapshot)
    if recorded is None:
        return False
    if inspection_id not in recorded:
        return True
    return recorded[inspection_id] != (due.isoformat() if due else "")


def _in_window(due: date | None, days: int) -> bool:
    if due is None:
        return False
    start = today()
    return start <= due <= start + timedelta(days=days)


def _effective_status(
    handling: dict[str, Any],
    maintenance: dict[str, Any] | None,
    days: int,
) -> str:
    """把持久化状态换算成当前清单里的三种结果之一。"""
    if str(handling.get("status")) == STATUS_ACCEPTED:
        return STATUS_ACCEPTED
    due = _maintenance_due(maintenance) if maintenance else parse_date(handling.get("复检期限"))
    if due is None or not _in_window(due, days):
        return STATUS_WAITING
    requested = str(handling.get("status", STATUS_REQUESTED))
    return requested if requested in (STATUS_REQUESTED, STATUS_WAITING) else STATUS_REQUESTED


def refresh_handling_status(
    handling: dict[str, Any],
    maintenance: dict[str, Any] | None = None,
    days: int | None = None,
) -> str:
    """把换算后的状态写回办理单，保证单条查询与清单口径一致。"""
    days = _window_days(days)
    if maintenance is None:
        maintenance = store.find(MAINTENANCE_MODULE, _to_int(handling.get("保养编号")))
    if _is_accepted(handling):
        return STATUS_ACCEPTED
    status = _effective_status(handling, maintenance, days)
    handling["status"] = status
    handling["pending"] = True
    handling["abnormal"] = status == STATUS_WAITING
    if status == STATUS_WAITING:
        due = _maintenance_due(maintenance)
        handling["当前下次保养"] = due.isoformat() if due else ""
        if not handling.get("等待更正原因"):
            handling["等待更正原因"] = "保养日期已超出当前复检窗口"
    return status


def _inspection_evidence(
    inspection: dict[str, Any],
    acceptance_no: str = "",
) -> dict[str, Any]:
    is_cooling = "冷却" in str(inspection.get("检验类型", ""))
    return {
        "来源检验编号": inspection.get("id"),
        "检验类型": inspection.get("检验类型", ""),
        "检验机构": inspection.get("检验机构", ""),
        "检验日期": inspection.get("检验日期", ""),
        "有效期至": inspection.get("有效期至", ""),
        "检验结论": inspection.get("检验结论", ""),
        "缺陷项目": inspection.get("缺陷项目", ""),
        "是否冷却": is_cooling,
        "受理编号": acceptance_no,
    }


def _candidate_item(
    inspection: dict[str, Any],
    maintenance: dict[str, Any],
    handling: dict[str, Any] | None,
    days: int,
) -> dict[str, Any]:
    due = _maintenance_due(maintenance)
    due_text = due.isoformat() if due else ""
    status = STATUS_UNSCHEDULED
    if handling is not None:
        status = _effective_status(handling, maintenance, days)
    return {
        "id": int(handling.get("id", 0)) if handling else None,
        "handling_id": int(handling.get("id", 0)) if handling else None,
        "inspection_id": _to_int(inspection.get("id")),
        "maintenance_id": _to_int(maintenance.get("id")),
        "检验编号": inspection.get("id"),
        "保养编号": maintenance.get("id"),
        "船舶编号": inspection.get("船舶编号", ""),
        "检验类型": inspection.get("检验类型", ""),
        "保养项目": maintenance.get("保养项目", ""),
        "复检期限": due_text,
        "剩余天数": (due - today()).days if due else None,
        "排序": 0,
        "status": status,
        "办理状态": status,
        "办理号": handling.get("办理号", "") if handling else "",
        "受理编号": handling.get("受理编号", "") if handling else "",
        "安排复检期限": handling.get("复检期限", "") if handling else due_text,
        "原检验日期": (
            handling.get("原检验日期") if handling else inspection.get("检验日期", "")
        ),
        "原有效期至": (
            handling.get("原有效期至") if handling else inspection.get("有效期至", "")
        ),
        "原检验结论": (
            handling.get("原检验结论") if handling else inspection.get("检验结论", "")
        ),
        "原缺陷项目": (
            handling.get("原缺陷项目") if handling else inspection.get("缺陷项目", "")
        ),
        "原保养周期": (
            handling.get("原保养周期") if handling else maintenance.get("保养周期", "")
        ),
        "原下次保养": (
            handling.get("原下次保养") if handling else maintenance.get("下次保养", "")
        ),
        "原检验": {
            "检验编号": inspection.get("id"),
            "检验类型": (
                handling.get("检验类型") if handling else inspection.get("检验类型", "")
            ),
            "检验机构": (
                handling.get("原检验机构") if handling else inspection.get("检验机构", "")
            ),
            "检验日期": (
                handling.get("原检验日期") if handling else inspection.get("检验日期", "")
            ),
            "有效期至": (
                handling.get("原有效期至") if handling else inspection.get("有效期至", "")
            ),
            "检验结论": (
                handling.get("原检验结论") if handling else inspection.get("检验结论", "")
            ),
            "缺陷项目": (
                handling.get("原缺陷项目") if handling else inspection.get("缺陷项目", "")
            ),
        },
        "原周期": {
            "保养周期": (
                handling.get("原保养周期") if handling else maintenance.get("保养周期", "")
            ),
            "下次保养": (
                handling.get("原下次保养") if handling else maintenance.get("下次保养", "")
            ),
        },
        "办理编号": handling.get("办理号", "") if handling else "",
        "安排时间": handling.get("安排时间", "") if handling else "",
        "受理时间": handling.get("受理时间", "") if handling else "",
        "窗口外": False,
        "是否冷却": (
            "冷却" in str(inspection.get("检验类型", ""))
            or "冷却" in str(maintenance.get("保养项目", ""))
        ),
    }


def _result(
    ok: bool,
    message: str,
    *,
    handling: dict[str, Any] | None = None,
    status: str = "",
    duplicate: bool = False,
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = status
    if handling is not None and not resolved:
        resolved = str(handling.get("status", ""))
    return {
        "ok": ok,
        "duplicate": duplicate,
        "message": message,
        "entry": handling,
        "handling": handling,
        "receipt": receipt if receipt is not None else (
            handling.get("回执", {}) if handling else {}
        ),
        "status": resolved,
    }


# ---------------------------------------------------------------------------
# 清单定位与排序
# ---------------------------------------------------------------------------

def locate_due_rechecks(
    days: int | None = None,
    *,
    include_outside: bool = False,
    include_overdue: bool = False,
    status: str | None = None,
    vessel: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """定位并排序未来 N 日内到期的复检项目。

    include_outside=True 时，把已有办理但已移出窗口的项目一并返回，
    便于页面提示“等待周期更正”，但窗口内清单仍只保留 normal 项。
    """
    with _LOCK:
        return _locate_due_rechecks_locked(
            days,
            include_outside=include_outside,
            include_overdue=include_overdue,
            status=status,
            vessel=vessel,
            keyword=keyword,
        )


def _locate_due_rechecks_locked(
    days: int | None = None,
    *,
    include_outside: bool = False,
    include_overdue: bool = False,
    status: str | None = None,
    vessel: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    days = _window_days(days)
    items: list[dict[str, Any]] = []
    moved_out: list[dict[str, Any]] = []
    accepted_items: list[dict[str, Any]] = []
    seen_handling: set[int] = set()

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for inspection in store.rows(INSPECTION_MODULE):
        maintenance = _maintenance_for_inspection(inspection)
        if maintenance is not None:
            pairs.append((inspection, maintenance))

    # 排序依据：复检期限升序；同日再按检验编号，保证每次筛选顺序稳定。
    pairs.sort(key=lambda pair: (
        _maintenance_due(pair[1]) or date.max,
        int(pair[0].get("id", 0)),
    ))

    for inspection, maintenance in pairs:
        due = _maintenance_due(maintenance)
        handling = _latest_handling(inspection_id=_to_int(inspection.get("id")))
        in_window = _in_window(due, days)
        overdue = due is not None and due < today()

        if handling is not None and _is_accepted(handling) and id(handling) not in seen_handling:
            # 已经受理的安排是既成事实：即使周期被更正到窗口外，也保留办理快照。
            seen_handling.add(id(handling))
            accepted_items.append(_candidate_item(inspection, maintenance, handling, days))

        if not in_window:
            if handling is not None and not _is_accepted(handling):
                item = _candidate_item(inspection, maintenance, handling, days)
                item["窗口外"] = True
                moved_out.append(item)
                if include_outside:
                    items.append(item)
            elif include_overdue and overdue:
                item = _candidate_item(inspection, maintenance, None, days)
                item["窗口外"] = True
                items.append(item)
            continue

        items.append(_candidate_item(inspection, maintenance, handling, days))

    filtered: list[dict[str, Any]] = []
    for item in items:
        if keyword:
            text = f"{item['船舶编号']} {item['检验类型']} {item['保养项目']}"
            if str(keyword).casefold() not in text.casefold():
                continue
        if vessel and str(vessel).casefold() not in str(item["船舶编号"]).casefold():
            continue
        if status and item["status"] != status:
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: (item["复检期限"] or "9999-12-31", int(item["检验编号"] or 0)))
    for index, item in enumerate(filtered, start=1):
        item["排序"] = index

    moved_out.sort(key=lambda item: (item["复检期限"] or "9999-12-31", int(item["检验编号"] or 0)))
    for index, item in enumerate(moved_out, start=1):
        item["排序"] = index

    accepted_items.sort(key=lambda item: (item["复检期限"] or "9999-12-31", int(item["检验编号"] or 0)))
    for index, item in enumerate(accepted_items, start=1):
        item["排序"] = index

    snapshot_id = _snapshot(days)
    _SNAPSHOTS[snapshot_id] = {
        int(item["检验编号"]): item["复检期限"]
        for item in filtered + moved_out + accepted_items
        if item.get("检验编号") is not None
    }

    first = filtered[0] if filtered else None
    first_cooling = next(
        (item for item in filtered if item.get("是否冷却")),
        None,
    )

    return {
        "items": filtered,
        "moved_out": moved_out,
        "accepted": accepted_items,
        "first": first,
        "first_cooling": first_cooling,
        "total": len(filtered),
        "outside_total": len(moved_out),
        "accepted_total": len(accepted_items),
        "page": 1,
        "size": len(filtered),
        **window_info(days),
    }


def inclusion_wanted(include_outside: bool, include_overdue: bool, overdue: bool) -> bool:
    return include_outside or (include_overdue and overdue)


# ---------------------------------------------------------------------------
# 安排 / 受理 / 周期更正 / 核验
# ---------------------------------------------------------------------------

def arrange_recheck(
    *,
    inspection_id: int | None = None,
    maintenance_id: int | None = None,
    handling_id: int | None = None,
    days: int | None = None,
    snapshot: str | None = None,
    expected_due: str | None = None,
    remark: str = "",
) -> dict[str, Any]:
    """发出复检安排请求；同一检验重复/并发提交返回同一条办理。"""
    days = _window_days(days)
    with _LOCK:
        if handling_id is not None:
            existing = store.find(RECHECK_MODULE, handling_id)
            if existing is None:
                return _result(False, f"复检办理 {handling_id} 不存在")
            inspection_id = _to_int(existing.get("检验编号"))
            maintenance_id = _to_int(existing.get("保养编号"))

        inspection_id = _to_int(inspection_id)
        if inspection_id is None:
            return _result(False, "缺少检验编号，无法安排复检")
        inspection = store.find(INSPECTION_MODULE, inspection_id)
        if inspection is None:
            return _result(False, f"检验记录 {inspection_id} 不存在或已归档")

        maintenance = store.find(MAINTENANCE_MODULE, _to_int(maintenance_id)) if maintenance_id else None
        if maintenance is None:
            maintenance = _maintenance_for_inspection(inspection)
        if maintenance is None:
            return _result(
                False, f"检验记录 {inspection_id} 没有对应的保养计划，无法安排复检")

        maintenance_id = _to_int(maintenance.get("id"))
        due = _maintenance_due(maintenance)
        # 同一检验就是同一笔复检：唯一键按检验编号去重，重复安排不会多出一条。
        existing = _latest_handling(inspection_id=inspection_id)

        # 已受理的安排是既成事实：保留原检验与原周期，不因周期变化而失效。
        if _is_accepted(existing):
            return _result(
                True,
                f"该复检已受理（受理编号 {existing.get('受理编号', '')}），"
                f"沿用原办理号 {existing.get('办理号', '')}，无需重复登记",
                handling=existing,
                status=STATUS_ACCEPTED,
                duplicate=True,
            )

        # 旧清单守卫：界面打开后值班员补报了新的保养日期，且该检验已移出窗口。
        expected = parse_date(expected_due) if expected_due not in (None, "") else None
        changed = (
            (expected is not None and due is not None and expected != due)
            or (expected is None and _stale_snapshot(snapshot, inspection_id, due))
        )
        stale = changed and not _in_window(due, days)
        if stale:
            if existing is not None:
                existing["status"] = STATUS_WAITING
                existing["pending"] = True
                existing["abnormal"] = True
                existing["当前下次保养"] = due.isoformat() if due else ""
                existing["等待更正原因"] = "值班员补报了新的保养日期，旧清单已失效"
            return _result(
                False,
                "复检期限已更新：保养计划已补报新日期，该检验已移出原清单窗口，"
                "请刷新复检办理清单后再处理",
                handling=existing,
                status=STATUS_WAITING,
            )

        # 超出当前窗口：未受理的安排转入“等待周期更正”，不能沿旧清单继续。
        if not _in_window(due, days):
            due_text = due.isoformat() if due else "未知"
            if existing is not None:
                existing["status"] = STATUS_WAITING
                existing["pending"] = True
                existing["abnormal"] = True
                existing["当前下次保养"] = due.isoformat() if due else ""
                return _result(
                    False,
                    f"该检验当前复检期限为 {due_text}，已超出未来 {days} 日窗口；"
                    "未受理的安排不能沿旧清单继续核验，请等待保养周期更正后重试",
                    handling=existing,
                    status=STATUS_WAITING,
                )
            return _result(
                False,
                f"该检验不在未来 {days} 日复检窗口内（复检期限：{due_text}）",
                status=STATUS_WAITING,
            )

        if existing is not None:
            # 并发或重复提交：唯一键命中，只更新状态，不新增办理、不复制检验记录。
            existing["保养编号"] = maintenance_id
            existing["保养项目"] = maintenance.get("保养项目", "")
            existing["status"] = STATUS_REQUESTED
            existing["pending"] = True
            existing["abnormal"] = False
            existing["当前下次保养"] = due.isoformat() if due else ""
            existing["重试安排时间"] = _now()
            if snapshot and snapshot != existing.get("清单快照"):
                existing["清单快照"] = snapshot
            maintenance["复检办理号"] = existing.get("办理号", "")
            maintenance["复检安排状态"] = STATUS_REQUESTED
            return _result(
                True,
                f"同一复检仅登记一次，沿用原办理号 {existing.get('办理号', '')}；"
                "未新增冷却复检记录",
                handling=existing,
                status=STATUS_REQUESTED,
                duplicate=True,
            )

        # 首次登记：只创建办理单，不复制、不新增检验记录。
        number = _next_number("FJ")
        receipt = {
            "回执号": f"HZ-{number.split('-', 1)[-1]}",
            "办理号": number,
            "检验编号": inspection_id,
            "保养编号": maintenance_id,
            "船舶编号": inspection.get("船舶编号", ""),
            "检验类型": inspection.get("检验类型", ""),
            "复检期限": due.isoformat() if due else "",
            "原保养周期": maintenance.get("保养周期", ""),
            "安排状态": STATUS_REQUESTED,
            "提示": "安排请求已发出，等待保养人员受理；受理后凭受理编号核验",
        }
        handling = {
            "id": _next_id(),
            "status": STATUS_REQUESTED,
            "pending": True,
            "abnormal": False,
            "办理号": number,
            "唯一键": f"inspection:{inspection_id}",
            "检验编号": inspection_id,
            "保养编号": maintenance_id,
            "船舶编号": inspection.get("船舶编号", ""),
            "检验类型": inspection.get("检验类型", ""),
            "保养项目": maintenance.get("保养项目", ""),
            "复检期限": due.isoformat() if due else "",
            "原检验日期": inspection.get("检验日期", ""),
            "原有效期至": inspection.get("有效期至", ""),
            "原检验结论": inspection.get("检验结论", ""),
            "原缺陷项目": inspection.get("缺陷项目", ""),
            "原检验机构": inspection.get("检验机构", ""),
            "原检验": _inspection_evidence(inspection),
            "原周期": {
                "保养周期": maintenance.get("保养周期", ""),
                "下次保养": maintenance.get("下次保养", ""),
            },
            "办理编号": number,
            "原保养周期": maintenance.get("保养周期", ""),
            "原下次保养": maintenance.get("下次保养", ""),
            "当前下次保养": due.isoformat() if due else "",
            "安排时间": _now(),
            "受理时间": "",
            "受理编号": "",
            "证据": {},
            "回执": receipt,
            "清单快照": snapshot or _snapshot(days),
            "窗口版本": _WINDOW_VERSION,
            "备注说明": remark,
        }
        store.rows(RECHECK_MODULE).append(handling)

        # 原保养工单上也留一个可恢复的办理号。
        maintenance["复检办理号"] = number
        maintenance["复检安排状态"] = STATUS_REQUESTED
        maintenance["复检期限"] = due.isoformat() if due else ""

        return _result(
            True,
            f"复检安排请求已发出，办理号 {number}；回执已随本次返回",
            handling=handling,
            status=STATUS_REQUESTED,
            duplicate=False,
        )


def accept_recheck(handling_id: int, days: int | None = None) -> dict[str, Any]:
    """受理安排：生成受理编号，并从原检验（冷却项目）提取实际证据。"""
    days = _window_days(days)
    with _LOCK:
        handling = store.find(RECHECK_MODULE, _to_int(handling_id))
        if handling is None:
            return _result(False, f"复检办理 {handling_id} 不存在或已归档")

        maintenance = store.find(MAINTENANCE_MODULE, _to_int(handling.get("保养编号")))
        inspection = store.find(INSPECTION_MODULE, _to_int(handling.get("检验编号")))
        if inspection is None:
            return _result(False, "原检验记录不存在，无法受理复检", handling=handling)

        if _is_accepted(handling):
            result = _result(
                True,
                f"复检已受理，沿用受理编号 {handling.get('受理编号', '')}",
                handling=handling,
                status=STATUS_ACCEPTED,
                duplicate=True,
            )
            result["受理编号"] = handling.get("受理编号", "")
            result["evidence"] = handling.get("证据", {})
            return result

        status = _effective_status(handling, maintenance, days)
        if status != STATUS_REQUESTED:
            return _result(
                False,
                "复检状态为等待周期更正：保养日期已超出复检窗口，未受理的复检不能受理；"
                "请先更正保养周期",
                handling=handling,
                status=STATUS_WAITING,
            )

        acceptance_no = _next_number("SL")
        handling["status"] = STATUS_ACCEPTED
        handling["pending"] = False
        handling["abnormal"] = False
        handling["受理编号"] = acceptance_no
        handling["受理时间"] = _now()
        handling["证据"] = _inspection_evidence(inspection, acceptance_no)
        handling["受理状态"] = STATUS_ACCEPTED
        handling["当前下次保养"] = (
            _maintenance_due(maintenance).isoformat()
            if _maintenance_due(maintenance) else ""
        )
        if maintenance is not None:
            maintenance["复检办理号"] = handling.get("办理号", "")
            maintenance["受理编号"] = acceptance_no
            maintenance["复检安排状态"] = STATUS_ACCEPTED
        result = _result(
            True,
            f"复检已受理，受理编号 {acceptance_no}；"
            "已从原冷却检验提取实际证据，可凭办理清单核验保养",
            handling=handling,
            status=STATUS_ACCEPTED,
        )
        result["evidence"] = handling.get("证据", {})
        result["受理编号"] = acceptance_no
        return result


def update_maintenance_cycle(
    maintenance_id: int,
    *,
    next_date: Any = None,
    cycle: Any = None,
    days: int | None = None,
) -> dict[str, Any]:
    """更正保养周期/补报下次保养日期，并重新定位关联复检。"""
    global _WINDOW_VERSION
    days = _window_days(days)
    with _LOCK:
        maintenance = store.find(MAINTENANCE_MODULE, _to_int(maintenance_id))
        if maintenance is None:
            return {"ok": False, "message": f"保养记录 {maintenance_id} 不存在或已归档"}

        old_next = maintenance.get("下次保养", "")
        old_cycle = maintenance.get("保养周期", "")
        new_date = parse_date(next_date) if next_date not in (None, "") else None
        if cycle not in (None, ""):
            maintenance["保养周期"] = cycle
        if new_date is not None:
            maintenance["下次保养"] = new_date.isoformat()
        elif cycle not in (None, "") and parse_cycle_days(cycle) is not None:
            base = parse_date(maintenance.get("上次保养")) or today()
            maintenance["下次保养"] = (
                base + timedelta(days=parse_cycle_days(cycle) or 0)
            ).isoformat()

        new_due = _maintenance_due(maintenance)
        _WINDOW_VERSION += 1

        affected: list[dict[str, Any]] = []
        for handling in _handlings(_to_int(maintenance_id)):
            handling["当前下次保养"] = new_due.isoformat() if new_due else ""
            handling["周期更正时间"] = _now()
            if not _is_accepted(handling) and _in_window(new_due, days):
                handling["status"] = STATUS_REQUESTED
                handling["pending"] = True
                handling["abnormal"] = False
                handling["等待更正原因"] = ""
            elif not _is_accepted(handling):
                handling["status"] = STATUS_WAITING
                handling["pending"] = True
                handling["abnormal"] = True
                handling["等待更正原因"] = (
                    f"保养日期已更正为 {new_due.isoformat() if new_due else '未知'}，"
                    f"超出未来 {days} 日复检窗口"
                )
            # 已受理的安排保持原检验/原周期快照，只更新当前期限。
            affected.append({
                "办理号": handling.get("办理号", ""),
                "status": handling.get("status", ""),
                "受理编号": handling.get("受理编号", ""),
                "原检验编号": handling.get("检验编号"),
                "原保养周期": handling.get("原保养周期", ""),
                "原下次保养": handling.get("原下次保养", ""),
                "当前下次保养": handling.get("当前下次保养", ""),
            })

        maintenance["复检安排状态"] = (
            affected[-1]["status"] if affected else maintenance.get("复检安排状态", "")
        )
        # 更正后在保养页先尝试用这笔复检完成核验：给出可核验性和证据预览。
        preview_handling, preview_error, preview_status = verification_context(
            _to_int(maintenance_id), days=days)
        verification = {
            "status": preview_status,
            "can_verify": not preview_error and preview_status == STATUS_ACCEPTED,
            "message": preview_error,
            "handling_id": int(preview_handling.get("id", 0)) if preview_handling else None,
            "受理编号": preview_handling.get("受理编号", "") if preview_handling else "",
            "evidence": preview_handling.get("证据", {}) if preview_handling else {},
        }
        return {
            "ok": True,
            "message": (
                f"保养周期已更正：{old_cycle} → {maintenance.get('保养周期', '')}，"
                f"下次保养 {old_next} → {maintenance.get('下次保养', '')}"
            ),
            "entry": maintenance,
            "affected": affected,
            "verification": verification,
            "evidence": verification.get("evidence", {}),
            "window": window_info(days),
        }


def recover_for_maintenance(maintenance_id: int) -> dict[str, Any]:
    """从原保养工单恢复复检办理号及原检验/原周期快照。"""
    with _LOCK:
        return _recover_for_maintenance_locked(maintenance_id)


def _recover_for_maintenance_locked(maintenance_id: int) -> dict[str, Any]:
    maintenance = store.find(MAINTENANCE_MODULE, _to_int(maintenance_id))
    if maintenance is None:
        return {"ok": False, "message": f"保养记录 {maintenance_id} 不存在或已归档",
                "entry": None, "办理号": ""}
    rows = _handlings(_to_int(maintenance_id))
    if not rows:
        return {
            "ok": False,
            "message": f"保养记录 {maintenance_id} 尚未关联复检办理",
            "entry": None,
            "办理号": "",
            "maintenance": maintenance,
        }
    handling = rows[-1]
    return {
        "ok": True,
        "message": f"已从原保养工单恢复复检办理号 {handling.get('办理号', '')}",
        "entry": handling,
        "handling": handling,
        "办理号": handling.get("办理号", ""),
        "受理编号": handling.get("受理编号", ""),
        "status": handling.get("status", ""),
        "receipt": handling.get("回执", {}),
        "原检验": {
            "检验编号": handling.get("检验编号"),
            "检验类型": handling.get("检验类型", ""),
            "检验日期": handling.get("原检验日期", ""),
            "有效期至": handling.get("原有效期至", ""),
            "检验结论": handling.get("原检验结论", ""),
            "缺陷项目": handling.get("原缺陷项目", ""),
            "检验机构": handling.get("原检验机构", ""),
        },
        "原保养周期": handling.get("原保养周期", ""),
        "原下次保养": handling.get("原下次保养", ""),
        "maintenance": maintenance,
    }


def verification_context(
    maintenance_id: int,
    handling_id: int | None = None,
    days: int | None = None,
) -> tuple[dict[str, Any] | None, str, str]:
    """返回 (accepted_handling, error_message, current_status)。"""
    days = _window_days(days)
    maintenance = store.find(MAINTENANCE_MODULE, _to_int(maintenance_id))
    if maintenance is None:
        return None, f"保养记录 {maintenance_id} 不存在或已归档", ""

    if handling_id is not None:
        handling = store.find(RECHECK_MODULE, _to_int(handling_id))
        if handling is None or _to_int(handling.get("保养编号")) != _to_int(maintenance_id):
            return None, f"复检办理 {handling_id} 未关联该保养工单", ""
    else:
        handling = _latest_handling(maintenance_id=_to_int(maintenance_id))

    if handling is None:
        # 只有确实存在应复检的检验时才强制走办理清单；普通保养仍可直接核验。
        for candidate in store.rows(INSPECTION_MODULE):
            linked = _maintenance_for_inspection(candidate)
            if linked is not None and _to_int(linked.get("id")) == _to_int(maintenance_id):
                return None, (
                    f"保养工单 {maintenance_id} 的核验入口必须关联复检办理清单："
                    f"检验记录 {candidate.get('id')} 尚未安排复检"
                ), STATUS_UNSCHEDULED
        return None, "", STATUS_UNSCHEDULED

    status = _effective_status(handling, maintenance, days)
    if status == STATUS_WAITING:
        return handling, (
            "复检状态为等待周期更正：保养日期已超出复检窗口，"
            "未受理的复检不能沿旧清单继续核验"
        ), status
    if status != STATUS_ACCEPTED:
        return handling, (
            "复检只发出了安排请求，尚未受理；没有受理编号，不能取用核验证据"
        ), status
    if not handling.get("受理编号"):
        return handling, "复检缺少受理编号，不能核验", status
    return handling, "", status


def verify_maintenance_with_recheck(
    maintenance_id: int,
    handling_id: int | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """保养页核验入口：必须凭已受理的复检办理，从原冷却检验取证据。"""
    with _LOCK:
        maintenance = store.find(MAINTENANCE_MODULE, _to_int(maintenance_id))
        handling, error, status = verification_context(maintenance_id, handling_id, days)
        if error:
            return {
                "ok": False,
                "message": error,
                "entry": maintenance,
                "handling": handling,
                "status": status,
                "evidence": handling.get("证据", {}) if handling else {},
            }
        if maintenance is None:
            return {"ok": False, "message": f"保养记录 {maintenance_id} 不存在或已归档",
                    "entry": None, "status": status}

        if handling is None:
            # 没有关联检验的普通保养：按原保养动作直接核验，不伪造检验证据。
            maintenance["status"] = "已核验"
            maintenance["pending"] = False
            maintenance["abnormal"] = False
            maintenance["保养状态"] = "已核验"
            maintenance["核验时间"] = _now()
            return {
                "ok": True,
                "message": "保养已核验（该工单没有关联复检检验）",
                "entry": maintenance,
                "handling": None,
                "status": STATUS_UNSCHEDULED,
                "evidence": {},
            }

        inspection = store.find(INSPECTION_MODULE, _to_int(handling.get("检验编号")))
        if inspection is None:
            return {"ok": False, "message": "原检验记录不存在，无法核验",
                    "entry": maintenance, "handling": handling, "status": status}
        evidence = dict(handling.get("证据") or _inspection_evidence(
            inspection, str(handling.get("受理编号", ""))))

        maintenance["status"] = "已核验"
        maintenance["pending"] = False
        maintenance["abnormal"] = False
        maintenance["保养状态"] = "已核验"
        maintenance["核验依据办理号"] = handling.get("办理号", "")
        maintenance["核验受理编号"] = handling.get("受理编号", "")
        maintenance["核验证据"] = evidence
        maintenance["核验时间"] = _now()
        handling["核验时间"] = maintenance["核验时间"]

        return {
            "ok": True,
            "message": (
                f"保养已核验：凭复检办理号 {handling.get('办理号', '')}、"
                f"受理编号 {handling.get('受理编号', '')} 从原检验取用证据"
            ),
            "entry": maintenance,
            "handling": handling,
            "status": STATUS_ACCEPTED,
            "evidence": evidence,
        }


def retry_arrange(
    *,
    inspection_id: int | None = None,
    maintenance_id: int | None = None,
    handling_id: int | None = None,
    days: int | None = None,
    expected_due: str | None = None,
    snapshot: str | None = None,
) -> dict[str, Any]:
    """从检验页或保养页重试安排，语义与 arrange_recheck 一致（幂等）。"""
    if handling_id is None and inspection_id is None and maintenance_id is not None:
        rows = _handlings(_to_int(maintenance_id))
        if rows:
            handling_id = int(rows[-1].get("id", 0))
        else:
            for inspection in store.rows(INSPECTION_MODULE):
                linked = _maintenance_for_inspection(inspection)
                if linked is not None and _to_int(linked.get("id")) == _to_int(maintenance_id):
                    inspection_id = _to_int(inspection.get("id"))
                    break
    return arrange_recheck(
        inspection_id=inspection_id,
        maintenance_id=maintenance_id,
        handling_id=handling_id,
        days=days,
        expected_due=expected_due,
        snapshot=snapshot,
    )


def list_handlings(
    *,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    days: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """复检办理清单（按办理 id 倒序，供独立办理页使用）。"""
    with _LOCK:
        for handling in store.rows(RECHECK_MODULE):
            refresh_handling_status(handling, days=days)
        rows = list(store.rows(RECHECK_MODULE))
    if status:
        rows = [row for row in rows if row.get("status") == status]
    rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
    total = len(rows)
    start = max(0, (page - 1) * size)
    return rows[start:start + size], total


# 语义更直白的别名，路由与前端都可按“复检办理清单”来引用。
recheck_list = locate_due_rechecks
recover_recheck = recover_for_maintenance

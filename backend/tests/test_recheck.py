"""复检办理链路测试：窗口筛选排序、并发去重、三种状态、证据核验与办理号恢复。"""
from __future__ import annotations

import threading
from datetime import date, timedelta

import pytest

from app.services import recheck
from app.services.inspection import InspectionService
from app.services.maintenance import MaintenanceService
from app.store import store


def _reset() -> None:
    """每个用例从同一份初始台账开始，避免办理记录互相污染。"""
    from app.seed import SEED_ROWS

    for name in ("inspection", "maintenance", "recheck"):
        store.rows(name)[:] = [dict(row) for row in SEED_ROWS[name]]
    recheck._SNAPSHOTS.clear()


def _shift(maintenance_id: int, offset: int) -> None:
    store.find("maintenance", maintenance_id)["下次保养"] = (
        date.today() + timedelta(days=offset)
    ).isoformat()


@pytest.fixture(autouse=True)
def clean_state():
    _reset()
    yield
    _reset()


class TestDueWindow:
    def test_filters_future_seven_days_and_orders_by_due_date(self):
        result = recheck.locate_due_rechecks(7)
        assert result["total"] >= 2
        dues = [item["复检期限"] for item in result["items"]]
        assert dues == sorted(dues)
        assert result["first"]["检验类型"] == "冷却系统检验"
        assert [item["排序"] for item in result["items"]] == list(
            range(1, result["total"] + 1)
        )

    def test_items_outside_window_are_excluded(self):
        result = recheck.locate_due_rechecks(7)
        types = {item["检验类型"] for item in result["items"]}
        assert "船体结构检验" not in types
        assert "主机检验" not in types

    def test_overdue_item_not_in_plain_window(self):
        result = recheck.locate_due_rechecks(7)
        assert 4 not in {item["检验编号"] for item in result["items"]}

    def test_window_days_are_configurable(self):
        result = recheck.locate_due_rechecks(1)
        assert all(item["剩余天数"] <= 1 for item in result["items"])
        assert result["days"] == 1


class TestArrangeOnce:
    def test_first_cooling_recheck_returns_receipt(self):
        listing = recheck.locate_due_rechecks(7)
        first = listing["first"]
        result = recheck.arrange_recheck(
            inspection_id=first["检验编号"], days=7, snapshot=listing["snapshot"]
        )
        assert result["ok"] is True
        assert result["status"] == recheck.STATUS_REQUESTED
        assert result["receipt"]["办理号"] == result["handling"]["办理号"]
        assert result["receipt"]["安排状态"] == recheck.STATUS_REQUESTED

    def test_duplicate_arrange_keeps_single_handling(self):
        first = recheck.locate_due_rechecks(7)["first"]
        first_result = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        second_result = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        assert second_result["ok"] is True
        assert second_result["duplicate"] is True
        assert second_result["handling"]["办理号"] == first_result["handling"]["办理号"]
        assert len(store.rows("recheck")) == 1

    def test_concurrent_submissions_register_once(self):
        first = recheck.locate_due_rechecks(7)["first"]
        results: list[dict] = []
        barrier = threading.Barrier(8)

        def submit() -> None:
            barrier.wait()
            results.append(
                recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
            )

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(result["ok"] for result in results)
        numbers = {result["handling"]["办理号"] for result in results}
        assert len(numbers) == 1
        assert len(store.rows("recheck")) == 1
        assert len(store.rows("inspection")) == 5  # 没有多出一条冷却复检


class TestThreeStates:
    def test_requested_cannot_verify(self):
        first = recheck.locate_due_rechecks(7)["first"]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        outcome = recheck.verify_maintenance_with_recheck(first["保养编号"])
        assert outcome["ok"] is False
        assert outcome["status"] == recheck.STATUS_REQUESTED
        assert "尚未受理" in outcome["message"]
        assert arranged["status"] == recheck.STATUS_REQUESTED

    def test_accepted_uses_evidence_from_cooling_inspection(self):
        first = recheck.locate_due_rechecks(7)["first"]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        accepted = recheck.accept_recheck(arranged["handling"]["id"])
        assert accepted["ok"] is True
        assert accepted["status"] == recheck.STATUS_ACCEPTED
        assert accepted["受理编号"].startswith("SL-")
        assert accepted["evidence"]["是否冷却"] is True

        outcome = recheck.verify_maintenance_with_recheck(first["保养编号"])
        assert outcome["ok"] is True
        assert outcome["evidence"]["来源检验编号"] == first["检验编号"]
        assert outcome["evidence"]["受理编号"] == accepted["受理编号"]

    def test_accept_twice_keeps_same_acceptance_no(self):
        first = recheck.locate_due_rechecks(7)["first"]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        first_accept = recheck.accept_recheck(arranged["handling"]["id"])
        second_accept = recheck.accept_recheck(arranged["handling"]["id"])
        assert second_accept["duplicate"] is True
        assert second_accept["受理编号"] == first_accept["受理编号"]


class TestCycleCorrection:
    def test_moved_out_request_becomes_waiting_and_cannot_verify(self):
        listing = recheck.locate_due_rechecks(7)
        item = listing["items"][1]
        arranged = recheck.arrange_recheck(
            inspection_id=item["检验编号"], days=7, snapshot=listing["snapshot"]
        )
        assert arranged["ok"] is True

        # 值班员补报新的保养日期：把先前排在前面的检验挪出窗口。
        _shift(item["保养编号"], 40)
        stale = recheck.arrange_recheck(
            inspection_id=item["检验编号"],
            days=7,
            snapshot=listing["snapshot"],
        )
        assert stale["ok"] is False
        assert stale["status"] == recheck.STATUS_WAITING

        accepted = recheck.accept_recheck(arranged["handling"]["id"], days=7)
        assert accepted["ok"] is False
        assert accepted["status"] == recheck.STATUS_WAITING

        outcome = recheck.verify_maintenance_with_recheck(item["保养编号"], days=7)
        assert outcome["ok"] is False
        assert "等待周期更正" in outcome["message"]

        listing_after = recheck.locate_due_rechecks(7)
        assert item["检验编号"] not in {
            row["检验编号"] for row in listing_after["items"]
        }
        moved = {row["检验编号"]: row for row in listing_after["moved_out"]}
        assert moved[item["检验编号"]]["status"] == recheck.STATUS_WAITING

    def test_corrected_cycle_allows_retry_from_maintenance_page(self):
        listing = recheck.locate_due_rechecks(7)
        item = listing["items"][1]
        first = recheck.arrange_recheck(inspection_id=item["检验编号"], days=7)

        _shift(item["保养编号"], 60)
        waiting = recheck.retry_arrange(maintenance_id=item["保养编号"], days=7)
        assert waiting["status"] == recheck.STATUS_WAITING

        # 保养页更正周期，回到窗口内后再重试安排。
        corrected = recheck.update_maintenance_cycle(
            item["保养编号"], next_date=(date.today() + timedelta(days=2)).isoformat(), days=7
        )
        assert corrected["ok"] is True
        retried = recheck.retry_arrange(maintenance_id=item["保养编号"], days=7)
        assert retried["ok"] is True
        assert retried["handling"]["办理号"] == first["handling"]["办理号"]
        assert len(store.rows("recheck")) == 1

    def test_after_correction_try_verify_before_rearranging(self):
        listing = recheck.locate_due_rechecks(7)
        item = listing["items"][1]
        arranged = recheck.arrange_recheck(inspection_id=item["检验编号"], days=7)
        recheck.update_maintenance_cycle(
            item["保养编号"], next_date=(date.today() + timedelta(days=30)).isoformat(), days=7
        )

        maintenance_svc = MaintenanceService()
        outcome = maintenance_svc.handle_action(item["保养编号"], "核验保养", _payload(days=7))
        assert outcome["ok"] is False
        assert outcome["status"] == recheck.STATUS_WAITING

        inspection_svc = InspectionService()
        retry = inspection_svc.handle_action(item["检验编号"], "重试安排", _payload(days=7))
        assert retry["ok"] is False

        recheck.update_maintenance_cycle(
            item["保养编号"], next_date=(date.today() + timedelta(days=3)).isoformat(), days=7
        )
        retry = inspection_svc.handle_action(item["检验编号"], "重试安排", _payload(days=7))
        assert retry["ok"] is True
        assert retry["handling"]["id"] == arranged["handling"]["id"]


class TestRecoveryAndSnapshots:
    def test_recover_handling_no_from_work_order(self):
        first = recheck.locate_due_rechecks(7)["first"]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        accepted = recheck.accept_recheck(arranged["handling"]["id"])

        recovered = recheck.recover_for_maintenance(first["保养编号"])
        assert recovered["ok"] is True
        assert recovered["办理号"] == arranged["handling"]["办理号"]
        assert recovered["受理编号"] == accepted["受理编号"]
        assert recovered["原检验"]["检验类型"] == "冷却系统检验"
        assert recovered["原保养周期"] == "180天"

    def test_accepted_handling_survives_cycle_change_and_keeps_original(self):
        first = recheck.locate_due_rechecks(7)["first"]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        accepted = recheck.accept_recheck(arranged["handling"]["id"])

        _shift(first["保养编号"], 90)
        listing = recheck.locate_due_rechecks(7)
        accepted_rows = {row["检验编号"]: row for row in listing["accepted"]}
        row = accepted_rows[first["检验编号"]]
        assert row["受理编号"] == accepted["受理编号"]
        assert row["原保养周期"] == "180天"
        assert row["原下次保养"] == first["复检期限"]

        again = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        assert again["ok"] is True
        assert again["status"] == recheck.STATUS_ACCEPTED
        assert again["duplicate"] is True
        assert len(store.rows("recheck")) == 1


class _payload:
    """模拟 ActionPayload，只暴露复检动作会读到的字段。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.values = {}


class TestMaintenanceVerificationEntry:
    def test_verify_before_any_arrangement_is_rejected(self):
        outcome = MaintenanceService().handle_action(1, "核验保养", _payload(days=7))
        assert outcome["ok"] is False
        assert "复检办理清单" in outcome["message"]

    def test_plain_maintenance_without_inspection_still_verifies(self):
        entry, _ = MaintenanceService().create_entry({
            "船舶编号": "VSL-9999",
            "保养项目": "普通清洁保养",
            "保养周期": "30天",
        })
        assert entry is not None
        outcome = MaintenanceService().handle_action(entry["id"], "核验保养", _payload(days=7))
        assert outcome["ok"] is True
        assert outcome["entry"]["status"] == "已核验"

    def test_verify_after_cycle_correction_then_retry_arrange(self):
        first = recheck.locate_due_rechecks(7)["first"]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        recheck.accept_recheck(arranged["handling"]["id"])
        recheck.update_maintenance_cycle(
            first["保养编号"], cycle="90天", days=7
        )

        outcome = MaintenanceService().handle_action(first["保养编号"], "核验保养", _payload(days=7))
        assert outcome["ok"] is True
        assert outcome["evidence"]["是否冷却"] is True
        assert outcome["evidence"]["受理编号"] == arranged["handling"]["受理编号"]


class TestReportedAfterOpen:
    def test_first_cooling_sent_then_moved_out_of_window(self):
        """筛选未来7日→送出首条冷却复检→值班员补报日期后移出窗口。"""
        listing = recheck.locate_due_rechecks(7)
        first = listing["first"]
        assert first["是否冷却"] is True

        arranged = recheck.arrange_recheck(
            inspection_id=first["检验编号"], days=7, snapshot=listing["snapshot"])
        assert arranged["ok"] is True
        assert arranged["status"] == recheck.STATUS_REQUESTED
        assert arranged["receipt"]["回执号"]

        # 值班员补报新的保养日期，首条检验被排到窗口外。
        _shift(first["保养编号"], 45)

        items, total = recheck.list_handlings(days=7)
        assert total == 1
        assert items[0]["status"] == recheck.STATUS_WAITING

        blocked = recheck.accept_recheck(arranged["handling"]["id"], days=7)
        assert blocked["ok"] is False
        assert blocked["status"] == recheck.STATUS_WAITING

        # 旧清单快照再提交也不放行。
        stale = recheck.arrange_recheck(
            inspection_id=first["检验编号"], days=7, snapshot=listing["snapshot"])
        assert stale["ok"] is False
        assert stale["status"] == recheck.STATUS_WAITING

    def test_three_distinct_results_for_same_item(self):
        listing = recheck.locate_due_rechecks(7)
        first = listing["items"][0]
        arranged = recheck.arrange_recheck(inspection_id=first["检验编号"], days=7)
        assert arranged["status"] == recheck.STATUS_REQUESTED

        accepted = recheck.accept_recheck(arranged["handling"]["id"], days=7)
        assert accepted["status"] == recheck.STATUS_ACCEPTED

        _shift(first["保养编号"], 45)
        moved = recheck.retry_arrange(inspection_id=first["检验编号"], days=7)
        # 已受理的安排保留“已受理”，不会降级成等待周期更正。
        assert moved["status"] == recheck.STATUS_ACCEPTED

        # 另一笔未受理的安排移出窗口后就是“等待周期更正”。
        other = listing["items"][1]
        pending = recheck.arrange_recheck(inspection_id=other["检验编号"], days=7)
        assert pending["status"] == recheck.STATUS_REQUESTED
        _shift(other["保养编号"], 45)
        waiting = recheck.retry_arrange(inspection_id=other["检验编号"], days=7)
        assert waiting["status"] == recheck.STATUS_WAITING

        statuses = {arranged["status"], accepted["status"], waiting["status"]}
        assert statuses == {
            recheck.STATUS_REQUESTED,
            recheck.STATUS_ACCEPTED,
            recheck.STATUS_WAITING,
        }

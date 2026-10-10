"""麦麦时光机分析层单测。

零依赖（只用标准库 unittest），可直接：
    python -m unittest discover tests
或在项目根目录：
    python tests/test_analyze.py
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcd_time_machine.analyze import (  # noqa: E402
    build_timeline,
    build_taste_profile,
    rank_moments,
    replay_order,
)
from mcd_time_machine.normalize import (  # noqa: E402
    normalize_order,
    normalize_orders,
    parse_datetime,
)


def _order(order_id, when, items, total, channel="self_pickup", store="望京SOHO店", **kw):
    return normalize_order(
        {
            "orderId": order_id,
            "createTime": when,
            "totalAmount": total,
            "diningType": channel,
            "storeName": store,
            "items": items,
            **kw,
        }
    )


# 构造一批覆盖各种边界的订单
SAMPLE = [
    _order("A1", "2026-10-08 12:30", [
        {"name": "巨无霸套餐", "quantity": 1, "price": 4200, "isSet": True},
        {"name": "麦乐鸡", "quantity": 1, "price": 2000},
    ], 6200),
    _order("A2", "2026-09-20 22:10", [
        {"name": "板烧鸡腿堡", "quantity": 1, "price": 2200},
        {"name": "中可乐", "quantity": 1, "price": 800},
    ], 3000),
    _order("A3", "2026-09-19 23:40", [
        {"name": "麦辣鸡腿汉堡", "quantity": 2, "price": 2000},
        {"name": "薯条（中）", "quantity": 1, "price": 1200},
    ], 5200),
    _order("A4", "2025-11-11 08:15", [
        {"name": "培根蔬萃双层牛堡", "quantity": 1, "price": 2800},
        {"name": "热美式", "quantity": 1, "price": 1500},
    ], 4300),
    _order("A5", "2025-03-02 19:00", [
        {"name": "巨无霸套餐", "quantity": 2, "price": 4200, "isSet": True},
    ], 8400, channel="delivery"),
]


class TestNormalize(unittest.TestCase):
    def test_parse_datetime_formats(self):
        self.assertEqual(parse_datetime("2026-10-08 12:30:00"), dt.datetime(2026, 10, 8, 12, 30))
        self.assertEqual(parse_datetime("2026-10-08"), dt.datetime(2026, 10, 8))
        self.assertEqual(parse_datetime("2026/10/08 12:30"), dt.datetime(2026, 10, 8, 12, 30))
        # 毫秒时间戳
        self.assertEqual(parse_datetime(1791500000000).year, 2026)
        self.assertEqual(parse_datetime("2026-10-08T12:30:00"), dt.datetime(2026, 10, 8, 12, 30))

    def test_parse_datetime_failure_is_none(self):
        self.assertIsNone(parse_datetime("不是时间"))
        self.assertIsNone(parse_datetime(None))
        self.assertIsNone(parse_datetime(""))

    def test_amount_cents_to_yuan(self):
        order = normalize_order({"orderId": "X", "totalAmount": 4200, "createTime": "2026-01-01 12:00"})
        self.assertAlmostEqual(order["total_amount"], 42.0)

    def test_channel_normalization(self):
        for raw, expected in [
            ("delivery", "delivery"),
            ("麦乐送", "delivery"),
            ("selfPickup", "self_pickup"),
            ("到店自取", "self_pickup"),
            ("堂食", "dine_in"),
        ]:
            order = normalize_order({
                "orderId": "C", "diningType": raw,
                "createTime": "2026-01-01 12:00", "totalAmount": 1000,
            })
            self.assertEqual(order["channel"], expected, f"channel {raw} 归一化失败")

    def test_is_set_heuristic_by_name(self):
        order = normalize_order({
            "orderId": "S", "createTime": "2026-01-01 12:00", "totalAmount": 1000,
            "items": [{"name": "超值双人套餐", "quantity": 1, "price": 1000}],
        })
        self.assertTrue(order["items"][0]["is_set"])

    def test_non_combo_not_flagged(self):
        order = normalize_order({
            "orderId": "S2", "createTime": "2026-01-01 12:00", "totalAmount": 1000,
            "items": [{"name": "巨无霸汉堡", "quantity": 1, "price": 1000}],
        })
        self.assertFalse(order["items"][0]["is_set"])

    def test_missing_items_does_not_crash(self):
        order = normalize_order({"orderId": "M", "createTime": "2026-01-01 12:00"})
        self.assertEqual(order["items"], [])
        self.assertIsNone(order["total_amount"])

    def test_garbage_row_rejected(self):
        self.assertIsNone(normalize_order({"foo": "bar"}))

    def test_normalize_orders_wrapped_payload(self):
        """模拟 MCP 的多种外层包裹形态。"""
        raw_item = {"orderId": "W1", "createTime": "2026-01-01 12:00", "totalAmount": 1000}
        for payload in (
            [raw_item],
            {"orders": [raw_item]},
            {"list": [raw_item]},
            {"data": {"list": [raw_item]}},
            {"result": {"records": [raw_item]}},
        ):
            result = normalize_orders(payload)
            self.assertEqual(len(result), 1, f"包裹形态 {list(payload) if isinstance(payload, dict) else 'list'} 解析失败")

    def test_normalize_orders_empty(self):
        self.assertEqual(normalize_orders(None), [])
        self.assertEqual(normalize_orders({}), [])


class TestTimeline(unittest.TestCase):
    def setUp(self):
        self.tl = build_timeline(SAMPLE)

    def test_counts_and_range(self):
        self.assertEqual(self.tl["total_orders"], 5)
        self.assertEqual(self.tl["first_order"], "2025-03-02 19:00")
        self.assertEqual(self.tl["last_order"], "2026-10-08 12:30")
        self.assertGreater(self.tl["span_days"], 500)

    def test_by_hour_has_24_slots(self):
        self.assertEqual(len(self.tl["by_hour"]), 24)
        self.assertEqual(sum(h["orders"] for h in self.tl["by_hour"]), 5)

    def test_moods_detection(self):
        # 22:10/ 23:40 两单深夜；08:15 一单早餐
        self.assertEqual(self.tl["moods"]["late_night"], 2)
        self.assertEqual(self.tl["moods"]["breakfast"], 1)

    def test_by_month_and_channel(self):
        months = [m["month"] for m in self.tl["by_month"]]
        self.assertEqual(months, sorted(months))
        self.assertIn("2026-10", months)
        channels = {c["channel"] for c in self.tl["by_channel"]}
        self.assertIn("self_pickup", channels)
        self.assertIn("delivery", channels)

    def test_longest_gap(self):
        # 2025-11-11 -> 2026-09-19 = 312 天，是本批数据中最大的空窗
        self.assertEqual(self.tl["longest_gap_days"], 312)

    def test_empty_timeline(self):
        tl = build_timeline([])
        self.assertEqual(tl["total_orders"], 0)
        self.assertIsNone(tl["first_order"])
        self.assertEqual(len(tl["by_hour"]), 24)


class TestTasteProfile(unittest.TestCase):
    def setUp(self):
        self.p = build_taste_profile(SAMPLE)

    def test_headline_mentions_top_item(self):
        self.assertIn("巨无霸套餐", self.p["headline"])
        self.assertEqual(self.p["orders"], 5)

    def test_top_item_counting_respects_quantity(self):
        # 麦辣鸡腿汉堡 出现 2 次（A3 数量 2）
        names = [i["name"] for i in self.p["top_items"]]
        self.assertIn("麦辣鸡腿汉堡", names)
        entry = next(i for i in self.p["top_items"] if i["name"] == "麦辣鸡腿汉堡")
        self.assertEqual(entry["times"], 2)

    def test_averages(self):
        self.assertAlmostEqual(self.p["total_spend"], 62.0 + 30.0 + 52.0 + 43.0 + 84.0, places=2)
        self.assertAlmostEqual(self.p["avg_spend"], (62.0 + 30.0 + 52.0 + 43.0 + 84.0) / 5, places=2)

    def test_late_night_trait_present(self):
        tags = [t["tag"] for t in self.p["traits"]]
        self.assertIn("深夜食客", tags)

    def test_late_night_detail_not_contradictory(self):
        """回归：曾出现「深夜食客 · 最晚的一单在 08:30」。

        根因是取全订单最大时间而非深夜单最大时间。样例中最新一单恰是
        08:30 的早餐单，因此本断言能精准锁住该缺陷。
        """
        detail = next(t["detail"] for t in self.p["traits"] if t["tag"] == "深夜食客")
        self.assertIn("23:40", detail)          # 深夜单里最晚的应是 23:40
        self.assertNotIn("08:30", detail)        # 早餐单的时间不该出现在这里

    def test_roles_counted(self):
        roles = {r["role"]: r["count"] for r in self.p["roles"]}
        self.assertGreaterEqual(roles.get("main", 0), 5)
        self.assertGreaterEqual(roles.get("drink", 0), 2)

    def test_empty_profile_graceful(self):
        p = build_taste_profile([])
        self.assertEqual(p["orders"], 0)
        self.assertEqual(p["traits"], [])


class TestReplay(unittest.TestCase):
    def test_replay_by_id(self):
        r = replay_order(SAMPLE, order_id="A1")
        self.assertTrue(r["found"])
        self.assertEqual(r["order"]["order_id"], "A1")
        self.assertEqual(len(r["recipe"]), 2)

    def test_replay_by_date(self):
        r = replay_order(SAMPLE, when="2026-09-20")
        self.assertTrue(r["found"])
        self.assertEqual(r["order"]["order_id"], "A2")

    def test_replay_by_month(self):
        r = replay_order(SAMPLE, when="2025-03")
        self.assertTrue(r["found"])
        self.assertEqual(r["order"]["order_id"], "A5")

    def test_replay_default_is_latest(self):
        r = replay_order(SAMPLE)
        self.assertTrue(r["found"])
        self.assertEqual(r["order"]["order_id"], "A1")

    def test_replay_delivery_hint(self):
        r = replay_order(SAMPLE, order_id="A5")
        self.assertTrue(any("配送地址" in h for h in r["hints"]))

    def test_replay_set_meal_hint(self):
        """含套餐的订单，复刻方案必须给出套餐内可替换项的提示。"""
        r = replay_order(SAMPLE, order_id="A1")
        self.assertTrue(any("套餐" in h for h in r["hints"]))
        # 套餐行必须标注需要再查详情
        set_row = next(i for i in r["recipe"] if i["is_set"])
        self.assertIn("query-meal-detail", set_row["note"])

    def test_replay_not_found_lists_months(self):
        r = replay_order(SAMPLE, when="2020-01-01")
        self.assertFalse(r["found"])
        self.assertIn("available_months", r)

    def test_replay_bad_id(self):
        r = replay_order(SAMPLE, order_id="NOPE")
        self.assertFalse(r["found"])

    def test_replay_never_auto_orders(self):
        """安全红线：复刻输出必须带免责声明。"""
        r = replay_order(SAMPLE)
        self.assertIn("disclaimer", r)


class TestRealSchemaRegression(unittest.TestCase):
    """回归测试：锁定 2026-10-10 实测的真实 MCP 字段名。

    这批测试的价值在于——若将来有人「凭直觉」把别名改回 items/totalAmount/
    goodsList 等猜测值，本组会立刻失败。
    """

    # 真实 order-list 出参（2026-10-10 从 MCP schema 描述中取得）
    REAL_ORDER = {
        "orderId": "M20261008999",
        "orderType": "堂食",
        "createTime": "2026-10-08 19:12",
        "beType": "餐厅",
        "beCode": "B001",
        "storeCode": "S001",
        "storeName": "望京SOHO店",
        "orderStatus": "完成",
        "orderProductList": [
            {
                "productCode": "P001",
                "productName": "巨无霸套餐",
                "quantity": 1,
                "comboItemList": [
                    {"productCode": "S1", "productName": "中薯", "quantity": 1},
                    {"productCode": "S2", "productName": "中可乐", "quantity": 1},
                ],
            },
            {"productCode": "P002", "productName": "麦乐鸡", "quantity": 2, "comboItemList": []},
        ],
        "realTotalAmount": "84.00",
    }

    def test_real_schema_parses_all_fields(self):
        out = normalize_orders({"data": {"list": [self.REAL_ORDER]}})
        self.assertEqual(len(out), 1)
        o = out[0]
        self.assertEqual(o["order_id"], "M20261008999")
        self.assertEqual(o["total_amount"], 84.0)          # 字符串 "84.00" → 元
        self.assertEqual(o["channel"], "dine_in")          # orderType="堂食"
        self.assertEqual(o["store_name"], "望京SOHO店")
        self.assertEqual(o["store_code"], "S001")
        self.assertEqual(o["order_status"], "完成")
        self.assertEqual(o["created_at"].strftime("%Y-%m-%d %H:%M"), "2026-10-08 19:12")
        self.assertEqual(o["item_count"], 3)               # 1 + 2

    def test_real_schema_items_and_combo(self):
        o = normalize_orders({"data": {"list": [self.REAL_ORDER]}})[0]
        names = [i["name"] for i in o["items"]]
        self.assertEqual(names, ["巨无霸套餐", "麦乐鸡"])
        combo = o["items"][0]
        self.assertTrue(combo["is_set"])
        self.assertEqual([s["name"] for s in combo["sub_items"]], ["中薯", "中可乐"])
        self.assertFalse(o["items"][1]["is_set"])

    def test_real_schema_outer_wrapper(self):
        """真实外层是 data.list；也须兼容 MCP 文档所示的其他包裹。"""
        for payload in (
            {"data": {"list": [self.REAL_ORDER]}},
            {"list": [self.REAL_ORDER]},
            [self.REAL_ORDER],
        ):
            self.assertEqual(len(normalize_orders(payload)), 1)

    def test_real_schema_empty_data_returns_empty(self):
        """实测新账号返回 {"data":{}} —— 不得抛异常，返回空列表。"""
        self.assertEqual(normalize_orders({"data": {}}), [])

    def test_real_schema_analysis_pipeline(self):
        """真实字段结构能跑通时间轴与画像全链路。"""
        orders = normalize_orders({"data": {"list": [self.REAL_ORDER]}})
        tl = build_timeline(orders)
        self.assertEqual(tl["total_orders"], 1)
        self.assertEqual(tl["by_month"][0]["month"], "2026-10")
        profile = build_taste_profile(orders)
        self.assertEqual(profile["orders"], 1)
        self.assertAlmostEqual(profile["total_spend"], 84.0, places=2)
        r = replay_order(orders)
        self.assertTrue(r["found"])
        self.assertEqual(len(r["recipe"]), 2)


class TestMoments(unittest.TestCase):
    def test_late_night_ranked_first(self):
        moments = rank_moments(SAMPLE)
        self.assertTrue(moments)
        self.assertTrue(moments[0]["story"])

    def test_limit_respected(self):
        self.assertEqual(len(rank_moments(SAMPLE, limit=2)), 2)

    def test_moments_ranked_by_clock_not_date(self):
        """回归：深夜单应按「一天里几点」排序，不受日期影响。

        A2=9-20 22:10、A3=9-19 23:40。若按完整 datetime 排，
        A2 会因日期更大而错误地压过 A3。
        """
        moments = rank_moments(SAMPLE, limit=2)
        self.assertEqual(moments[0]["when"], "2026-09-19 23:40")
        self.assertEqual(moments[1]["when"], "2026-09-20 22:10")

    def test_empty_safe(self):
        self.assertEqual(rank_moments([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
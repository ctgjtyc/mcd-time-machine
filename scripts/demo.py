#!/usr/bin/env python3
"""麦麦时光机 · 离线演示器。

用内置 fixture 跑完整分析链路，无需 MCP Token。
用途：让任何人在clone 仓库后 5 秒内看到效果，也用于 CI 冒烟。

用法：
    python scripts/demo.py
    python scripts/demo.py --json          # 输出原始 JSON
    python scripts/demo.py --order 2025-03-02   # 演示复刻某一单
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from mcd_time_machine import (  # noqa: E402
    build_timeline,
    build_taste_profile,
    rank_moments,
    replay_order,
)

# ---------------------------------------------------------------------------
# 内置样例数据（模拟 order-list 出参，已过 normalize 层）
# 刻意覆盖：深夜单 / 早餐单 / 长期空窗 / 多次复购同款 / 套餐与单点混合
# ---------------------------------------------------------------------------
RAW_ORDERS = [
    {
        "orderId": "M20260302A1", "createTime": "2026-03-02 19:12",
        "totalAmount": 8400, "diningType": "selfPickup", "storeName": "望京SOHO店",
        "items": [
            {"name": "巨无霸套餐", "quantity": 2, "price": 4200, "isSet": True},
            {"name": "麦乐鸡5块", "quantity": 1, "price": 2500},
        ],
    },
    {
        "orderId": "M20260418B2", "createTime": "2026-04-18 22:47",
        "totalAmount": 5600, "diningType": "delivery", "storeName": "三里屯太古里店",
        "items": [
            {"name": "板烧鸡腿堡", "quantity": 2, "price": 2200},
            {"name": "中可乐", "quantity": 2, "price": 800},
            {"name": "鸡翅(3块)", "quantity": 1, "price": 1500},
        ],
    },
    {
        "orderId": "M2026060503", "createTime": "2026-06-05 12:20",
        "totalAmount": 4400, "diningType": "selfPickup", "storeName": "望京SOHO店",
        "items": [
            {"name": "培根蔬萃双层牛堡", "quantity": 1, "price": 2800},
            {"name": "热美式", "quantity": 1, "price": 1500},
        ],
    },
    {
        "orderId": "M2026092004", "createTime": "2026-09-20 23:05",
        "totalAmount": 7400, "diningType": "delivery", "storeName": "望京SOHO店",
        "items": [
            {"name": "巨无霸套餐", "quantity": 1, "price": 4200, "isSet": True},
            {"name": "麦旋风", "quantity": 1, "price": 1900},
            {"name": "中可乐", "quantity": 1, "price": 800},
        ],
    },
    {
        "orderId": "M2026100805", "createTime": "2026-10-08 08:30",
        "totalAmount": 3200, "diningType": "selfPickup", "storeName": "望京SOHO店",
        "items": [
            {"name": "吉士蛋堡", "quantity": 1, "price": 1800},
            {"name": "中热美式", "quantity": 1, "price": 1400},
        ],
    },
]


def _bar(value: float, maximum: float, width: int = 24) -> str:
    if maximum <= 0:
        return ""
    filled = int(round(value / maximum * width))
    return "█" * max(filled, 1 if value > 0 else 0)


def demo_timeline(orders) -> dict:
    tl = build_timeline(orders)
    print("=" * 58)
    print("📅  订单时间轴")
    print("=" * 58)
    print(f"共{tl['total_orders']} 单 · {tl['first_order']} → {tl['last_order']}"
          f" · 跨度 {tl['span_days']} 天")
    print()
    print("按月：")
    max_orders = max((m["orders"] for m in tl["by_month"]), default=1)
    for m in tl["by_month"]:
        print(f"  {m['month']}  {_bar(m['orders'], max_orders, 18):<18} "
              f"{m['orders']} 单  ¥{m['spend']:.2f}")
    print()
    print("按就餐方式：")
    for c in tl["by_channel"]:
        print(f"  {c['channel']:<14} {c['orders']} 单  ¥{c['spend']:.2f}")
    print()
    print("按门店 TOP5：")
    for s in tl["by_store"]:
        print(f"  {s['store']:<16} {s['orders']} 单  ¥{s['spend']:.2f}")
    print()
    mood = tl["moods"]
    print(f"时段画像：深夜单{mood['late_night']} · 早餐单 {mood['breakfast']} "
          f"· 最长空窗 {tl['longest_gap_days']} 天")
    if tl["busiest_day"]:
        print(f"最忙的一天：{tl['busiest_day']['date']}（{tl['busiest_day']['orders']} 单）")
    return tl


def demo_profile(orders) -> dict:
    profile = build_taste_profile(orders)
    print()
    print("=" * 58)
    print("🍟  口味画像")
    print("=" * 58)
    print(f"  {profile['headline']}")
    print()
    for trait in profile["traits"]:
        print(f"  【{trait['tag']}】{trait['detail']}")
    print()
    print("常点 TOP8：")
    max_times = max((i["times"] for i in profile["top_items"]), default=1)
    for item in profile["top_items"]:
        print(f"  {item['name']:<18} {_bar(item['times'], max_times, 14):<14} ×{item['times']}")
    print()
    print(f"结构：均价 ¥{profile['avg_spend']:.2f} · 中位 ¥{profile['median_spend']:.2f} "
          f"· 累计 ¥{profile['total_spend']:.2f} · 复购过 {profile['distinct_items']} 种餐品")
    roles = " · ".join(f"{r['role']} {r['count']}" for r in profile["roles"])
    print(f"品类分布：{roles}")
    return profile


def demo_moments(orders) -> list:
    moments = rank_moments(orders, limit=3)
    print()
    print("=" * 58)
    print("⏱  有故事的订单")
    print("=" * 58)
    for moment in moments:
        print(f"  {moment['story']}")
    return moments


def demo_replay(orders, when: str | None) -> dict:
    result = replay_order(orders, when=when)
    print()
    print("=" * 58)
    print("🔁  复刻方案")
    print("=" * 58)
    if not result["found"]:
        print(f"  未找到：{result['reason']}")
        if result.get("available_months"):
            print(f"  可选月份：{', '.join(result['available_months'])}")
        return result

    order = result["order"]
    print(f"  目标订单：{order['when']}  {order['store']}  "
          f"（{order['channel']}）  实付 ¥{order['total_amount']}")
    print()
    print("  复刻清单：")
    for row in result["recipe"]:
        mark = "🍱" if row["is_set"] else "  "
        print(f"    {mark} {row['name']} × {row['quantity']}")
        if row["note"]:
            print(f"       ↳ {row['note']}")
    if result["hints"]:
        print()
        print("  提示：")
        for hint in result["hints"]:
            print(f"    · {hint}")
    print()
    print(f"  ⚠️  {result['disclaimer']}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="麦麦时光机离线演示")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    parser.add_argument("--order", metavar="DATE", help="复刻指定日期，如 2026-03-02")
    args = parser.parse_args()

    from mcd_time_machine import normalize_orders

    orders = normalize_orders(RAW_ORDERS)
    if not orders:
        print("样例数据归一化失败，请检查 RAW_ORDERS 结构")
        return 1

    if args.json:
        print(json.dumps(
            {
                "timeline": build_timeline(orders),
                "profile": build_taste_profile(orders),
                "moments": rank_moments(orders, limit=3),
                "replay": replay_order(orders, when=args.order),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ))
        return 0

    print()
    print("🍟  麦麦时光机 · 离线演示（样例数据，非真实用户订单）")
    print("─" * 58)

    demo_timeline(orders)
    demo_profile(orders)
    demo_moments(orders)
    demo_replay(orders, args.order)

    print()
    print("─" * 58)
    print("提示：接入真实麦当劳 MCP 后，以上数据将来自你的 order-list 实流水。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
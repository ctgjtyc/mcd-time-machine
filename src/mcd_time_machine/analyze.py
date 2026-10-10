"""麦麦时光机 · 分析层。

三个核心能力，对应三种用户意图：

1. :func:`build_timeline`   —— 「我这些年都吃了什么？」按月/渠道/门店切时间轴
2. :func:`build_taste_profile` —— 「我是怎样的麦麦用户？」口味画像与偏好聚类
3. :func:`replay_order`     —— 「把去年那顿再来一次」复刻方案（含可替换项推断）

设计原则：全部纯函数，输入归一化后的订单列表，输出可序列化的dict。
不依赖 MCP，方便单测与离线演示。
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "build_timeline",
    "build_taste_profile",
    "replay_order",
    "rank_moments",
]

# 深夜时段定义（用于「深夜麦麦」画像）
_LATE_NIGHT_START = 21
_LATE_NIGHT_END = 5

# 早餐时段（麦当劳早餐供应时间）
_BREAKFAST_START = 5
_BREAKFAST_END = 10


def _orders_with_time(orders: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """只保留有有效时间戳的订单——时间轴类分析的前提。"""
    return [o for o in orders if isinstance(o, Mapping) and o.get("created_at")]


def build_timeline(orders: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """构建订单时间轴。

    返回结构::

        {
          "total_orders": int,          # 有时间戳的订单总数
          "first_order": "2024-03-02 19:40" | None,
          "last_order":  "2026-10-08 12:05" | None,
          "span_days": int,
          "by_month": [{"month": "2026-10", "orders": 12, "spend": 268.0}, ...],
          "by_channel": [{"channel": "self_pickup", "orders": 30, "spend": 640.0}, ...],
          "by_store":   [{"store": "望京SOHO店", "orders": 18, "spend": 402.0}, ...],
          "by_hour":    [{"hour": 12, "orders": 9}, ...],   # 24 槽，未出现为 0
          "moods": {"late_night": 5, "breakfast": 2, "counted": 40},
          "busiest_day": {"date": "2025-11-11", "orders": 3},
          "longest_gap_days": 63,
        }
    """
    timed = _orders_with_time(orders)

    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"orders": 0, "spend": 0.0})
    by_channel: dict[str, dict[str, float]] = defaultdict(lambda: {"orders": 0, "spend": 0.0})
    by_store: dict[str, dict[str, float]] = defaultdict(lambda: {"orders": 0, "spend": 0.0})
    by_hour: list[int] = [0] * 24
    by_date: Counter = Counter()

    late_night = 0
    breakfast = 0
    earliest: _dt.datetime | None = None
    latest: _dt.datetime | None = None
    timed_sorted: list[_dt.datetime] = []

    for order in timed:
        when: _dt.datetime = order["created_at"]
        spend = float(order.get("total_amount") or 0.0)

        month_key = when.strftime("%Y-%m")
        by_month[month_key]["orders"] += 1
        by_month[month_key]["spend"] += spend

        channel = order.get("channel") or "unknown"
        by_channel[channel]["orders"] += 1
        by_channel[channel]["spend"] += spend

        store = order.get("store_name") or "未知门店"
        by_store[store]["orders"] += 1
        by_store[store]["spend"] += spend

        by_hour[when.hour] += 1
        by_date[when.date().isoformat()] += 1

        if when.hour >= _LATE_NIGHT_START or when.hour < _LATE_NIGHT_END:
            late_night += 1
        elif _BREAKFAST_START <= when.hour < _BREAKFAST_END:
            breakfast += 1

        earliest = when if earliest is None or when < earliest else earliest
        latest = when if latest is None or when > latest else latest
        timed_sorted.append(when)

    # 最长间隔：把时间排好序，看相邻两单之间隔了多久
    longest_gap = 0
    for previous, current in zip(sorted(timed_sorted), sorted(timed_sorted)[1:]):
        gap = (current - previous).days
        longest_gap = max(longest_gap, gap)

    busiest_day = None
    if by_date:
        top_date, top_count = by_date.most_common(1)[0]
        busiest_day = {"date": top_date, "orders": top_count}

    return {
        "total_orders": len(timed),
        "first_order": earliest.strftime("%Y-%m-%d %H:%M") if earliest else None,
        "last_order": latest.strftime("%Y-%m-%d %H:%M") if latest else None,
        "span_days": (latest - earliest).days if earliest and latest else 0,
        "by_month": [
            {"month": m, "orders": int(v["orders"]), "spend": round(v["spend"], 2)}
            for m, v in sorted(by_month.items())
        ],
        "by_channel": [
            {"channel": c, "orders": int(v["orders"]), "spend": round(v["spend"], 2)}
            for c, v in sorted(by_channel.items(), key=lambda kv: -kv[1]["orders"])
        ],
        "by_store": [
            {"store": s, "orders": int(v["orders"]), "spend": round(v["spend"], 2)}
            for s, v in sorted(by_store.items(), key=lambda kv: -kv[1]["orders"])[:5]
        ],
        "by_hour": [{"hour": h, "orders": by_hour[h]} for h in range(24)],
        "moods": {
            "late_night": late_night,
            "breakfast": breakfast,
            "counted": len(timed),
        },
        "busiest_day": busiest_day,
        "longest_gap_days": longest_gap,
    }


def _classify_role(item: Mapping[str, Any]) -> str:
    """给商品打上语义角色：主食 / 副食 / 饮品 / 甜点 / 酱料。

    纯启发式，基于中文名关键词。目的是让口味画像能说人话
    （"你是酱料狂热者"），而不是甩一堆商品 ID。
    """
    name = str(item.get("name") or "")
    cat = str(item.get("category") or "")
    blob = name + cat

    if any(
        k in blob
        for k in (
            "饮",
            "茶",
            "咖啡",
            "美式",
            "拿铁",
            "可乐",
            "雪碧",
            "橙汁",
            "奶昔",
            "drink",
            "coffee",
        )
    ):
        return "drink"
    if any(k in blob for k in ("甜筒", "派", "布丁", "圣代", "麦旋风", "冰淇淋", "dessert")):
        return "dessert"
    if any(
        k in blob
        for k in ("薯条", "薯饼", "鸡块", "鸡翅", "麦辣鸡腿", "上校", "鸡排", "小食", "side")
    ):
        return "side"
    if any(k in blob for k in ("酱", "番茄酱", "芥末", "ketchup")):
        return "sauce"
    if any(k in blob for k in ("堡", "三明治", "卷", "套餐", "饭", "面")):
        return "main"
    return "other"


def build_taste_profile(orders: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """生成口味画像。

    输出刻意做成「可以直接当文案念」的形式（``headline`` / ``traits``），
    因为 Skill 的最终交付是对话回复，不是报表。
    """
    timed = _orders_with_time(orders)
    if not timed:
        return {"orders": 0, "headline": "还没有足够的历史订单，先去点一单吧。", "traits": []}

    item_counter: Counter = Counter()
    role_counter: Counter = Counter()
    category_counter: Counter = Counter()
    store_counter: Counter = Counter()
    set_count = 0
    item_total = 0
    total_spend = 0.0
    spends: list[float] = []

    for order in timed:
        total_spend += float(order.get("total_amount") or 0.0)
        spends.append(float(order.get("total_amount") or 0.0))
        store = order.get("store_name")
        if store:
            store_counter[store] += 1
        for item in order.get("items") or []:
            name = item.get("name")
            qty = int(item.get("quantity") or 1)
            if name:
                item_counter[name] += qty
                category_counter[str(item.get("category") or "未分类")] += qty
            role_counter[_classify_role(item)] += qty
            item_total += qty
            if item.get("is_set"):
                set_count += 1

    n = len(timed)
    avg = total_spend / n if n else 0.0
    sorted_spends = sorted(spends)
    median = (
        sorted_spends[len(sorted_spends) // 2] if sorted_spends else 0.0
    )

    # ---- 画像标签生成 ----
    traits: list[dict[str, Any]] = []

    late = sum(1 for o in timed if (o["created_at"].hour >= 21 or o["created_at"].hour < 5))
    if n and late / n >= 0.25:
        traits.append(
            {
                "tag": "深夜食客",
                "detail": f"{late}/{n} 单下在21 点后，最晚的一单在 {_latest_late_night_text(timed)}",
            }
        )

    morning = sum(1 for o in timed if 5 <= o["created_at"].hour < 10)
    if n and morning / n >= 0.15:
        traits.append(
            {"tag": "早餐党", "detail": f"{morning}/{n} 单在上午 10 点前下的，早起的麦麦是有回报的"}
        )

    if item_total and set_count / item_total >= 0.3:
        traits.append({"tag": "套餐信徒", "detail": "多数订单直接选套餐，省心派"})

    if role_counter["drink"] and role_counter["main"]:
        traits.append(
            {
                "tag": "干饭结构",
                "detail": f"主食 {role_counter['main']} 份 vs 饮品 {role_counter['drink']} 杯",
            }
        )

    if item_total and role_counter["dessert"] / item_total >= 0.2:
        traits.append({"tag": "甜品刚需", "detail": "甜点在你的订单里占比不低，甜是刚需"})

    top_store, top_store_n = (store_counter.most_common(1)[0] if store_counter else (None, 0))
    if top_store and n and top_store_n / n >= 0.3:
        traits.append({"tag": "回头客", "detail": f"{top_store_n}/{n} 单来自「{top_store}」"})

    if not traits:
        traits.append({"tag": "麦麦新面孔", "detail": f"目前有 {n} 单记录，再多点几单画像更准"})

    top_items = [{"name": n_, "times": c} for n_, c in item_counter.most_common(8)]

    headline = (
        f"{n} 单、{total_spend:.0f} 元、"
        f"平均 {avg:.1f} 元/单，"
        f"最常点「{top_items[0]['name'] if top_items else '—'}」"
    )

    return {
        "orders": n,
        "headline": headline,
        "traits": traits,
        "top_items": top_items,
        "categories": [{"name": c, "times": t} for c, t in category_counter.most_common(6)],
        "roles": [{"role": r, "count": c} for r, c in role_counter.most_common()],
        "avg_spend": round(avg, 2),
        "median_spend": round(median, 2),
        "total_spend": round(total_spend, 2),
        "favorite_store": top_store,
        "distinct_items": len(item_counter),
    }


def _latest_late_night_text(timed: Sequence[Mapping[str, Any]]) -> str:
    """深夜单里最晚的一单是什么时间。

    ⚠️ 不能直接取全部订单的最大时间——那可能是早上的早餐单，
    会输出「深夜食客 · 最晚的一单在 08:30」这种自相矛盾的话。
    """
    late = [
        o["created_at"]
        for o in timed
        if o["created_at"].hour >= _LATE_NIGHT_START or o["created_at"].hour < _LATE_NIGHT_END
    ]
    if not late:
        return "无"
    # ⚠️ 按「时刻」而非完整 datetime 比较。若按 datetime 取 max，
    # 9-20 22:10 会盖过 9-19 23:40（日期更大），但"最晚的一单"
    # 问的是一天里几点最晚，答案应是 23:40。
    latest = max(late, key=lambda t: t.hour * 60 + t.minute)
    if latest.hour < _LATE_NIGHT_END:
        return f"凌晨 {latest.hour:02d}:{latest.minute:02d}"
    return f"{latest.hour:02d}:{latest.minute:02d}"


def rank_moments(orders: Sequence[Mapping[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """挑出最有故事性的几单，供「随机来一口」/「回到某天」使用。

    排序偏好：深夜 > 特殊门店> 高客单。这样选出来的订单最适合发朋友圈。
    """
    timed = _orders_with_time(orders)
    if not timed:
        return []

    def score(order: Mapping[str, Any]) -> tuple[int, int, float]:
        """排序键：深夜优先 → 时刻越晚越优先 → 客单越高越优先。

        深夜单内部按 ``hour * 60 + minute`` 比较而非完整 datetime，
        否则「9-19 23:40」会排在「9-20 22:10」之后（日期更大），
        与「挑出故事性最强的单」的意图相悖。
        """
        when = order["created_at"]
        night = 1 if (when.hour >= 21 or when.hour < 5) else 0
        minute_of_day = when.hour * 60 + when.minute
        spend = float(order.get("total_amount") or 0.0)
        return (night, minute_of_day, spend)

    ranked = sorted(timed, key=score, reverse=True)[:limit]
    return [
        {
            "order_id": o.get("order_id"),
            "when": o["created_at"].strftime("%Y-%m-%d %H:%M"),
            "store": o.get("store_name"),
            "channel": o.get("channel"),
            "total_amount": o.get("total_amount"),
            "items": [
                {"name": i.get("name"), "quantity": i.get("quantity")}
                for i in (o.get("items") or [])
            ],
            "story": _story_line(o),
        }
        for o in ranked
    ]


def _story_line(order: Mapping[str, Any]) -> str:
    """为单笔订单生成一句可朗读的描述。"""
    when = order["created_at"]
    names = [i.get("name") for i in (order.get("items") or []) if i.get("name")]
    food = "、".join(names[:3]) if names else "一份没记住名字的单"
    store = order.get("store_name") or "某家门店"
    if when.hour >= 21 or when.hour < 5:
        return f"{when.strftime('%m-%d')} 深夜 {when.hour:02d}:{when.minute:02d}，在{store}，你吃了{food}。"
    return f"{when.strftime('%m-%d')} {when.hour:02d}:{when.minute:02d}，{store}，{food}。"


def replay_order(
    orders: Sequence[Mapping[str, Any]],
    order_id: str | None = None,
    when: str | None = None,
) -> dict[str, Any]:
    """生成复刻方案。

    :param order_id: 精确指定订单 id
    :param when: 宽松匹配，格式 ``YYYY-MM-DD`` 或 ``YYYY-MM`` 或 ``MM-DD``

    返回 ``{"found": bool, "order": {...}, "recipe": [...], "hints": [...]}``。
    ``recipe`` 是「照着这份清单重新点」的步骤化建议；真实下单必须由
    Agent 再走 ``calculate-price`` +用户确认 + ``create-order``，
    本函数**绝不触碰资金相关动作**。
    """
    timed = _orders_with_time(orders)
    if not timed:
        return {
            "found": False,
            "reason": "没有带时间戳的历史订单可用于复刻",
            "order": None,
            "recipe": [],
            "hints": [],
        }

    target: Mapping[str, Any] | None = None

    if order_id:
        for o in timed:
            if str(o.get("order_id")) == str(order_id):
                target = o
                break
        if target is None:
            return {
                "found": False,
                "reason": f"没找到订单号 {order_id}",
                "order": None,
                "recipe": [],
                "hints": [],
            }
    elif when:
        needle = when.strip()
        for o in timed:
            dt: _dt.datetime = o["created_at"]
            if dt.strftime("%Y-%m-%d") == needle:
                target = o
                break
        if target is None:
            for o in timed:
                dt = o["created_at"]
                if dt.strftime("%Y-%m") == needle or dt.strftime("%m-%d") == needle:
                    target = o
                    break
        if target is None:
            available = sorted({o["created_at"].strftime("%Y-%m") for o in timed})
            return {
                "found": False,
                "reason": f"没找到 {needle} 的订单",
                "available_months": available[:12],
                "order": None,
                "recipe": [],
                "hints": [],
            }
    else:
        # 默认复刻最近一单
        target = max(timed, key=lambda o: o["created_at"])

    items = target.get("items") or []
    recipe = []
    for item in items:
        recipe.append(
            {
                "name": item.get("name"),
                "code": item.get("code"),
                "quantity": item.get("quantity"),
                "is_set": item.get("is_set"),
                "note": "套餐需用 query-meal-detail 查当前可替换项后再确认"
                if item.get("is_set")
                else None,
            }
        )

    hints: list[str] = []
    if target.get("channel") == "delivery":
        hints.append("这一单是麦乐送外送，复刻需重新确认配送地址与预计送达时间")
    if target.get("channel") == "self_pickup":
        hints.append("这一单是到店自提，复刻时可直接比对当前门店菜单是否一致")
    if any(i.get("is_set") for i in items):
        hints.append("含套餐：MCP 1.0.5 起支持更换套餐内商品组合，可在原价不变的前提下微调")

    return {
        "found": True,
        "order": {
            "order_id": target.get("order_id"),
            "when": target["created_at"].strftime("%Y-%m-%d %H:%M"),
            "store": target.get("store_name"),
            "channel": target.get("channel"),
            "total_amount": target.get("total_amount"),
        },
        "recipe": recipe,
        "hints": hints,
        "disclaimer": "本函数仅生成复刻清单，不会自动下单；下单需你显式确认。",
    }
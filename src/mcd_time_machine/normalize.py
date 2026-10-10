"""麦麦时光机 · 订单记录容错归一化层。

设计动机
--------
麦当劳 MCP 的 `order-list` / `query-order` 出参字段名在不同版本、不同
就餐方式（到店取餐 / 麦乐送 / 团餐 / 主题活动）下并不完全一致。本模块
不猜测、不写死任何单一字段名，而是维护一组**候选别名**，在运行时按
优先级探测，保证：

1. MCP 侧字段变更时，引擎不需要改代码；
2. 拿不到 Token 也能用本地 fixture 跑通全流程（便于 CI 与演示）；
3. 归一化过程对未知字段保留在 ``raw`` 中，便于排查。

所有函数均为纯函数，无网络、无副作用。
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "FIELD_ALIASES",
    "pick",
    "normalize_order",
    "normalize_orders",
    "parse_datetime",
]

# ---------------------------------------------------------------------------
# 字段别名表
# ---------------------------------------------------------------------------
# 每个逻辑字段 -> 候选键名（小写比较）。顺序即优先级。
# 之所以写成"候选列表"而不是单个键：MCP 出参在 1.0.6 之后陆续新增过
# 历史订单查询、优惠价格展示、取消订单等能力，字段命名存在历史包袱。

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # --- 标识 ---
    "order_id": ("orderid", "orderno", "ordercode", "id", "order_id", "order_no"),
    # --- 时间（2026-10-10 实测确认 createTime 存在，格式 "YYYY-MM-DD HH:MM"）---
    "created_at": (
        "createtime",
        "createdat",
        "create_time",
        "ordertime",
        "ordercreatetime",
        "order_time",
        "time",
        "placedat",
        "place_time",
    ),
    "finished_at": (
        "finishtime",
        "finishedat",
        "finish_time",
        "arrivetime",
        "complete_time",
        "completedtime",
    ),
    # --- 金额 ---
    # ⚠️ 2026-10-10 实测：order-list 列表项只有 realTotalAmount（字符串，如 "84.00"，单位=元）。
    # totalAmount/payAmount 等仅在 query-order 明细中出现，故 realTotalAmount 优先级最高。
    "total_amount": (
        "realtotalamount",
        "realtotalprice",
        "totalamount",
        "totalprice",
        "payamount",
        "payamt",
        "amount",
        "total",
    ),
    "discount_amount": (
        "discountamount",
        "discount",
        "preferentialamount",
        "reduceamount",
        "youhui",
    ),
    # --- 就餐方式（实测键名 orderType，值域含中文「堂食」等）---
    "channel": (
        "ordertype",
        "diningtype",
        "diningmode",
        "servicetype",
        "channeltype",
        "channel",
        "type",
    ),
    "store_name": (
        "storename",
        "store_name",
        "shopname",
        "restaurantname",
        "storetext",
    ),
    "store_code": ("storecode", "store_code", "shopcode"),
    "order_status": ("orderstatus", "order_status", "status"),
    # --- 明细 ---
    # ⚠️ 2026-10-10 实测：真实键名为 orderProductList（商品数组），
    # 且套餐子项在 comboItemList 里——先前猜测的 "items"/"goodsList" 均未命中。
    "items": (
        "orderproductlist",
        "orderproduct",
        "items",
        "goodslist",
        "products",
        "productlist",
        "orderitems",
        "order_items",
        "details",
        "detail",
        "goods",
    ),
}

# 商品行字段别名（item 层）
# ⚠️ 2026-10-10 实测（order-list 的 orderProductList 元素）：
#   productCode / productName / quantity / comboItemList
#   —— **没有 price 字段**。列表页不给单价，金额只在订单级 realTotalAmount。
#   因此 item.line_total 在列表场景下会算成 0，属预期；单价需 query-order 补齐。
_ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("productname", "name", "goodsname", "itemname", "title"),
    "code": ("productcode", "code", "goodscode", "itemcode", "skuid", "id"),
    "quantity": ("quantity", "qty", "num", "count", "numcount"),
    "price": (
        "price",
        "amount",
        "unitprice",
        "singleprice",
        "saleprice",
        "realprice",
        "totalprice",
    ),
    "category": ("categoryname", "category", "classname", "typename", "sort"),
    "is_set": ("isset", "iscombo", "is_setmeal", "comboflag", "combo"),
    "sub_items": ("comboitemlist", "comboitems", "subitems", "subitemlist"),
}

# 归一化后的统一就餐方式标签
_CHANNEL_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("delivery", ("delivery", "takeout", "waimai", "shipping", "express", "麦乐送", "外送", "配送")),
    ("dine_in", ("dinein", "dine_in", "restaurant", "onsite", "堂食")),
    (
        "self_pickup",
        (
            "selfpickup",
            "pickup",
            "takeaway",
            "qujiao",
            "daodian",
            "自取",
            "取餐",
            "到店",
            "外带",
            "得来速",
        ),
    ),
    ("group_meal", ("groupmeal", "group", "tuancan", "canteen", "团餐")),
    ("party", ("party", "theme", "activity", "派对", "主题活动")),
    ("mall", ("mall", "shop", "shangcheng", "商城")),
)

_CENT_PATTERN = re.compile(r"^-?\d+(\.\d{1,2})?$")


def _norm_key(key: Any) -> str:
    """键名归一化：小写、去掉空格/下划线/连字符，便于宽松匹配。

    ⚠️ 必须**保留中文字符**：MCP 出参的枚举值（如 ``diningType="麦乐送"``）
    与商品名称都是中文，早期版本用``[一-鿿]`` 剔除中文导致所有中文匹配静默失效。
    """
    return re.sub(r"[\s_\-]", "", str(key)).lower()


def pick(source: Mapping[str, Any] | None, field: str) -> Any:
    """按``FIELD_ALIASES[field]`` 的优先级从 dict 中取第一个命中的值。

    匹配是大小写/下划线不敏感的。若所有候选都没命中，返回 ``None``
    （而不是抛异常）——缺字段是MCP 侧的正常情况，调用方自行决定降级策略。
    """
    if not isinstance(source, Mapping):
        return None
    aliases = FIELD_ALIASES.get(field, (field,))
    # 先按归一化键建索引，避免 O(n*m) 反复遍历
    index: dict[str, Any] = {}
    for k, v in source.items():
        index.setdefault(_norm_key(k), v)
    for alias in aliases:
        hit = _norm_key(alias)
        if hit in index:
            return index[hit]
    return None


def _pick_from(source: Mapping[str, Any] | None, field: str) -> Any:
    """item 层取值的内部版本（使用 ``_ITEM_ALIASES``）。"""
    if not isinstance(source, Mapping):
        return None
    aliases = _ITEM_ALIASES.get(field, (field,))
    index: dict[str, Any] = {}
    for k, v in source.items():
        index.setdefault(_norm_key(k), v)
    for alias in aliases:
        hit = _norm_key(alias)
        if hit in index:
            return index[hit]
    return None


_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y%m%d%H%M%S",
    "%Y%m%d",
)


def parse_datetime(value: Any) -> _dt.datetime | None:
    """尽最大努力把 MCP 的时间字段解析成 ``datetime``。

    依次尝试：datetime/date 对象 → 13位/10位 毫秒/秒 时间戳 →
    ISO8601 字符串 → 若干常见中文格式字符串。全部失败返回 ``None``。
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.isdigit() and len(value) in (10, 13)
    ):
        try:
            num = float(value)
            if num > 1e11:  # 毫秒
                num /= 1000.0
            return _dt.datetime.fromtimestamp(num)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("T", " ")
        # 去掉时区后缀与毫秒小数，交给 strptime
        text = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", text).strip()
        text = re.sub(r"\.\d+$", "", text)
        for fmt in _DT_FORMATS:
            try:
                return _dt.datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _to_yuan(value: Any, cents_hint: bool | None = None) -> float | None:
    """金额归一化到「元」。

    **2026-10-10 实测结论（order-list）**：``realTotalAmount`` 是**字符串元**，
    形如 ``"84.00"``。因此：

    - ``cents_hint=True``：强制按分÷100
    - ``cents_hint=False``：强制按元
    - ``cents_hint=None``（默认）：启发式——无小数点的整数且 abs>=1000 视为分

    实测数据里 ``"84.00"`` 这类带小数的值会走"按元"分支，故启发式对真实数据安全。
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("￥", "").replace("元", "")
        if not _CENT_PATTERN.match(text):
            return None
        value = float(text)
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if cents_hint is True:
        return number / 100.0
    if cents_hint is False:
        return number
    # 启发式：无小数部分且数值较大 ⇒ 视为分
    if number == int(number) and abs(number) >= 1000:
        return number / 100.0
    return number


def _norm_channel(value: Any) -> str | None:
    if value is None:
        return None
    text = _norm_key(value)
    for label, keys in _CHANNEL_MAP:
        if any(k in text for k in keys):
            return label
    return text or None


def _normalize_item(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    """把一个商品行归一化为统一结构。"""
    name = _pick_from(raw_item, "name")
    code = _pick_from(raw_item, "code")
    qty = _pick_from(raw_item, "quantity")
    price = _to_yuan(_pick_from(raw_item, "price"))

    try:
        qty_int = int(float(qty)) if qty not in (None, "") else 1
    except (TypeError, ValueError):
        qty_int = 1

    try:
        price_float = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        price_float = 0.0

    sub_raw = _pick_from(raw_item, "sub_items")
    sub_items = _extract_items(sub_raw)

    is_set_raw = _pick_from(raw_item, "is_set")
    if isinstance(is_set_raw, bool):
        is_set = is_set_raw
    elif is_set_raw in (0, 1):
        is_set = bool(is_set_raw)
    elif isinstance(is_set_raw, str):
        is_set = _norm_key(is_set_raw) in {"true", "1", "yes", "是"}
    else:
        # 判据优先级：comboItemList 非空 > 名称关键词。
        # 实测中套餐一定带 comboItemList，是比名称匹配更硬的信号。
        is_set = bool(sub_items) or any(
            k in _norm_key(name) for k in ("套餐", "超值", "双享", "taocan", "combo", "setmeal")
        )

    return {
        "name": str(name).strip() if name is not None else None,
        "code": str(code).strip() if code is not None else None,
        "quantity": qty_int,
        "unit_price": price_float,
        "line_total": round(price_float * qty_int, 2),
        "category": _pick_from(raw_item, "category"),
        "is_set": is_set,
        "sub_items": sub_items,
        "raw": dict(raw_item),
    }


def _extract_items(raw_items: Any) -> list[dict[str, Any]]:
    """从各种可能的容器形态里挖出商品列表。

    MCP 出参可能是：直接list、``{"items": [...]}``、或 list of dict。
    这里统一成 list[dict]，非dict 元素按 ``{"name": str(el)}`` 处理。
    """
    if raw_items is None:
        return []
    if isinstance(raw_items, Mapping):
        raw_items = pick(raw_items, "items") or []
    if isinstance(raw_items, str):
        return []
    if not isinstance(raw_items, Sequence):
        return []

    result: list[dict[str, Any]] = []
    for element in raw_items:
        if isinstance(element, Mapping):
            result.append(_normalize_item(element))
        elif isinstance(element, str) and element.strip():
            result.append(_normalize_item({"name": element.strip()}))
    return result


def normalize_order(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """把单条原始订单归一化为统一结构。

    返回 ``None`` 表示这条数据无法识别为订单（调用方应跳过而非崩溃）。
    关键取舍：**不因为缺时间戳就丢弃订单**——时间轴是这个 Skill 的核心，
    但「点了什么」同样有价值，所以缺时间时 ``created_at`` 为 ``None``，
    由分析层决定降级处理方式。
    """
    if not isinstance(raw, Mapping):
        return None

    items = _extract_items(pick(raw, "items"))
    created = parse_datetime(pick(raw, "created_at"))
    order_id = pick(raw, "order_id")

    # 判定是否为有效订单：至少要有 id、时间、明细三者之一
    if not items and order_id in (None, "") and created is None:
        return None

    total = _to_yuan(pick(raw, "total_amount"))
    if total is None and items:
        total = round(sum(i["line_total"] for i in items), 2)

    return {
        "order_id": str(order_id).strip() if order_id not in (None, "") else None,
        "created_at": created,
        "finished_at": parse_datetime(pick(raw, "finished_at")),
        "total_amount": total,
        "discount_amount": _to_yuan(pick(raw, "discount_amount")),
        "channel": _norm_channel(pick(raw, "channel")),
        "store_name": (
            str(pick(raw, "store_name")).strip()
            if pick(raw, "store_name") not in (None, "")
            else None
        ),
        "store_code": (
            str(pick(raw, "store_code")).strip()
            if pick(raw, "store_code") not in (None, "")
            else None
        ),
        "order_status": (
            str(pick(raw, "order_status")).strip()
            if pick(raw, "order_status") not in (None, "")
            else None
        ),
        "items": items,
        "item_count": sum(i["quantity"] for i in items),
        "raw": dict(raw),
    }


def normalize_orders(
    payload: Any,
) -> list[dict[str, Any]]:
    """从 MCP ``order-list`` 的完整出参里提取并归一化订单列表。

    兼容三种外层形态：
    - ``{"orders": [...]}`` / ``{"list": [...]}`` / ``{"records": [...]}``
    - 直接是 ``[...]``
    - 包了两层（``{"data": {"list": [...]}}``）

    这是"拿到 Token 就能直接跑"的关键：真实出参外层结构尚未验证，
    这里一次性把常见形态都兜住，避免首次实测就因结构不符而返工。
    """
    # 逐层下钻，最多 3 层
    current = payload
    for _ in range(3):
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            break
        if not isinstance(current, Mapping):
            return []
        nxt = None
        for key in (
            "list",
            "orders",
            "orderlist",
            "records",
            "items",
            "data",
            "result",
            "orderproductlist",
        ):
            candidate = current.get(key)
            if isinstance(candidate, (Mapping, list)):
                nxt = candidate
                break
        if nxt is None:
            return []
        current = nxt

    if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
        return []

    normalized = (normalize_order(element) for element in current)
    return [order for order in normalized if order is not None]
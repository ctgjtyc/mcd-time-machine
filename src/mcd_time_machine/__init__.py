"""麦麦时光机 —— 基于麦当劳中国官方 MCP 的订单复刻助手。

2026 麦当劳程序员创意开发大赛参赛作品。

用历史订单做三件事：看时间轴、画口味画像、复刻任意一单。
"""

from __future__ import annotations

from .analyze import (
    build_timeline,
    build_taste_profile,
    rank_moments,
    replay_order,
)
from .normalize import normalize_order, normalize_orders, parse_datetime

__version__ = "1.0.0"

__all__ = [
    "build_timeline",
    "build_taste_profile",
    "rank_moments",
    "replay_order",
    "normalize_order",
    "normalize_orders",
    "parse_datetime",
    "__version__",
]
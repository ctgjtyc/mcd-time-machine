# examples

- `sample-orders.json` — 5 笔与 MCP `order-list` 出参同构的样例订单（已脱敏，非真实用户订单）。
  字段名与官方 MCP 一致：`orderId` / `createTime` / `totalAmount` / `diningType` / `storeName` / `items`。

## 跑起来

```bash
# 1) 离线演示（内置同样这 5 笔样例，三个维度全量输出）
python scripts/demo.py

# 2) 用本文件自己跑一遍三个分析维度
python -c "
import sys, json; sys.path.insert(0, 'src')
from mcd_time_machine import normalize_orders, build_timeline, build_taste_profile, rank_moments
orders = normalize_orders(json.load(open('examples/sample-orders.json')))
out = {
    'timeline': build_timeline(orders),
    'profile' : build_taste_profile(orders),
    'moments' : rank_moments(orders),
}
print(json.dumps(out, ensure_ascii=False, indent=2))
"
```

完整输出见 [`docs/demo-output.txt`](../docs/demo-output.txt)。

> 注：分析能力由 `build_timeline` / `build_taste_profile` / `rank_moments` / `replay_order`
> 四个函数提供（没有单一的 `analyze()` 入口）——按需取用即可。

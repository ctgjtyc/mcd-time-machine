# 麦当劳 MCP 工具速查（实测校准用）

> 数据来源：[M-China/mcd-mcp-server](https://github.com/M-China/mcd-mcp-server) README v1.0.9（2026-09-10）
> 接入地址 `https://mcp.mcd.cn`，协议 Streamable HTTP，限流 600 次/分钟。

## 本 Skill 实际用到的工具

| Tool | 用途 | 在链路中的位置 |
|---|---|---|
| `now-time-info` | 当前完整时间 | 第一步，解析"上个月""最近"等相对表述 |
| `order-list` | 近期到店/外送历史订单 | 第二步，数据主干 |
| `query-order` | 单笔订单详情（含配送信息） | 第四步，复刻时补全明细 |
| `query-meal-detail` | 餐品详情 / 套餐组成 / **可替换项** | 第四步，复刻含套餐时必用 |
| `query-meals` | 当前门店在售菜单 | 第四步，比对历史菜品是否仍在售 |
| `calculate-price` | 含券价格计算 | 第四步，复刻前复核实付 |
| `query-nearby-stores` | 附近可用门店 | 复刻外送单时重新定位门店 |
| `query-meal-assistance` | 团餐助餐服务 | 若历史单含团餐场景 |

**明确不使用**（超出本 Skill 边界，且涉及资金或权益变更）：
`create-order`、`cancel-order`、`draw-lottery`、`mall-create-order`、`party-order-create`、`auto-bind-coupons`。

## 出参容错字段别名表

MCP 出参字段名在不同版本与不同就餐方式下不完全一致。
本Skill 的 `normalize.py` 按下列优先级做宽松匹配（键名比较忽略大小写、
下划线、连字符；**保留中文**）。

### 订单层

| 逻辑字段 | 候选键名（按优先级） |
|---|---|
| `order_id` | `orderId` → `orderNo` → `orderCode` → `id` |
| `created_at` | `createTime` → `createTime` → `orderTime` → `orderCreateTime` → `time` → `placeAt` |
| `finished_at` | `finishTime` → `arriveTime` → `completeTime` |
| `total_amount` | `totalAmount` → `totalPrice` → `payAmount` → `amount` |
| `discount_amount` | `discountAmount` → `discount` → `preferentialAmount` → `reduceAmount` |
| `channel` | `diningType` → `diningMode` → `orderType` → `serviceType` → `channel` |
| `store_name` | `storeName` → `shopName` → `restaurantName` |
| `items` | `items` → `goodsList` → `products` → `orderItems` → `details` → `goods` |

### 商品层

| 逻辑字段 | 候选键名 |
|---|---|
| `name` | `name` → `goodsName` → `productName` → `itemName` |
| `code` | `code` → `productCode` → `goodsCode` → `skuId` |
| `quantity` | `quantity` → `qty` → `num` → `count` |
| `price` | `price` → `amount` → `unitPrice` → `salePrice` |
| `is_set` | `isSet` → `isCombo` → `isSetMeal`（缺失时按名称含"套餐/超值/双享"推断） |

## 时间字段解析规则

`parse_datetime()` 依次尝试：

1. `datetime` / `date` 对象 → 直接用
2. 10 位（秒）或 13 位（毫秒）时间戳→ `fromtimestamp`
3. ISO 8601 字符串（含 `T` 分隔符、`Z` 后缀）→ 剥离后 `fromisoformat`
4. 8 种常见格式串：`%Y-%m-%d %H:%M:%S` / `%Y-%m-%d %H:%M` / `%Y-%m-%d` /
   `%Y/%m/%d %H:%M:%S` / `%Y/%m/%d %H:%M` / `%Y/%m/%d` / `%Y%m%d%H%M%S` / `%Y%m%d`

全部失败返回 `None`，**不抛异常**。

## 金额单位陷阱

MCP 侧金额通常是**分**（整数）。`normalize.py` 的启发式规则：

- 数值为整数且 `abs >= 1000` → 视为分，除以 100
- 其余 → 视为元，原样保留

⚠️ 若 MCP 返回的是「元」，且恰好是 1000 元整，会被误判为 10000分。
拿到真实Token 后应实测一单确认单位，必要时改为强制单位开关。

## 就餐方式归一化

| 归一标签 | 匹配关键词 |
|---|---|
| `delivery` | delivery / takeout / waimai / shipping / 麦乐送 / 外送 / 配送 |
| `dine_in` | dinein / restaurant / onsite / 堂食 |
| `self_pickup` | selfpickup / pickup / takeaway / qujiao / daodian / 自取 / 取餐 / 到店 / 外带 / 得来速 |
| `group_meal` | groupmeal / group / tuancan / canteen / 团餐 |
| `party` | party / theme / activity / 派对 / 主题活动 |
| `mall` | mall / shop / shangcheng / 商城 |

匹配方式为**子串包含**（在归一化后的小写串上）。

## 实测补丁记录

拿到 MCP Token 后，按下列清单回填本节：

- [ ] `order-list` 真实出参的外层包裹结构（是否为 `{"orders": [...]}`）
- [ ] 金额单位确认（分 or 元）
- [ ] `createTime` 的真实格式
- [ ] `items` 内商品字段的真实键名
- [ ] `diningType` 的真实枚举值
- [ ] 分页机制（是否有 `pageNum` / `pageSize` / `hasMore`，是否需要循环拉取）

## 错误码

| code | 原因 | 处理 |
|---|---|---|
| 401 | Token 无效/过期/未提供 | 检查 Authorization 头，**不要**把 Token 写进仓库 |
| 429 | 超 600 次/分钟 | 降频；批量分析优先在本地做，只用 MCP 拉原始数据 |
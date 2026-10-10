# 麦当劳 MCP 工具速查（2026-10-10 实测校准）

> 数据来源：[M-China/mcd-mcp-server](https://github.com/M-China/mcd-mcp-server) README v1.0.9
> **本文档已由真实 Token 实测校准（2026-10-10）**，标「实测」的内容均来自真实响应。

## ⚠️ 接入地址与官方文档不一致（实测发现）

| 来源 | 地址 | 实测结果 |
|---|---|---|
| 官方 README 写的 | `https://mcp.mcd.cn/mcp` | ❌ **404 Route Not Found** |
| 实测可用 | `https://mcp.mcd.cn`（根路径） | ✅ 握手成功 |

探路记录（2026-10-10）：

| 路径 | 响应 |
|---|---|
| `/mcp` | 404 |
| `/mcp/` | 404 |
| `/sse` | 404 |
| `/messages/` | 404 |
| `/` | **200**，返回 `serverInfo: {name: "mcd-mcp", version: "1.0.0"}` |

握手响应（实测）：

```json
{"jsonrpc":"2.0","id":1,"result":{
  "capabilities":{"tools":{}},
  "protocolVersion":"2024-11-05",
  "serverInfo":{"name":"mcd-mcp","version":"1.0.0"}}}
```

⇒ **若按官方文档配 `/mcp` 会拿到 404 而非 401，容易误判成 Token 无效。**
本项目 `mcp-config.example.json` 已按实测地址配置。

## 接入信息

| 项 | 值 |
|---|---|
| **接入地址（实测）** | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 限流 | 每 Token 每分钟 600 次请求，超限返回 429 |
| 错误码 | 401 Token 无效/过期；429 触发限流；404 路径错误（实测踩过） |
| 工具总数（实测） | **35 个** |

## 实测：`order-list` 真实出参 schema

调用 `tools/call {"name":"order-list","arguments":{}}`，服务端返回结构描述如下：

```
data.list[]                      订单列表（array）
  .orderId                string  订单编码
  .orderType              string  订单类型（如「堂食」）
  .createTime                    string  下单时间（如 "2026-10-08 19:12"）
  .beType                 string  门店类型
  .beCode                 string  BE 编码
  .storeCode              string  门店编码
  .storeName              string  门店名称
  .orderStatus            string  订单状态
  .orderProductList       array   商品
    .productCode          string  商品 code
    .productName          string  商品名称
    .quantity             integer 商品数量
    .comboItemList        array   子项列表（套餐子项）
  .realTotalAmount        string  总金额 —— ⚠️ **字符串，单位元**，如 "84.00"
```

**外层包裹（实测）**：`{"success":true,"code":200,"message":"请求成功","data":{"list":[...]}}`

新账号（无历史订单）实测返回：`{"data":{}}` —— 空对象，**不抛异常**。

### 由此确认的关键事实

1. **商品数组键名是 `orderProductList`**，不是文档示例里的 `items`/`goodsList`。
   先前按猜测写的别名全部未命中，导致商品被整体丢弃。
2. **金额键名是 `realTotalAmount`，类型是字符串，单位元**（`"84.00"`）。
   先前按「分为单位」处理会得到 0.84 元这种错误结果。
3. **就餐方式键名是 `orderType`**，值域含中文（「堂食」等）。
4. **列表页没有单价字段**。item 级单价需 `query-order` 补齐，
   因此分析层的 `line_total` 在仅用 order-list 时会为 0 —— 属预期行为，
   总额一律以订单级 `realTotalAmount` 为准。
5. **套餐的可靠标志是 `comboItemList` 非空**，比按名称匹配「套餐」二字更准。

## 本 Skill 实际用到的工具

| Tool | 用途 | 实测状态 |
|---|---|---|
| `now-time-info` | 当前完整时间 | ✅ 实测通过 |
| `order-list` | 近期到店/外送历史订单 | ✅ 实测通过（schema 已校准） |
| `query-order` | 单笔订单详情（含配送信息） | 未实测（账号无订单） |
| `query-meal-detail` | 餐品详情 / 套餐组成 / 可替换项 | 未实测 |
| `query-meals` | 当前门店在售菜单 | 未实测 |
| `calculate-price` | 含券价格计算 | 未实测 |
| `query-nearby-stores` | 附近可用门店 | 未实测 |
| `query-my-account` | 我的积分 | ✅ 实测通过 |

**明确不使用**（超出本 Skill 边界，且涉及资金或权益变更）：
`create-order`、`cancel-order`、`draw-lottery`、`mall-create-order`、
`party-order-create`、`auto-bind-coupons`。

### 实测：`now-time-info` 出参字段

```
data.year / month / day / dayOfWeek / time / date / datetime / timestamp
data.timezone / offset / utc / formatted
```

### 实测：`query-my-account` 出参字段

```
data.accountId                    string  账户 ID
data.availablePoint               积分    可用积分
data.accumulativePoint            积分    累计积分
data.frozenPoint                  积分    冻结积分
data.usedPoint                    积分    已用积分
data.expiredPoint                 积分    已过期积分
data.currentMouthExpirePoint      积分    本月即将过期
data.nextMouthExpirePoint         积分    下月即将过期
data.lastMouthExpirePoint         积分    上月已过期
data.currency                     string
```

> 该账号实测返回全 0（从未消费），符合预期。

## 出参容错字段别名表（已按实测校准）

MCP 出参字段名在不同版本、不同就餐方式（到店取餐 / 麦乐送 / 团餐 / 主题活动）
下并不完全一致。`src/mcd_time_machine/normalize.py` 因此维护候选别名表，
运行时按优先级探测。**实测确认的字段排在最前。**

### 订单层

| 逻辑字段 | 候选键名（实测字段优先） |
|---|---|
| `order_id` | `orderId` ✅实测 → `orderNo` → `orderCode` → `id` |
| `created_at` | `createTime` ✅实测 → `orderTime` → `time` → `placeAt` |
| `total_amount` | `realTotalAmount` ✅实测 → `totalAmount` → `payAmount` → `amount` |
| `channel` | `orderType` ✅实测 → `diningType` → `serviceType` → `channel` |
| `store_name` | `storeName` ✅实测 → `shopName` → `restaurantName` |
| `store_code` | `storeCode` ✅实测 → `shopCode` |
| `order_status` | `orderStatus` ✅实测 → `status` |
| `items` | **`orderProductList` ✅实测** → `items` → `goodsList` → `products` |

### 商品层

| 逻辑字段 | 候选键名 |
|---|---|
| `name` | **`productName` ✅实测** → `name` → `goodsName` |
| `code` | **`productCode` ✅实测** → `code` → `goodsCode` |
| `quantity` | `quantity` ✅实测 → `qty` → `num` |
| `price` | `price` → `amount` → `unitPrice`（⚠️ order-list 不提供，需 query-order） |
| `sub_items` | **`comboItemList` ✅实测** → `comboItems` |
| `is_set` | 优先 `comboItemList` 非空判定，其次名称含「套餐/超值/双享」 |

## 时间字段解析规则

实测 `createTime` 格式为 `"YYYY-MM-DD HH:MM"`（无秒）。
`parse_datetime()` 依次尝试：

1. `datetime` / `date` 对象
2. 10 位（秒）/ 13 位（毫秒）时间戳
3. ISO 8601（含 `T` 分隔符、`Z` 后缀）
4. 8 种常见格式串（含实测的 `%Y-%m-%d %H:%M`）

全部失败返回 `None`，**不抛异常**。

## 金额单位（实测已确认）

**`realTotalAmount` 是字符串元，如 `"84.00"`。**

`_to_yuan()` 的判定顺序：

1. `cents_hint=True` → 强制按分 ÷100
2. `cents_hint=False` → 强制按元
3. 默认启发式 → 无小数点的整数且 abs≥1000 视为分，其余按元

真实数据 `"84.00"` 带小数，稳走「按元」分支。
⚠️ 唯一残留风险：若某单恰好是 `"1000"`（无小数点的整千元），会被误判为 10000 分。
概率极低；如需绝对稳妥，调用方传 `cents_hint=False`。

## 就餐方式归一化

| 归一标签 | 匹配关键词 |
|---|---|
| `delivery` | delivery / takeout / waimai / shipping / 麦乐送 / 外送 / 配送 |
| `dine_in` | dinein / restaurant / onsite / 堂食 ✅实测命中 |
| `self_pickup` | selfpickup / pickup / takeaway / qujiao / daodian / 自取 / 取餐 / 到店 / 外带 / 得来速 |
| `group_meal` | groupmeal / group / tuancan / canteen / 团餐 |
| `party` | party / theme / activity / 派对 / 主题活动 |
| `mall` | mall / shop / shangcheng / 商城 |

匹配方式为**子串包含**（在归一化后的小写串上）。

## 实测补丁记录

- [x] `order-list` 真实出参外层包裹 → `{"data":{"list":[...]}}`，空账号为 `{"data":{}}`
- [x] 金额单位确认 → **字符串元**（`realTotalAmount: "84.00"`），非分
- [x] `createTime` 真实格式 → `"YYYY-MM-DD HH:MM"`
- [x] 商品字段真实键名 → `orderProductList` / `productCode` / `productName` / `quantity` / `comboItemList`
- [x] `orderType` 真实枚举值 → 中文（如「堂食」）
- [x] **接入地址与官方文档不一致** → 实测 `https://mcp.mcd.cn`（根路径），文档写的 `/mcp` 返回 404
- [x] 工具总数 → 35 个，与官方 README 一致
- [ ] 分页机制（是否有 `pageNum`/`pageSize`/`hasMore`）→ 新账号无数据，**未能实测**
- [ ] `query-order` / `query-meals` / `calculate-price` 出参 → 同上，待有订单后补测

## 错误码

| code | 原因 | 处理 |
|---|---|---|
| 401 | Token 无效/过期/未提供 | 检查 Authorization 头，**不要**把 Token 写进仓库 |
| 429 | 超 600 次/分钟 | 降频；批量分析优先在本地做，只用 MCP 拉原始数据 |
| 404 | 路径错误（实测踩过） | 确认用的是根路径而非 `/mcp`；**404 ≠ Token 失效** |
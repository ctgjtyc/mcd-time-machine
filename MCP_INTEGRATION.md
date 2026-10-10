# MCP 接入与调用流程

本文档说明麦麦时光机如何使用麦当劳中国官方 MCP Server（Server 版本 v1.0.9，2026-09-10），
以及每个环节的业务价值。

## 接入信息

| 项 | 值 |
|---|---|
| **接入地址（2026-10-10 实测）** | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 申请 | [open.mcd.cn/mcp](https://open.mcd.cn/mcp) → 手机号登录 → 控制台 → 激活 |
| 限流 | 每 Token 每分钟 600 次请求，超限返回 429 |
| 错误码 | 401 Token 无效/过期；429 触发限流；404 路径错误 |
| 工具总数 | 35 个（实测与官方 README 一致） |

> ### ⚠️ 接入地址与官方文档不一致（实测发现）
>
> 官方 README 写的是 `https://mcp.mcd.cn/mcp`，**实测返回 404 Route Not Found**；
> 真正可用的是**根路径** `https://mcp.mcd.cn`。
>
> 这个坑很隐蔽：404 而不是 401，容易被误判成「Token 无用」。
> 探路实测记录：
>
> | 路径 | 响应 |
> |---|---|
> | `/mcp` · `/mcp/` · `/sse` · `/messages/` | 全部 404 |
> | `/` | 200，返回 `serverInfo: {name:"mcd-mcp", version:"1.0.0"}` |
>
> 握手响应（实测）：
>
> ```json
> {"jsonrpc":"2.0","id":1,"result":{
>   "capabilities":{"tools":{}},
>   "protocolVersion":"2024-11-05",
>   "serverInfo":{"name":"mcd-mcp","version":"1.0.0"}}}
> ```

脱敏配置示例见 [`mcp-config.example.json`](mcp-config.example.json)。

## 实测：`order-list` 真实出参 schema

以下结构于 2026-10-10 用真实 Token 实测取得（服务端自带字段说明）：

```
data.list[]                    订单列表
  .orderId / .orderType / .createTime / .beType / .beCode
  .storeCode / .storeName / .orderStatus
  .orderProductList[]          商品
    .productCode / .productName / .quantity / .comboItemList
  .realTotalAmount              总金额 —— ⚠️ 字符串，单位元（"84.00"）
```

外层包裹：`{"success":true,"code":200,"data":{"list":[...]}}`；
新账号无订单时返回 `{"data":{}}`（空对象，不抛异常）。

**由此修正的三处认知**（详见
[`skill/mcd-time-machine/references/tools.md`](skill/mcd-time-machine/references/tools.md)）：

1. 商品数组键名是 `orderProductList`，非 `items`/`goodsList`
2. 金额是**字符串元**，非「分」——按分处理会差 100 倍
3. 就餐方式键名是 `orderType`，值域含中文（「堂食」）
4. 列表页**无单价字段**，item 级金额需 `query-order` 补齐；
   总额一律以订单级 `realTotalAmount` 为准
5. **套餐的可靠标志是 `comboItemList` 非空**，优于按名称匹配

## 本 Skill 使用的 MCP Tool

| Tool | 用途 | 环节 |
|---|---|---|
| `now-time-info` | 获取当前完整时间 | 解析「上个月」「最近」「去年国庆」等相对表述 |
| `order-list` | 查询近期到店/外送历史订单 | **数据主干**，所有分析的唯一来源 |
| `query-order` | 查询单笔订单详情 | 复刻场景补全明细（商品、金额、状态） |
| `query-meal-detail` | 餐品详情 / 套餐组成 / 可替换项 | 历史含套餐时，查询当前能否更换组合 |
| `query-meals` | 查询当前门店在售菜单 | 比对历史菜品是否仍在售 |
| `calculate-price` | 含券价格计算 | 复刻前复核实付金额 |
| `query-nearby-stores` | 查询附近可用门店 | 原单为外送时重新定位门店 |

**明确不使用**的 Tool（超出本 Skill 边界）：`create-order`、`cancel-order`、
`draw-lottery`、`mall-create-order`、`party-order-create`、`auto-bind-coupons`。

原因：本 Skill 的核心价值是**读取与分析**，不涉及资金或权益变更。
所有涉及下单、取消、抽奖、兑换的动作，即使 MCP 提供了能力，也交由用户在对话中显式发起。

## 完整调用流程

### 场景一：消费时间轴

```
用户：看看我的麦麦时间轴
     │
     ├─1. now-time-info
     │     └ 得到当前时间，用于界定「最近 N 个月」的默认范围
     │
     ├─2. order-list
     │     └ 拉取历史订单流水（若返回分页信息，逐页拉取）
     │
     └─3. 本地分析（离线，不发起任何 MCP 调用）
           ├ build_timeline()    → 按月/渠道/门店/时段切片，深夜单占比、最长空窗
           ├ build_taste_profile() → 口味画像
           └ rank_moments()      → 挑出故事性最强的几单
```

**MCP 调用次数：2 次**。无论用户有 5 单还是 500 单，分析均在本地完成。

### 场景二：订单复刻

```
用户：把 2025-11-11 那顿再来一次
     │
     ├─1. order-list           → 定位目标订单（日期/月份匹配）
     │
     ├─2. query-order          → 补全该单明细
     │
     ├─3. query-meals          → 比对：历史菜品当前是否仍在售
     │
     ├─4. query-meal-detail    → 若含套餐：查当前可替换项
     │                          （MCP 1.0.5 起支持更换套餐内商品组合）
     │
     ├─5. calculate-price      → 用当前价格 + 可用券复核实付
     │
     └─6. 输出复刻方案 → 【等待用户确认】 → create-order
```

**关键约束**：第 6 步的 `create-order` 必须由用户显式确认后执行。
`replay_order()` 函数的返回值中带有 `disclaimer` 字段，
单元测试 `test_replay_never_auto_orders` 锁定了这一行为。

## 业务价值

### 为什么读 order-list 值得做

MCP 的 35 个工具里，`order-list` 和 `query-order` 是唯二能拿到「用户自己」
数据的工具。其余 33 个都是麦当劳侧的公共信息（菜单、营养、活动、门店）。

这意味着：基于这两个工具能做出**别的工具做不出来的东西**——
因为它回答的是「你」的问题，不是「麦当劳」的问题。

### 三个能力的差异化

| 能力 | 解决什么 | 依赖的 MCP 特性 |
|---|---|---|
| 时间轴 | 我的消费习惯是什么形状 | `order-list` 返回时间戳，可做时段/频次聚合 |
| 口味画像 | 我到底爱吃什么 | `order-list` 内嵌商品明细，含名称与分类 |
| 订单复刻 | 帮我找回某一顿 | `query-order` 精确定位 + `query-meal-detail` 的套餐可替换项（1.0.5+） |

第三项尤其依赖 MCP 的迭代：正是 v1.0.5 引入的「更换套餐内商品组合」能力，
让「复刻」从简单的照抄清单，变成了「原价不变的前提下微调」。

## 出参容错设计

MCP 出参字段名在不同版本、不同就餐方式（到店取餐 / 麦乐送 / 团餐 / 主题活动）
下并不完全一致。`src/mcd_time_machine/normalize.py` 因此不写死任何单一字段名，
而是维护候选别名表并在运行时按优先级探测。

完整的字段别名表、时间解析规则、金额单位判定、就餐方式归一化规则，
见 [`skill/mcd-time-machine/references/tools.md`](skill/mcd-time-machine/references/tools.md)。

这种设计带来两个好处：MCP 侧字段变更时引擎无需改动；
以及在拿到 Token 之前就能用 fixture 跑通全流程。

## 数据处理与隐私

- **全部分析在本地完成**，不上传任何第三方服务器
- 订单流水、积分、地址仅存在于本地进程内存与用户机器
- 对话回复中不复述完整配送地址与电话，需要时只提示「已用默认地址」
- 仓库、fixture、README 中不含任何真实订单号、Token 或个人信息
- fixture 数据为构造的样例，非真实用户订单

## 限流建议

MCP 限流为每 Token 每分钟 600 次请求。本 Skill 的设计已尽量减少调用：

1. 历史订单**一次性拉取**，分析全部本地完成
2. `build_timeline` / `build_taste_profile` / `rank_moments` 不触发任何 MCP 调用
3. 复刻场景最多 6 次调用，远低于限流阈值

批量分析时若遇 429，降低频率重试即可，不要改为并发请求。
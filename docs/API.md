# 接口

所有端点都在 `/api` 下，字段名保持 Java 源的驼峰风格。
除 `GET /api/map/config` 外**都要求登录**（session cookie，见[鉴权](#鉴权)）。

错误体与 Java 同构：`{code, message, details[], timestamp}`。

| 场景 | HTTP | code |
| --- | --- | --- |
| 参数校验失败 | 400 | `VALIDATION_ERROR` |
| 规划约束冲突、数据源不可用、未配置模型 | 400 | `TRIP_PLANNING_ERROR` |
| 未登录 | 401 | `UNAUTHORIZED` |
| 其他异常 | 500 | `INTERNAL_ERROR` |

---

## `POST /api/trips/plan` — 生成行程

```bash
curl -X POST http://localhost:8080/api/trips/plan \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{
    "city": "杭州",
    "startDate": "2026-09-30",
    "endDate": "2026-10-02",
    "budget": 3000,
    "preferences": ["美食", "自然风景"],
    "transportation": "公共交通",
    "accommodation": "舒适型酒店",
    "extraRequirements": "希望整体节奏轻松一些，对海鲜过敏"
  }'
```

**请求字段**：`city`、`startDate`、`endDate`（`yyyy-MM-dd`）、`budget`（`>=1`）、
`preferences`（非空数组）、`transportation`（可空，为空时 PlannerAgent 自行决定）、
`accommodation`、`extraRequirements`（交给 `RequirementAnalysisService` 解析成约束）。

另有三个 Java 没有的可空字段（见 [MIGRATION_NOTES.md](../MIGRATION_NOTES.md) 第七节）：
`hotelAnchor`（期望住宿位置，如「西湖」）、`dayHotelAnchors`（`{天数下标: 地名}`，只影响某一天）、
`hotelContinuity`（`strict` 表示整趟住同一家，留空即各天最优）。
三个都不填时，逐天酒店与通勤与 Java 完全一致。

日期若超出高德天气预报窗口（本机实测约 4 天），对应天会按 Java 同款逻辑逐天落
`source=rule-fallback` 并写 `高德未返回该日期天气，已使用规则天气补全`，这不是错误。

**响应体**是 Java 的 `TripPlan`：`city`、`startDate`、`endDate`、`days[]`（含 `hotel`、
`attractions[]`、`meals[]`、`routePlan`、`estimatedCost`、`optimizationNotes`）、
`weatherInfo[]`、`overallSuggestions`、`budget`、`planningNotes[]`、`agentTraces[]`、
`loopSummary`、`loopIterations[]`。

`loopSummary` / `loopIterations` 由 `LoopPlanningAgent` 填充，与 Java 一样在
`travel.loop.enabled=false` 时也非 null（此时 `loopSummary.enabled=false`、`roundsExecuted=0`、
`stopReason="loop-disabled"`、`loopIterations` 为空数组）。

Loop 是"生成 → 反思评分 → 批评 → 修正输入 → 再生成"的多轮闭环，每轮都复用上面那条
`create_agent` 取数链路：`agentTraces` 前面会插入逐轮的 `LoopPlanningAgent` /
`ReflectionAgent` / `LlmCriticAgent` / `LoopDecisionExecutor` 轨迹，`planningNotes` 末尾追加三句
（`LoopPlanningAgent 已完成生成-评估-反思-修正闭环`、`LoopPlanningAgent 选定第 N 轮作为当前最优方案`、
以及本次的停止原因）。四个调整动作字符串照搬 Java：`REDUCE_BUDGET` / `COMPRESS_ROUTE` /
`REPLACE_OUTDOOR_WITH_INDOOR` / `UPGRADE_EXPERIENCE` / `STOP`。批评阶段 LLM 失败时按 Java
回退规则批评，并写 `LLM 批评阶段失败，已回退到规则批评`。

---

## `POST /api/trips/adjust` — 用自然语言改计划

对应 Java 的 `TripPlanController.adjustTrip`：把自然语言改计划要求解析成新的请求副本，
再用这份副本重新走一遍上面的规划链路（含 Loop 闭环），不另写一份规划逻辑。

```bash
curl -X POST http://localhost:8080/api/trips/adjust \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{
    "originalRequest": {
      "city": "杭州",
      "startDate": "2026-09-30",
      "endDate": "2026-10-02",
      "budget": 3000,
      "preferences": ["美食", "自然风景"],
      "transportation": "公共交通",
      "accommodation": "舒适型酒店",
      "extraRequirements": "希望整体节奏轻松一些"
    },
    "adjustmentPrompt": "预算改成5000，住经济型酒店，不要自然风景"
  }'
```

**请求字段**：`originalRequest`（`@NotNull`，结构同上）、`adjustmentPrompt`（`@NotBlank`）。

**响应体**是 Java 的 `TripAdjustmentResponse`，只有三个字段：`tripPlan`（重新规划出的完整
`TripPlan`，字段与 `/plan` 相同）、`updatedRequest`（解析后的新请求副本）、`appliedChanges[]`。
`adjustmentSource` 在 Java 里不是响应字段，Python 同样不输出它，它只以
`tripPlan.planningNotes` 里的"改计划解析来源：llm|rule|rule-fallback"一句体现。

`planningNotes` 的追加顺序照搬 Java：先由规划链路写满原有备注，再依次追加
`改计划解析来源：X`、`本次改计划要求：<adjustmentPrompt 原文>`，最后 `addAll(appliedChanges)`，
因此 `appliedChanges` 的每条文案会同时出现在响应体两处。

`adjustmentPrompt` 的解析有两条路径，与 Java 一致：模型可用时先按 system prompt 要求返回结构化 JSON
（`budget`/`transportation`/`accommodation`/`preferencesToAdd`/`preferencesToRemove`/`extraRequirements`），
备注以 `LLM 已…` 开头；JSON 解析失败时按 Java 回退规则解析，`appliedChanges` 末尾追加
`LLM 解析失败，已自动回退到规则解析`。规则路径识别预算数字（3-6 位）、自驾/打车/公共交通/智能推荐、
六档住宿、五类偏好的增加与移除（"不要美食""不想去博物馆"），以及"省一点""路线紧凑""下雨""品质一点"
这类会转成统一策略动作的表述。两条路径各自无命中时的兜底文案不同，照搬 Java：规则路径写
"已保留原始规划条件，并记录新的调整要求"，LLM 路径写"LLM 已保留原始规划条件，并记录新的调整要求"。
剩余的非指令性子句（如"对海鲜过敏"）按 Java 合并进 `extraRequirements`。

与 Java 的一处已注明差异：`TripAdjustmentRequest.originalRequest` 在 Java 侧**没有 `@Valid`**，
内层 `TripPlanRequest` 的约束不会被触发；FastAPI 会级联校验并把这类输入挡成 400 `VALIDATION_ERROR`
（details 形如 `originalRequest.city: must not be blank`），见 `MIGRATION_NOTES.md` 第三节第 20 条。

---

## `POST /api/trips/ask` — 就当前方案追问

**移植基线之外的能力扩展**（Java 无对应物）：方案生成后，前端把上一次 `/plan` 的响应体与原始请求
一起回传，agent 用只读工具回答关于这份方案的追问；判出"其实想改"时也只产出一句话，
**要用户点确认才发给 `/adjust`**，agent 自己没有任何写权限。

```bash
curl -X POST http://localhost:8080/api/trips/ask \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"question":"第二天住得离西湖近一点","tripPlan":{...},"request":{...}}'
# {"answer":"...","intent":"need_change","adjustmentPrompt":"第二天住得离西湖近一点",
#  "anchorHint":"西湖","anchorDay":1,"traces":[{"agentName":"get_day_plan",...}]}
```

1. 六个工具：`get_plan_overview` / `get_day_plan` / `get_attraction_detail` 只读方案，
   `search_attractions` / `query_weather` / `recommend_hotels` 复用规划那三个数据 Agent。
2. 服务端不存方案，所以 `tripPlan` 与 `request` 都要回传（方案里没有偏好与住宿档位这些取数参数）。
3. 需要可用模型：没有模型直接 400「追问能力需要可用模型」，与 `/plan` 的纯规则路径有意不同。
4. 联网失败不整体失败：记一条 `status=failed` 的留痕，模型改用方案内数据作答。
5. 模型最后一轮必须输出固定 JSON（`answer` 或 `need_change` 两种）；不是合法 JSON、意图值不认识、
   说要改却没给那句话，三种情况**一律回落成 `answer`** 并记一条 `status=fallback`。理由是代价不对称：
   误判成"要改"白烧一次 40~70 秒的重规划，误判成"只是问"用户多点一次编辑就行。
6. `anchorHint` / `anchorDay` 是用户原话里的地名与天数，属于意图不属于事实，因此不受"事实必须来自工具"
   那条护栏约束；前端拿到它填进请求，再随确认一起交给 `/adjust`。
7. 设计、护栏与逐题验证记录在 [`ASK_AGENT_DESIGN.md`](ASK_AGENT_DESIGN.md)，
   改方案那一轮的设计在 [`ADJUST_AGENT_DESIGN.md`](ADJUST_AGENT_DESIGN.md)。

## `POST /api/trips/ask/stream` — 同上，SSE 版

请求体与响应语义和 `/ask` 一字不差，只是边跑边推。**答案不逐 token 流**：模型最后一轮输出的是
规则 9 约定的 JSON 信封（`{"intent":"answer","answer":"…"}`），逐 token 推出去用户会先看到脚手架。
而工具调用发生在答案之前，是天然的进度条，所以**那部分真流**；答案整条推，前端拿到之后本地做
打字机效果（"假流式"）。

鉴权失败返回的是普通的 JSON 401，而不是"流建好了再推一条 error 事件"——
依赖在进入 handler 之前跑，所以前端不必为流式那条路另写一套鉴权失败的解析。

---

## 鉴权

| 端点 | 说明 |
| --- | --- |
| `POST /api/auth/register` | 注册**成功后直接登录**（种 cookie），省用户注册完再输一遍 |
| `POST /api/auth/login` | 登录 |
| `POST /api/auth/logout` | 删掉服务端那一行会话，再让浏览器把 cookie 丢了。没登录时调也回 200——登出是无副作用的幂等操作 |
| `GET /api/auth/me` | 前端开页面先打这个，决定显示登录页还是规划页 |

会话 cookie 是 `httponly`（JS 读不到，XSS 偷不走）、`samesite=lax`（同源够用，顺带挡掉外站发起的
跨站 POST）。**`secure` 故意没开**：本机是 http，开了浏览器直接不收这个 cookie，登录会静默失效。
上线换 https 时必须加 `secure=True`（见 [`ASK_LONGTERM_MEMORY.md`](ASK_LONGTERM_MEMORY.md) §2.1.4）。

`/plan`、`/adjust`、`/ask` 三条要求登录，判据是"会产生成本、会读写数据"，不是"是个 API"——
所以 `/api/map/config` 没挡，它吐的是本来就要发到浏览器里的高德前端 JS key（§2.1.6）。

---

## `GET /api/map/config` — 前端地图配置

对应 Java 的 `MapConfigController`，给前端页面加载高德 JS SDK 用。

```bash
curl http://localhost:8080/api/map/config
# {"enabled":false,"jsApiKey":"","securityJsCode":null,"provider":"amap-jsapi",
#  "message":"尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示"}
```

响应体是 Java 的 `MapConfigResponse` 五个字段：`enabled`（`AMAP_WEB_JS_KEY` 非空白才为 `true`）、
`jsApiKey`（`enabled=true` 时原样透传该 key，否则空串）、`securityJsCode`（无条件透传
`AMAP_SECURITY_JS_CODE`，未配置为 `null`）、`provider`（恒为 `amap-jsapi`）、`message`
（`高德地图前端配置已就绪` / 上面那句占位提示）。

这是全项目唯一把凭据写进响应体的端点，与 Java 一致；两个值只从环境变量或 `.env` 读，
不进代码、日志与文档。`enabled=false` 时前端地图区渲染"真实地图待开启"文字占位、不加载高德 SDK，
`.env` 里填上 `AMAP_WEB_JS_KEY` 与 `AMAP_SECURITY_JS_CODE`（必须是「Web端(JS API)」类型，
与后端那把「Web服务」key 不通用）之后才会出真底图、Marker 与当日路线 Polyline。

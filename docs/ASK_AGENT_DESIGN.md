# 方案 A：行程追问 Agent（只读问答）设计

状态：已实现并验证（见文末第 11 节）。两处与本文原稿不同，已在正文就地改准。
落地位置：分支 `feature/ask-agent`，`main` 上的 Java 等价移植基线未动。

---

## 1. 目标与非目标

目标：方案生成之后，用户能就**这份方案**追问，系统回答，且每个数字都有出处。

非目标（这一轮明确不做）：

- 不改方案：不产生新 `TripPlan`、不动 `days`、不动预算。
- 不做会话记忆：`thread_id` 只进请求体占位，不接 checkpointer。
- 不做长期记忆、不做登录鉴权。
- 不做 trace 落库：A 只把 trace 随响应带回去。
- 不做流式输出：一次请求一次完整响应。

---

## 2. 接口契约

`POST /api/trips/ask`

请求：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `question` | string | 是 | 用户原话，空白直接 400 |
| `trip_plan` | TripPlan | 是 | 前端把上一次 `/plan` 的响应体原样回传 |
| `request` | TripPlanRequest | 是 | 生成这份方案时用的原始请求，同样由前端回传。方案里没有偏好、住宿档位这些取数参数，追问要联网就得靠它 |
| `thread_id` | string | 否 | A 阶段收下不用，为会话那一档预留 |

响应（字段名对外驼峰，沿用 `CamelModel`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `answer` | string | 中文回答 |
| `intent` | string | A 恒为 `"answer"`；`"need_change"` 留给 B |
| `traces` | AgentTrace[] | 本次每一步的留痕，结构复用现有 `AgentTrace` |

错误沿用现有三分支：`VALIDATION_ERROR`（400，字段校验）、`TRIP_PLANNING_ERROR`（400，无可用模型 / 工具全失败）、`INTERNAL_ERROR`（500）。

---

## 3. 新增与改动的文件

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `models/ask_request.py` | 新增 | 上面那个请求体，校验 `question` 非空、`trip_plan` 非空 |
| `models/ask_response.py` | 新增 | 上面那个响应体 |
| `agent/ask_context.py` | 新增 | 请求级上下文（`trip_plan` + `question`），contextvar 存取，照搬 `agent/planning_context.py` 的写法 |
| `agent/ask_tools.py` | 新增 | 三个读方案工具 + 把现有三个取数工具挂进来 |
| `agent/ask_agent.py` | 新增 | `create_agent` 装配、跑圈、收 trace |
| `api/ask_controller.py` | 新增 | `POST /api/trips/ask` |
| `main.py` | 改动 | `include_router(ask_router)`，位置在两个现有 router 之后、`StaticFiles` 挂载之前 |
| `config/container.py` | 改动 | 装配 `AskAgent`，暴露 `get_ask_agent()` |
| `static/index.html` | 改动 | 结果页加提问框 + 答案区；**这一处破坏"与 Java 字节级一致"，单独一个提交** |

依赖零新增：`langchain 1.4.2`、`langgraph 1.2.11`、`fastapi 0.141.1`、`pydantic 2.13.5` 都已装。

---

## 4. 工具：从 3 个变 6 个

现有 3 个（`agent/data_tools.py`，零参数、参数取自请求上下文）：`search_attractions`、`query_weather`、`recommend_hotels`。

新增 3 个，全部是纯函数，不联网、不调模型：

| 工具 | 参数 | 输出物件 |
| --- | --- | --- |
| `get_plan_overview` | 无 | 一行城市/日期/天数/总花费/结余 + 每天一行"第N天：X个景点（点名）、Y分钟通勤/Z公里、预估花费、天气" |
| `get_day_plan` | `day_index`（从 0 起，可省） | 那天完整安排：景点名+票价+时长+地址、餐、酒店、路线摘要与总通勤、当天预估花费、优化备注 |
| `get_attraction_detail` | `attraction_name`（可省） | 该景点的名称、类别、地址、票价、游览时长、有无坐标、简介 |

参数约定与本文原稿不同，按实现改准：**读方案的三个允许带"定位参数"，但参数只用来在已生成的方案内部找东西**——
`day_index` 越界就返回"方案里没有第 N 天，共 3 天"，景点名对不上就返回"方案里没有叫 X 的景点"并列出方案内清单，
两者都**不会外发任何请求**，所以模型编一个名字也造不出事实。省略参数时 `get_day_plan` 按游标逐天往下走。
**联网的三个保持零参数**：它们的查询条件是城市/日期/预算/住宿，这些值让模型填就会编。

`get_budget` 不单列——预算与结余已经在 `get_plan_overview` 那一行里，多一个工具只会让模型多绕一圈。

失败处理：工具内部异常不外抛——记一条 `status=failed` 的留痕，并把"某某失败：原因"当返回值交给模型，
由它改用方案内已有数据作答并按护栏第 5 条承认查不到。**联网失败不把整次追问打成 400**，
因为用户问的多半是方案内容，一条外部数据源不可用不该让回答整体失败。

---

## 5. 编排与提示词护栏

`create_agent(model, tools=[6 个], system_prompt=...)`，循环由框架驱动（模型出 `tool_call` → 工具执行 → 结果回灌 → 模型再出话或再调工具）。

提示词里写死五条：

1. 所有事实只能来自工具返回值，禁止使用你自己的知识补景点、票价、时长、坐标、天气。
2. 回答里出现的每个数字都必须能在本轮某次工具返回值里找到原文。
3. 按问题需要取数，两个方向都不许偏：数据不够就不许硬答（该调 `get_day_plan` 拿细节就不能只看 `get_plan_overview`）；也不许多取（一次问一天就别把三天都取一遍，方案里已有的天气就别再联网去查）。
4. 用户要求"改方案/换酒店/调预算"时，不要回答具体方案，输出 `intent=need_change` 所需的那句话即可（A 阶段仍返回 `answer`，只加一句"这需要重新生成方案，请使用改计划功能"）。
5. 工具没给到的信息，直接说没查到，不许推测。

无可用模型时（`is_available()` 为假）：`/api/trips/ask` 返回 400 `TRIP_PLANNING_ERROR`，文案"追问能力需要可用模型"。A **不做纯规则兜底**——没有模型就没有对话，这是与 `/plan` 的有意差别。

---

## 6. 留痕（第 1 档）

每次工具调用记一条 `AgentTrace`：

| 字段 | 填什么 |
| --- | --- |
| `agentName` | 被调的工具名，如 `get_day_plan` |
| `action` | 这一步在做什么，如"取第2天安排" |
| `inputSummary` | 上下文里取到的定位信息，如"day_index=1" |
| `outputSummary` | 工具返回的那一小块文本（截断到 300 字） |
| `status` | `success` / `failed` |

最后再补一条 `agentName=AskAgent`、`action=生成回答`，`outputSummary` 写答案前 200 字。
前端复用现有"Agent轨迹"区块（`#agent-trace-list` + `renderAgentTraces`），换数据源即可，不新增组件。

---

## 7. 前端改动（最小集）

1. 结果页底部加一块"追问这份方案"：一个输入框 + 一个按钮。
2. 点击时把 `currentTripData` 整个回传 + `question`，POST 到 `/api/trips/ask`。
3. 答案渲染进新加的答案区；把返回的 `traces` 灌进现有的 `renderAgentTraces` 逻辑，替换掉当前那次规划的轨迹显示（或并排加一个"追问轨迹"折叠块，二选一，倾向前者，改动更小）。
4. 请求进行中禁用按钮，失败沿用现有 `error-toast`。

---

## 8. 验收判据

回归：`.venv/bin/python -m pytest -q` 仍 55 passed 起跳，新增用例后全绿。

新增单测（`tests/test_ask_agent.py`）至少 5 条：

1. 模型不可用 → 400 且文案为"追问能力需要可用模型"。
2. `question` 空白 → 400 `VALIDATION_ERROR`，details 为 `question: must not be blank`。
3. 用假模型固定返回"调 `get_plan_overview` 再作答" → 断言只产生了 1 条工具 trace 且 `agentName=get_plan_overview`。
4. 假模型要求换天 → 断言 `get_day_plan` 被调两次、`day_index` 依次为 0 和 1。
5. 联网工具抛异常时 → 响应仍是 200，那条 trace `status=failed`，回答改用方案内数据并承认查不到。
6. 景点名对不上方案时 → 该条 trace `status=not_found`，返回值里带方案内景点清单，且不发起任何外部请求。
7. 空请求体 → 400 `VALIDATION_ERROR`，details 三条齐全（`question: must not be blank`、`tripPlan: must not be null`、`request: must not be null`）。

真实跑（`SERVER_PORT=8123`，模型开启）10 题，判据是 ≥8 题答案中的数字能在方案里对上：

| # | 问题 | 该走哪个工具 |
| --- | --- | --- |
| 1 | 这趟一共花多少钱 | `get_plan_overview` |
| 2 | 第二天为什么这么赶 | `get_day_plan` |
| 3 | 第三天安排了什么 | `get_day_plan` |
| 4 | 西溪湿地门票多少 | `get_attraction_detail` |
| 5 | 住的是哪家酒店、多少钱 | `get_day_plan` |
| 6 | 第一天天气怎么样 | `get_day_plan`（方案里已含天气） |
| 7 | 哪天通勤时间最长 | `get_plan_overview` |
| 8 | 预算还剩多少 | `get_plan_overview` |
| 9 | 灵隐寺附近有什么吃的 | `get_attraction_detail` + `search_attractions`（允许联网） |
| 10 | 把第二天改松一点 | 不查数据，命中护栏第 4 条 |

安全：全仓与日志按 `.env` 真实值比对命中 0；`outputSummary` 里不得出现高德完整 URL 或 key。

---

## 9. 已知风险

1. **高德配额**：第 9 题这类会真打高德，仍可能撞 `CUQPS_HAS_EXCEEDED_THE_LIMIT`。A 阶段不加重试（与 Java 一致），撞了就按第 6 节记 `status=failed` 并回答"这项暂时查不到"。
2. **token 体积**：`trip_plan` 实测一次响应 18846 字节，但工具是从上下文里按天取，不进提示词的只有整份 JSON——前提是**不要把 `trip_plan` 直接塞进 system prompt**，只给工具清单。这条是实现时最容易图省事踩的坑。
3. **前端一致性**：改 `index.html` 会让它不再与 Java 源文件字节级一致，`MIGRATION_NOTES.md` 第二节那一行必须同步改掉，否则文档说谎。
4. **A 依赖模型**：与 `/plan` 不同，A 没有降级路径，模型不可用即不可用。

---

## 10. 实施顺序

1. `git checkout -b feature/ask-agent`。
2. 后端：契约两个 model → `ask_context` → `ask_tools` → `ask_agent` → 端点 → 容器装配。
3. 单测 5 条 + 回归。
4. 前端一块。
5. 真实跑 10 题，把每题的 traces 贴出来核对。
6. 更新 `MIGRATION_NOTES.md`（新增"与 Java 不等价的能力扩展"一节，与移植差异清单分开）和 `README.md`。

---

## 11. 实现结果（2026-10-06）

1. 分支 `feature/ask-agent`；新增 8 个文件（3 个 model/context/tools/agent/controller + 1 个测试 + 本文档），改动 5 个（`main.py`、`container.py`、两个 `__init__.py`、`index.html`）。
2. 单测：`.venv/bin/python -m pytest -q` → **62 passed**（原 55 + 新 7），老用例一条未改。
   其中第 7 条是启动复查时抓出来的真缺陷：`AskRequest` 少写 `validate_default=True`，
   导致空请求体绕过字段校验、错误地以 `TRIP_PLANNING_ERROR`「回传的方案里没有可追问的内容」收场；
   补上后空体正常挡成 400 `VALIDATION_ERROR` 三条 details。
3. 真实追问 10/10 全部 200，单题耗时 1.3~3.2 秒。取数路径逐题可核对，摘三条：
   - "这趟一共花多少钱" → 只调 `get_plan_overview`，答"总花费 2937 元，预算 3000 元，结余 63 元"，与响应体 `budget` 字段一致。
   - "西溪湿地门票多少钱" → 第一次 `get_attraction_detail`（`not_found`，返回方案内景点清单）→ 第二次改名命中 → 答"60 元、建议停留 180 分钟"。**没有一次外发高德请求**。
   - "把第二天改松一点" → **未调任何工具**，直接命中护栏第 4 条，回"这需要重新生成方案，请使用页面上的「编辑行程」或改计划功能"。
   - "灵隐寺附近有什么吃的" → 只读方案，答"方案里只列了三餐的通用名称与预估费用，具体店铺没有，我也没查到"——按护栏第 5 条承认查不到，没有编造。
4. 前端：headless Chrome 加载改过的页面，用页面自带的 `renderResult` 渲染那份真实方案后点「追问这份方案」，
   `POST /api/trips/ask` 返回 200，答案渲染进 `#ask-answer`，三条留痕渲染进现有"智能体执行轨迹"区块。
   截图 `_verify/08-ask-agent-ui.png`，DOM 证据 `_verify/ask-ui-dom.json`。页面无脚本报错（只有一条来自高德 SDK 的 Canvas2D 性能提示）。
5. 遗留（都是有意留下的，不是漏）：
   - 追问的留痕会**覆盖**当次规划的 18 条轨迹显示，要回看需重新生成方案——这是"改动最小"的代价，第 2 档落库之后可以做成两份并存。
   - `thread_id` 收下不用，会话那一档再启用。
   - 无流式输出；单题 1.3~3.2 秒，暂时不必需。

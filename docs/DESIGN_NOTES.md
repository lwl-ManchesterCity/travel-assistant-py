# 设计说明：行为口径、取舍与验收记录

这份文档记三件事：主链路上每个"为什么这么定"、与常规写法不同的地方（以及不这样会怎样）、
每条口径的验证证据。分四块：

1. **行为口径与取舍**（23 条）：取数链路、编排、异常、对象比较、数值与日期、参数校验、配置、
   静态资源等处逐条的真实行为与理由。
2. **验证记录**：跑过的用例、真实链路实测数据、浏览器验收证据。
3. **能力扩展**：主链路之外的追问 agent，以及它带来的前端改动。
4. **主动偏离**：主链路定稿之后刻意改掉的行为，逐条列出。

文中所有路径、函数名、字段名、错误文案、阈值与数字都取自当前代码；两者不符时以代码为准。

---

## 一、行为口径与取舍

### 1. 取数链路两条路径：有模型走 create_agent，无模型退回固定顺序

- `is_available()` 为真时走 `create_agent`，由模型自选工具；为假时
  `PlanningDataAgent._collect_by_fixed_order` 按
  `search_attractions -> query_weather -> recommend_hotels` 顺序逐个 `invoke({})`，
  任一源报错立即 `break`，后面的源不再发请求，异常仍由 `data_tools.raise_planning_errors`
  冒泡成同一句 400 文案。
- 早先版本只有一条路：没有可用模型时直接抛"未配置可用模型"，该分支已删除，改成两条路都能出完整行程。
  LLM 不参与时，需求解析与文案都有规则兜底（`analysisSource=rule`、`结果文案来源：规则文案`）。
- 两条路复用同一批 LangChain 工具对象：固定顺序那条也是逐个 `invoke` 这些工具（工具内部仍是那三个
  agent），异常先暂存进 `context.errors`、取数收尾时再抛。因此对外可见的提示文案、短路位置
  （第一个失败点之后的源不再被调用）两条路一致，只有"抛出时机"比当场抛出晚了几行代码。
- 判据：`is_available()` 的四个条件（`enabled` + 非空 `api-key` + `base-url` + `model`）任一不满足
  即走固定顺序；异常暂存的细节见第 3 条。

### 2. 取数编排：默认让模型自主选工具

- `create_agent(model, tools=[search_attractions, query_weather, recommend_hotels], system_prompt=...)`，
  调用顺序和轮次由模型决定。没有可用模型时不走这条路，退回上面那三个工具的固定顺序（见第 1 条）。
- 为保证事实仍来自数据源而不是模型编造：三个工具都是**零参数**，查询条件从请求级 `PlanningContext`
  （contextvar）里取，取到的结构化结果写回同一个 context；system prompt 明确禁止编造
  景点/酒店/价格/坐标/天气，只允许复述"数据已就绪"。PlannerAgent 只读 context 里的结构化对象，
  不读模型的自然语言。
- 影响：`agentTraces` 与 `planningNotes` 的内容不受调用顺序影响（builder 按固定的 4 条 trace 顺序生成），
  但一次请求消耗的模型轮数与耗时由模型决定，不固定。
- 已验证：contextvar 在 LangGraph 工具节点（含三个 tool_call 并发执行）中可见，取数结果非空、errors 为空。
- Loop 每一轮都复用这同一个入口（`LoopPlanningAgent` 每轮调 `planner_input_builder.build(workingRequest)`
  再 `planner_agent.build_plan(...)`），没有把取数改回固定顺序；代价是每轮都重新取一次数，
  单轮耗时线性放大。

### 3. 工具里抛的异常不会自己冒泡

LangChain 的 ToolNode 会把工具异常转成 `ToolMessage(status="error")` 交给模型，异常本身传不出来。
所以 `data_tools.py` 里每个工具用 `try/except` 把异常暂存进 `context.errors`，builder 在模型跑完后调
`raise_planning_errors(context)` 统一重抛，用户看到的还是那句 400 文案
（如"天气信息暂时不可用"），不是模型的自由发挥。

### 4. PlannerAgent 的第二个依赖

- `PlannerAgent(route_agent, daily_plan_evaluator, llm_narration_service)`，`reflect_daily_plan()`
  落在 `evaluator/daily_plan_evaluator.py`。
- `agent/reflection_agent.py` 的 `reflect_daily_plan()` 只是回指 evaluator 的薄封装，不再重复一份
  服务于每日反思的私有方法。
- 保持不变的：输出里的文案与 trace 仍写 `ReflectionAgent` —— `planningNotes` 追加
  "ReflectionAgent 已对每日方案完成预算、路线、天气反思评估"，`agentTraces` 里
  `agentName="ReflectionAgent"`、action="反思第N天候选方案"、status="accepted|need_revision"。
- `config/container.py` 里 `ReflectionAgent(daily_plan_evaluator)` 与
  `PlannerAgent(..., daily_plan_evaluator, ...)` 共用同一个 evaluator 实例。

### 5. 响应体是 TripPlan，loop 两字段真实填充

- `TripPlan.loopSummary` / `loopIterations` 由 `LoopPlanningAgent.appendLoopResult` 填充，
  **loop 关闭时也会填充**：`plan()` 的第一分支调用
  `appendLoopResult(tripPlan, [], [], 1, "loop-disabled")`，
  于是 `loopSummary = {enabled:false, roundsExecuted:0, selectedRound:1, stopReason:"loop-disabled"}`、
  `loopIterations` 为空数组，并且 `planningNotes` 照样追加"LoopPlanningAgent 已完成生成-评估-反思-修正闭环"等三句。
- 两个字段类型已从第一轮的 `dict` 占位改成 `LoopExecutionSummary` / `LoopIterationTrace`。
- 更正第一轮记录：本文曾把 loop-disabled 的 `stopReason` 写成"规划循环已关闭，使用单轮 PlannerAgent
  生成结果"，代码里的实际值是 `"loop-disabled"`，按代码实现。

### 6. 四个 Amap client 共用一个 HTTP 层

- 候选 URL（http/https 互转）、URL 编码、curl fallback、`status` 校验统一放 `clients/amap_http.py`，
  各 client 保留自己原有的错误前缀与日志文案：
  景点 `log_scope=高德 POI` / 错误前缀 `高德景点检索`（curl 分支文案 `curl fallback 未获取到有效 POI 响应：`）；
  酒店 `高德酒店检索`（curl 分支 `高德酒店检索 curl fallback 未获取到有效响应：`）；
  天气按动作分两条 `高德城市编码解析` / `高德天气查询`；
  路线 `log_scope=高德` / 状态错误 `高德路线规划调用失败：` / `高德路线规划请求异常：`。
- 保留的行为：URL 参数编码规则（`,` 不转义；布尔按小写 `"true"`/`"false"`）、
  `User-Agent: travel-assistant/1.0`、候选 URL 顺序、curl 兜底开关
  `AMAP_CURL_FALLBACK_ENABLED`、`/usr/bin/curl -s --connect-timeout 10 --max-time 20`、限流错误码判定后切规则路线。

### 7. 日志与异常文案里的 api-key 会被打码

- `clients/amap_http.py::_redact()` 把所有对外可见字符串里的 `key=` 值替换成 `key=***`，
  失败日志与异常 message 都不会带出真实 key。
- 另一层：`httpx` 在 INFO 级会自己打印完整请求 URL（含 `key=<真实 key>`）。实测本机
  `logging.basicConfig(level=INFO)` 起服务时，一次请求会在日志里落 11 条带真实 key 的 URL，
  因此 `main.py` 把 `httpx` / `httpcore` 两个 logger 压到 WARNING。
- 这是有意为之：凭据不进日志、不进提交信息。若要为了逐字复现旧文案而撤回本条，需要显式决定。

### 8. 对象身份比较：`is not` 与 `_remove_by_identity()`

- `selectHotel` / `refineDailyPlan` 里的 `cheaperHotel != workingHotel` 用 `is not`：
  pydantic 模型是值相等，用 `!=` 会把"同价同名的另一家酒店"判成同一家，从而跳过本该发生的替换。
- 列表删除统一走 `_remove_by_identity()`（按 `id()` 删）：Python 的 `list.remove` 按 `__eq__` 删，
  会把同值的另一个对象当成目标。
- 同类：当天景点列表里的原地替换保留下标赋值（`list[index] = replacement`），不重建列表。

### 9. 数值与日期语义

- 取整统一用 `utils.jround`，即 `floor(x + 0.5)`（负数方向与 Python 内置 `round` 不同，
  所以要显式算，不能用内置）；一位小数用 `utils.round1`。
- `请求预算 / 天数` 是整数除法，用 `//`。
- 日期用 `datetime.date` + `isoformat()`（`yyyy-MM-dd`）。
- haversine 半径 `6371.0`，换算用 `math.radians`。

### 10. LLM prompt 里的列表渲染

- prompt 里的列表要渲染成 `[美食, 自然风景]`（元素之间逗号加空格、不带引号）。
  Python 直接 f-string 会变成 `['美食', '自然风景']`（带引号），会改变模型输入。
- 处理：`utils.render_list()` 拼出 `[a, b]` 格式，用在 `RequirementAnalysisService` 与
  `LlmNarrationService` 的 prompt 构造处（system prompt 文本块逐字固定）。
- `PlanningDataAgent` 的 user prompt 是新增的，不受本条约束。

### 11. 参数校验与错误响应

| 项 | 本项目行为 |
| --- | --- |
| 校验触发 | `TripPlanRequest` 的 `field_validator` + `validate_default=True`（请求体缺字段按 null 处理，同样报错） |
| 错误体 | `ApiErrorResponse{code,message,details,timestamp}`，字段名保持驼峰 |
| 校验失败 details | `字段 + ": " + 文案`；pydantic 的 `"Value error, "` 前缀被剥掉；`loc` 去掉 `body` 前缀并把蛇形名转驼峰，保证出现 `startDate: ...` 而不是 `start_date: ...` |
| 一个字段多条约束同时违反 | pydantic 每字段只报第一条 |
| 请求体缺失/JSON 语法错 | FastAPI 自己返回 422/400，不是本项目的错误体 |
| 类型不匹配（如 budget 传字符串） | 归入校验失败 -> 400 `VALIDATION_ERROR` |
| `TripPlanningException` | 400 `TRIP_PLANNING_ERROR`；`details` **按异常类型分流**：高德限流/配额类失败（`is_quota_exhausted`）回 `["地图数据接口调用过于频繁，请稍后重试"]`，其余仍是 `["请调整输入条件后重试"]`。限流时把错归到用户输入上会把人带偏，见第四节 |
| 时间戳 | `datetime.now()`，输出微秒精度的无时区 ISO 串 |

### 12. 启动日志

- 打一条 `python.version=<platform.python_version()>`，其余字段（各 provider 名、`amap.baseUrl`、
  `amap.mockResponse`、`amap.curlFallbackEnabled`、`amap.webJsEnabled`）也都打出来。
- 只打印开关和地址，不打印 key。

### 13. CORS

- 用全局 `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`，
  未开 `allow_credentials`（与 `Access-Control-Allow-Origin: *` 的语义一致）。
- 作用域是整个 app（当前 app 的对外端点本来就同属规划那一个 controller）。

### 14. 目录结构上的偏离（除已注明的合并/新增）

- 知识库落在 `clients/knowledge_base.py` 而不是 `service/`：它唯一的调用方是
  `InMemoryAttractionClient`，留在 `service/` 会让 `clients` 反向依赖 `service`，装配时形成循环导入。
- `config/__init__.py` 不导出 `container` / `runtime_logger`，同样是为了避开
  `clients -> config -> container -> agent -> clients` 的循环导入。

### 15. 配置项默认值取"生效值"

默认值按配置文件里的生效值定，不按代码里的字段初值（两者不一致时以配置为准）：

| 配置 | 默认值 | 环境变量 |
| --- | --- | --- |
| `providers.{attraction,weather,hotel,route}` | `amap` | `TRAVEL_PROVIDERS_*` |
| `amap.baseUrl` | `http://restapi.amap.com` | `AMAP_BASE_URL` |
| `amap.mockResponse` | `false` | `AMAP_MOCK_RESPONSE` |
| `llm.enabled` | `False` | `TRAVEL_LLM_ENABLED` |
| `amap.apiKey` / `llm.apiKey` | `""` | `AMAP_API_KEY` / `TRAVEL_LLM_API_KEY`（回落 `DASHSCOPE_API_KEY`） |

生效值与原字段初值不一致的三处，按生效值取：`providers.*` 取 `amap`（字段初值是 `mock`）、
`amap.baseUrl` 取 `http://restapi.amap.com`（字段初值是 `https://restapi.amap.com`）、
`amap.mockResponse` 取 `false`（字段初值是 `true`）。
其余项（`route-strategy`、`route-extensions`、`curl-fallback-enabled`、`poi-page-size`、`city-limit`、
`web-js-key`、`security-js-code`、`server.port`）默认值与配置一致。
key 只从环境变量/`.env` 读，`resolved_api_key()` 复刻两级回退。
`web-js-key`/`security-js-code` 有消费方：`api/map_controller.py` 把它们原样写进
`GET /api/map/config` 的响应体，`config/runtime_logger.py` 只取 `web-js-key` 的非空布尔；
后端取数链路（四个 Amap client）仍然不读这两个键。

兜底开关的两层语义：`providers.<source> == "amap"`（忽略大小写）选择高德实现，否则用规则/本地实现；
选中高德后 `amap.mockResponse=true` 直接走该 client 内置的兜底实现；未配 `AMAP_API_KEY` 抛
"已选择高德景点 provider，但尚未配置 AMAP_API_KEY"（其余三个源文案见各自文件）。

### 16. 批评提示词里的 JSON 排版

- 用 `json.dumps(..., indent=2, ensure_ascii=False)`：缩进两个空格、冒号前没有空格。
- 影响：只影响送给模型的文本外观，不影响批评动作判定；中文保留原样，null 字段照常出现。
  `tests/test_llm_critic_agent.py` 里的提示词断言按这个排版写。

### 17. 从 JSON 节点取字符串的取值语义

- `llm_critic_agent.py` 的 `_path_as_text()` / `_node_as_text()` / `_read_string_list()` 约定：
  字段缺失返回默认值；JSON `null` 按字符串 `"null"` 处理（不是回落默认值）；数字取字面量；
  对象/数组取不到字符串，回落默认值。
- 影响：模型返回 `"reason": null` 这类畸形字段时按上面规则取值；`notes` 里的空字符串同样被丢弃。

### 18. `LoopCritique.is_should_replan()` 是方法，不是字段

- 实现成方法 `is_should_replan()`，不会出现在任何 JSON 输出里。
- 影响：无。`LoopCritique` 从不进 HTTP 响应（进响应的是 `LoopIterationTrace.shouldReplan` 这个真字段），
  也不进任何提示词。

### 19. 其他结构层面的取舍

- `reflection_agent.py` 里是模块级 `@dataclass TripPlanRequestView`，字段名 `requested_budget`，不进 JSON。
- `agent` 包不能在运行期导入 `service` 包（`service/__init__.py` 会导入 `trip_planning_service`，
  后者又要 `agent.loop_planning_agent`，形成循环导入），因此 `loop_planning_agent.py` 与
  `trip_planning_service.py` 对跨包类型只用 `TYPE_CHECKING` + 字符串标注。
- `LoopPlanningAgent` 的哨兵值用 `float("inf")`：四项严重度各被 `clampScore` 压在 100 以内，
  总分上限 400，两种写法在 `candidateScore < bestScore` 判定上行为一致。
- `LoopDecisionExecutor.ExecutionResult` 保持嵌套类写法。
- `TripPlanningService` 的构造器保留 `trip_adjustment_agent` 与 `loop_planning_agent` 两个依赖
  （另两个在 `planTrip` 和 `adjustTrip` 里都没有直接使用，`trip_adjustment_agent` 只被 `adjustTrip` 用）。
- `TripPlanningService.adjustTrip` 的重新规划走 `planTrip(decision.updated_request)`，即
  `LoopPlanningAgent.plan` 这个入口，没有另写一份规划逻辑。

### 20. `/adjust` 的内层请求会级联校验

- `TripAdjustmentRequest.original_request` 是带约束的 pydantic 模型，FastAPI 在校验时**会级联**进内层字段，
  非法内层值返回 **400 `VALIDATION_ERROR`**，details 用点号路径（如 `originalRequest.city: must not be blank`）。
- 这是刻意选的：不级联就得用 `model_construct` 绕过内层校验，而 `copy_request()` 本身走带约束的构造器，
  绕过后仍会在下一处报错，等于把 400 换成 500，所以不做。
  这一点在 `models/trip_adjustment_request.py` 的 docstring 里就地标注。
- 相同边界：`originalRequest` 缺失/为 JSON null -> 400 `VALIDATION_ERROR`，
  details 含 `originalRequest: must not be null`；`adjustmentPrompt` 缺失或全空白 ->
  `adjustmentPrompt: must not be blank`。

### 21. 预算正则里的 `\d`

- Python 的 `\d` 默认匹配 Unicode 十进制数字，全角 `３０００` 也会被识别成预算并改写入请求。
- 处理：写成 `re.compile(r"([0-9]{3,6})")`，收窄成 ASCII；"预算改成３０００"不会改预算。
- 影响范围：仅 `TripAdjustmentAgent.applyByRules` 的预算分支；loop 侧的预算来自请求体 `budget` 字段，
  不过这个正则。

### 22. 静态资源挂载顺序

- `StaticFiles` 是 `Mount`，Starlette 按注册顺序匹配路由，先注册的优先。`main.py` 因此把
  `app.mount("/", StaticFiles(directory=..., html=True))` 放在 `include_router(trip_router)`、
  `include_router(map_router)` **之后**，`/api/trips/plan`、`/api/map/config` 不会被同名静态目录抢走，
  `/` 落到 `static/index.html`。
- 验证：`app.routes` 实测顺序为 `/openapi.json`、`/docs`、`/docs/oauth2-redirect`、trips 路由、map 路由、
  最后的 `Mount("/")`；`tests/test_map_config_controller.py::test_should_serve_index_html_from_root_and_keep_api_routes_ahead_of_static`
  同时断言 `GET /` 返回页面 HTML、`POST /api/trips/plan` 非法体仍返回 `VALIDATION_ERROR` 错误 JSON（不是 HTML）、
  `GET /api/map/config` 返回 200。
- 差异：`StaticFiles` 只提供文件本身与 `ETag`/`Last-Modified`；页面是单个 `index.html`，行为等价。

### 23. `GET /api/map/config` 是响应体里回传密钥的端点

- `amap.web-js-key` 原样放进 `jsApiKey` 字段返回给浏览器（前端 SDK 加载地图需要），
  `security-js-code` 同样无条件透传；`enabled=false` 时 `jsApiKey` 回落成空串。
  逐字实现包括 `enabled ? webJsKey : ""` 与 `securityJsCode` 的无条件透传（未配置时返回 `null`）。
- 边界：这是全项目唯一一个把凭据写进响应体的端点，未额外加脱敏。密钥仍然只从环境变量/`.env` 读，
  不进代码、注释、README、报告与日志；`runtime_logger` 只打印 `webJsEnabled` 布尔。

---

## 二、验证记录

```
.venv/bin/python -m pytest -q
```

共 55 条用例（`55 passed`）。

- `tests/test_planner_agent.py` 断言逐条覆盖 PlannerAgent 的输出，含
  "ReflectionAgent 已对每日方案完成预算、路线、天气反思评估" 与 `agentName=ReflectionAgent`。
- `tests/test_trip_plan_controller.py` 用 `app.dependency_overrides[get_trip_planning_service]` 注入桩，
  断言 `overallSuggestions` / `days[0].transportation` / `budget.total` / `loopSummary` 四个字段 /
  `loopIterations[0]` 的 `requestSummary`、`reflectionAction`、`appliedChanges`、`selectedAsBest`，
  以及 400 的 `VALIDATION_ERROR` 结构。
  另有 3 条 `/api/trips/adjust` 用例：200 的响应体键集合恰为
  `{tripPlan, updatedRequest, appliedChanges}`（`adjustmentSource` 不进响应体）、
  `updatedRequest` 反映调整后的预算/住宿/补充要求、
  `tripPlan.loopSummary` 由重新规划链路真实填充；以及缺 `originalRequest`、`adjustmentPrompt` 全空白
  两条 400 details 文案。
- loop 的断言取自源码的真实分支与文案：
  `tests/test_reflection_agent.py`（严重度公式、`overallScore<=15` 可接受阈值、四个动作优先级与 `reason` 文案）、
  `tests/test_llm_critic_agent.py`（六个批评分支、`normalizeAction` 的非法值回落 STOP、节点取值语义、提示词三段 JSON）、
  `tests/test_loop_planning_agent.py`（八条闭环分支：首轮通过 / loop 关闭 / 重规划一轮后选中第 2 轮 /
  批评拒绝重规划 / 连续无改善 / 达到最大轮次 / 无可执行调整 / 检测到重复请求，含 trace 顺序与
  `appendLoopResult` 的三句备注）、
  `tests/test_request_optimization_strategy_service.py`（四分支 `applyAction` 文案、`mergeExtraRequirement`
  的"已含则跳过"、`copyRequest` 的净化差异）。
- `tests/test_trip_adjustment_agent.py` 的 10 条断言取自 `TripAdjustmentAgent` 与 `TripPlanningService`
  的真实分支与文案：
  规则路径 6 条（预算+住宿档位+移除偏好、打车+民宿+"路线紧凑"走统一策略、"祝旅途愉快"这类补充限制进
  `extraRequirements`、空白 prompt 落"已保留原始规划条件，并记录新的调整要求"、解析出的预算与原值相等时不记备注、
  `adjustmentPrompt=null` 不抛异常）；
  LLM 路径 3 条（结构化 JSON 产生的六句 "LLM 已…" 文案与顺序、system/user prompt 首尾逐字断言、
  畸形字段按节点取值语义处理、模型返回非 JSON 时 `adjustmentSource=rule-fallback`
  且回退备注在列表末尾）；
  再加 1 条用 stub agent 验证 `adjustTrip` 复用 `planTrip` 入口以及 `planningNotes` 追加的五句顺序。
- `providers.*=mock` 的整链路（HTTP 200 + 驼峰字段 + 预算/路线/天气结构）已用临时脚本跑通。
- 真实链路已在 2026-09-30 用本机密钥跑通（密钥只在 `.env`，未进代码与文档；这一条是 loop 未接入时的验证）：
  高德四源分别返回景点 16 个（`amap-poi-search`）、酒店 10 家（`amap-hotel-search`）、
  预报内天气 `source=amap-live`、路线 `高德路线已生成`；预报区间外日期按规则回退落 `source=rule-fallback`
  并写 `高德未返回该日期天气，已使用规则天气补全`；`POST /api/trips/plan` 两次（杭州 3 天 / 苏州 2 天）返回 200，
  其中一次出现 `need_revision` 触发 `refineDailyPlan` 的省预算调整，另一次 `within_budget`。
- Loop 闭环的真实请求验证（2026-09-30，`SERVER_PORT=8123`，四源 `amap` + 真实模型，密钥只在 `.env`）：
  杭州 3 天 / 预算 3000 一次得到 `loopSummary={enabled:true, roundsExecuted:2, selectedRound:1,
  stopReason:"检测到循环调整重复，保留当前最优方案"}`，`loopIterations` 两条、`selectedAsBest` 为
  `[true, false]`，批评来源 `llm`、动作 `REPLACE_OUTDOOR_WITH_INDOOR`；
  苏州 3 天 / 预算 1200 / 高端酒店 / 打车 一次得到 `roundsExecuted:3, selectedRound:2`，
  逐轮 `requestSummary` 显示修正真的作用到下一轮输入（住宿 高端酒店→舒适型酒店→经济型酒店、交通 打车→公共交通），
  `budgetSeverity` 75→56、`reflectionScore` 125→106，第 3 轮因请求与第 2 轮同签名而落到
  "检测到循环调整重复"。两次响应的 `planningNotes` 末尾三句逐字一致。
  服务日志 47 行里 `key=` 出现 0 次、两把真实密钥出现 0 次（`restapi.amap.com` 只在启动配置行出现一次，不带参数）。
- `/adjust` 的真实请求验证（2026-09-30，`SERVER_PORT=8123`，四源 `amap` + 真实模型，密钥只在 `.env`）：
  `adjustmentPrompt="预算改成6000，住经济型酒店，不要自然风景这类景点"`，`originalRequest` 为杭州 3 天 / 3000 元 /
  舒适型酒店 / 公共交通 / 偏好 `[美食, 自然风景]` / 补充要求"希望整体节奏轻松一些"。
  **HTTP 200，`time_total=66.795s`，响应 22647 字节**。走的是 LLM 解析路径，
  `appliedChanges` = `["LLM 已将预算调整为 6000 元", "LLM 已调整住宿偏好为 经济型酒店", "LLM 已移除偏好：自然风景"]`；
  `updatedRequest` 为 `budget=6000`、`accommodation="经济型酒店"`、`preferences=["美食"]`、
  `transportation="公共交通"`（未改）、`extraRequirements=null`（`copyRequest` 的净化按 23 个关键词丢掉了
  "希望整体节奏轻松一些"）；响应体键集合恰为 `{tripPlan, updatedRequest, appliedChanges}`。
  `tripPlan.planningNotes` 共 20 条，末尾五条依次为
  `改计划解析来源：llm`、`本次改计划要求：预算改成6000，住经济型酒店，不要自然风景这类景点`、
  以及上面三句 `appliedChanges`（同一份文案在响应体两处出现）。
  重新规划走的是 Loop 入口：`loopSummary={enabled:true, roundsExecuted:3, selectedRound:3,
  stopReason:"第 3 轮通过规则评估，直接停止"}`，逐轮 `requestSummary` 显示 `adjustmentPrompt` 解析出的
  经济型酒店进入第 1 轮输入、随后被 loop 的 `UPGRADE_EXPERIENCE` 依次上调为舒适型→高端，
  总花费 2319→3219→5019、`remainingBudget` 3681→2781→981，第 3 轮 `reflectionScore=0` 被选中。
  服务日志 30 行里 `key=` 出现 0 次、`api-key` 出现 0 次、两把真实密钥各出现 0 次，
  `restapi.amap.com` 只在启动配置行出现一次且不带参数；无 WARNING/ERROR 行。
- `/adjust` 的级联校验分支同次实测：`originalRequest` 内层字段全填非法值时返回
  **400 `VALIDATION_ERROR`**，details 为点号路径
  （`originalRequest.city: must not be blank` 等 6 条），见第 20 条；
  `transportation`/`extraRequirements` 传空串不报错，即这两个字段可空。
- `tests/test_map_config_controller.py` 的 4 条断言覆盖 `api/map_controller.py` 的两个分支：
  `enabled=true` 时响应体键集合恰为
  `{enabled, jsApiKey, securityJsCode, provider, message}` 且 `jsApiKey` 原样透传（测试用假值，不读 `.env`）、
  未配置与纯空白两种输入都落 `enabled=false` + `jsApiKey=""`（`securityJsCode` 仍无条件透传）、
  以及静态挂载不遮 `/api/` 路由（见第 22 条）。
- `tests/test_planning_data_agent.py` 的 3 条钉住取数两条路径：模型不可用时按
  `attraction -> weather -> hotel` 各调一次且结果写回 context；景点源抛错时短路（后两个源不被调用）
  并由 `raise_planning_errors` 抛出同一句文案；模型可用时仍然只走 `create_agent`、不碰固定顺序。
- 取数顺序由模型决定已单独验证：同一份工具与代码，仅替换 system prompt 的顺序倾向，实际调用序列由
  `['search_attractions', 'query_weather', 'recommend_hotels']` 变成
  `['recommend_hotels', 'query_weather', 'search_attractions']`（三个工具在同一轮模型输出里并行发起）。
- 关掉模型的真实请求验证（2026-09-30，`TRAVEL_LLM_ENABLED=false SERVER_PORT=8123`，四源仍为 `amap`，密钥只在 `.env`）：
  与浏览器那次同一份杭州 3 天请求体 → **HTTP 200，`time_total=2.958s`，响应 18846 字节**，
  `days=3`、`budget={total:2975, requestedBudget:3000, remainingBudget:25, status:"within_budget", withinBudget:true}`、
  第 1 天景点 `杭州西湖风景名胜区`、`routePlan.providerName=高德地图路线规划` +
  `高德路线已生成，共 2 段，预计 23.8 公里，通勤约 60 分钟`、`weatherInfo` 全部 `source=amap-live`。
  零模型调用的证据：`planningNotes` 里是 `需求理解来源：rule` 与 `结果文案来源：规则文案`，
  `loopIterations` 两条 `critiqueSource=rule`（动作 `REPLACE_OUTDOOR_WITH_INDOOR`、`reflectionScore=50`），
  整个响应体没有任何含 "LLM" 或 "模型" 字样的备注。
  服务端日志 8 行、2 条 WARNING 是高德路线逐段请求撞上 `CUQPS_HAS_EXCEEDED_THE_LIMIT`，
  按既有的限流保护自动回退规则路线、不影响整体 200；日志里 `key=` 出现 0 次、两把真实密钥出现 0 次。
- 前端页面的浏览器验收（2026-09-30，真实 Chrome 打开 `http://localhost:8123/`，四源 `amap` + 真实模型）：
  页面自己组装出的请求体为
  `{"city":"杭州","startDate":"2026-09-30","endDate":"2026-10-02","budget":3000,"preferences":["自然风景","美食","轻松节奏"],"transportation":"公共交通","accommodation":"舒适型酒店","extraRequirements":""}`
  （界面上的"自然风光/美食/休闲"三个复选框，在 `buildTripPayload()` 里映射成"自然风景/美食/轻松节奏"）。
- 接口状态码：第一次 `POST /api/trips/plan` 返回 **400**，服务端文案
  `高德景点检索失败：CUQPS_HAS_EXCEEDED_THE_LIMIT（infocode=10021）` —— 外部高德个人 key 的每秒调用配额被打满，
  不是本项目缺陷（同一份请求重试即 **200**，本轮共 3 次 400 / 1 次 200）；页面渲染结果后自行发起的
  `GET /api/map/config` 返回 **200**；根路径 `GET /` 返回 200 `text/html`。
  这也是第四节「高德限流处理」那行的由来：既然重试一次就能过，就不该让用户看到 400 ——
  现在由 `fetch_json` 自动退避重试，重试打光才抛，`details` 也不再误指用户输入。
- 字段对账：页面 JS 读取的 13 个数据结构（TripPlan / DayPlan / Attraction / Hotel / Meal / RoutePlan /
  RouteStep / Budget / WeatherInfo / AgentTrace / LoopExecutionSummary / LoopIterationTrace / MapConfigResponse）
  逐字段与后端吐出的驼峰键比对，**页面读取但后端不吐的字段 0 条**，因此 `index.html` 未作改动，
  前端与后端序列化都没动。
- 地图区结论（当次 `.env` 两项为空时）：**卡在缺 `AMAP_WEB_JS_KEY` / `AMAP_SECURITY_JS_CODE`**。
  `GET /api/map/config` 返回 `enabled=false`，页面走 disabled 分支，`.map-box` 内只有
  `map-placeholder`「真实地图待开启 / 尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示」，
  `#amap-canvas` 不存在、Marker 与 Polyline 数量为 0，未加载高德 SDK，也没有用假底图代替。
  同一份方案里的路线数据本身是真实的（`routePlan.provider=amap`，地图卡右上角显示"高德地图路线规划 / 2段路线"）。
- 补配 JS key 之后的地图验证（同日，`.env` 填入「Web端(JS API)」类型的 key 与安全密钥，各 32 字符）：
  启动日志 `amap.webJsEnabled=True`，`GET /api/map/config` 转为
  `{enabled:true, jsApiKey:<32 字符>, securityJsCode:<32 字符>, provider:"amap-jsapi", message:"高德地图前端配置已就绪"}`；
  真实 Chrome 重跑一次杭州 3 天后 `.map-box` 变成 `amap-container`：`window.AMap` 已加载、
  `window._AMapSecurityConfig` 已设、canvas 1 个、`.amap-marker` 2 个（酒店 H 点 + 景点 1 号点）、
  蓝色 Polyline 按当日 2 段路线绘制、比例尺与"高德地图 © 2026 AutoNavi"水印出现，
  角标由"真实地图待开启"变为 `真实地图 · 第1天 · 高德地图路线规划`；切到第 2 天后角标与点线全部重绘。
  控制台只有两条与地图无关的提示（label 用法、AMap 自身的 Canvas2D willReadFrequently 警告），
  无 `INVALID_USER_KEY` 一类鉴权错误；记录里的 SDK 请求 URL 已按 `key=***` 打码。
- Agent 轨迹与 loop 区块显示的是第 1 轮的真实数据：`Loop Planning 已启用 / 执行轮次：2 / 最终采用：第1轮方案 /
  停止原因：检测到循环调整重复，保留当前最优方案`；`第1轮循环` 卡片带"最终采用"徽标、动作
  `REPLACE_OUTDOOR_WITH_INDOOR`、`总分 50 / 预算 0 / 路线 0 / 天气 50 / 体验 0`、系统动作与
  执行改动 `已加入雨天室内备选约束`；`第2轮循环` 同结构；下方 18 条 `agentTraces` 从
  `1. LoopPlanningAgent / success / 第 1 轮生成初稿` 开始逐条列出。
- 截图与 DOM 证据在 `_verify/`：`01-overview.png`、`02-map-section.png`、`03-agent-trace.png`、
  `04-days.png`、`05-fullpage.png`（1440x6410 整页）与 `api-calls.json`、`browser-network.txt`、
  `render-summary.json`、`loop-block-dom.json`。补配 JS key 后新增 `06-map-day1-real.png`、`07-map-day2-real.png`
  （真底图 + 酒店 H 点与景点 1 号点 + 当日 Polyline）与 `map-real-api-calls.json`。截图取自一次性 headless Chrome：加载未改动的 `index.html`，
  调用页面自带的 `renderResult()` 渲染**上面那次真实 200 响应的原样落盘数据**（真实浏览器窗口在另一个 Space，
  macOS `screencapture` 只能拿到缩略图，故改用 headless 出图；页面代码与后端均未为此改动）。
  `browser-network.txt` 里是真实 Chrome 那次的三个接口状态码与服务端访问日志摘录。
- 本轮服务日志 237 行里 `key=` 出现 0 次、`.env` 里三把真实密钥各出现 0 次（脚本按 `.env` 实际值比对，
  值本身不回显）。

---

## 三、能力扩展（分支 `feature/ask-agent`）

这一节记的是主链路之外新增的能力——它们不在上面的行为口径里，因为没有可对照的旧行为。

1. `POST /api/trips/ask`：方案生成后的只读追问 agent。
2. 新增文件：`models/ask_request.py`、`models/ask_response.py`、`agent/ask_context.py`、
   `agent/ask_tools.py`、`agent/ask_agent.py`、`api/ask_controller.py`、`tests/test_ask_agent.py`、
   `docs/ASK_AGENT_DESIGN.md`；改动 `main.py`、`config/container.py`、两个 `__init__.py` 与 `static/index.html`。
3. 对既有链路的影响：`/api/trips/plan`、`/api/trips/adjust`、`/api/map/config` 的语义、响应体与错误分支
   一字未改，原有 55 条用例未作修改且仍全绿（A 合并后 62 条；B 与第四节补格子之后共 85 条）。
4. 前端页面有两类改动，因此 `static/index.html` 只在 `main` 分支保持逐字一致：
   - 加了"方案追问"区块（新端点 + 加载态）；
   - 价格文案改成标明估算来源，共四处：每日行程景点价格牌 `¥30（估算）`（带 title 说明）、
     地图信息窗 `门票 ¥30（估算）`、酒店行 `价格范围（估算）: 260-560元`、
     预算卡两行小字改成"门票按景点类别估算后累加，非实时票价"与"按天数与酒店档位估算累计"。
     改的是显示文案，`ticketPrice` / `priceRange` / `estimatedCost` 这些字段的值与算法一字未动。
5. 为什么要标估算：实测高德 POI 接口不返回景点票价（顶层 47 个字段无价格字段，`biz_ext.cost` 为空、
   `ticket_ordering` 是 `'0'`/`null`），酒店侧 `biz_ext.cost` 实测 10 家也全为空，
   于是门票走 `inferTicketPrice` 的四个固定值（60/50/0/30）、酒店走按预算档位推算。
   这些行为未改，这里只是把它标出来。
6. 追问 agent 的提示词同步加了一条：工具返回值里带「估算」的价格，转述时必须保留「估算」二字。
7. 设计与逐题验证：见 `docs/ASK_AGENT_DESIGN.md` 第 11 节（真实 10 题全 200、单题 1.3~3.2 秒、
   护栏命中与留痕形状均有记录）。

---

## 四、主动偏离

上面记的是不得不的取舍，这一节记的是**刻意改掉主链路原有行为**的地方，逐条见下表。
判定基准：请求里不填新字段时，`/api/trips/plan` 的逐天酒店、**景点顺序**、预算与留痕条数必须保持原口径
不变，这条写成用例钉住（`tests/test_hotel_anchor.py::test_shouldKeepWholeCityCandidatesWhenAnchorIsNotFilled`）。
**这条基线现在只剩两个例外，都是"不分填没填新字段"都生效的默认路径改动**：
① 当日路线起讫点的通勤数字（早上那段酒店→首站不计）；② 预算分配（日预算口径、每天景点数、住宿上限与酒店打分、
市内通勤 20 元/天）与真实动线端点里"第 N 天从前一晚酒店起步"那半。
城际大交通是**可选开关**：不填 `originCity` 时票价为 0、第一天仍从首景点起步，与现状一字不变。

| 偏离点 | 现在的做法（及改掉的旧口径） | 影响范围 |
| --- | --- | --- |
| 预算因子（2026-10-10 追加） | 改成**先扣城际、再预留门票与吃行、最后给酒店**：日预算＝(总预算−往返大交通)÷天数；门票预留＝目标景点数×60；住宿上限＝min(日预算×0.45, 日预算−门票预留−155) 且是**资格过滤**（超上限直接不进候选，不进打分）；酒店打分加"每 60 元折 1 公里"的连续成本项、评分项给 0.1 分＝0.5 公里且低预算也生效；砍景点只保留"日预算<450 → 1 个"；头尾两天各 1 个景点、中间天默认 2 个（≥1000 给 3、≥1800 给 4）；市内通勤公共交通档 20 元/天固定，打车与自驾仍按里程。改掉的旧口径是：日预算＝总预算÷天数；门票可用钱＝日预算−酒店实际价−180（酒店先占）；一晚超日预算 55% 才罚 12 分；评分项只在日预算≥1600 时进分；酒店占日预算 52%/65% 就把景点砍到 2/1 个；单日景点按"单双日 3/2 交替"排 | **默认路径也生效**（不填任何新字段也变）：2000 元 3 天从"每晚 380、每天 1 个景点"变成"住宿被限到约 226、每天 2 个景点" |
| 真实动线端点（2026-10-10 追加） | `RouteQuery` 多 `startPoint` / `endPoint`：第 N 天从前一晚那家酒店起步，规则引擎、高德分段、前端折线三条链一起读它；没给端点时仍按"首个景点起步、末站回当晚酒店"。旧口径是路线只有 `hotel` 与 `attractions` 两个入参，起讫点写死在两个 client 里 | 第 2 天起默认生效；第一天的到达口岸与最后一天的返程口岸只在填了 `originCity` 时出现 |
| 城际大交通（2026-10-10 追加） | 新增 `originCity` → `IntercityService`：跨城 `transit/integrated`（`extensions=all`）取 `cost` 与 `railway.arrival_stop`，按 G/D/C/S 与 T/K/Z 分高铁/普速，飞机按直线估并进同一池；**先筛掉下车站不在目的城市的方案**（60 公里外），再按"往返≤总预算 35% 的闸门内取最便宜"选档，全被淘汰时取最便宜并让方案标红；预算加第五桶 `totalIntercity`，日预算从"可玩预算"倒推；耗时只留档不参与判定（统一假设早上出发、中午前后到、回程当天完成）。旧口径是请求里没有出发城市（前端那个框只做展示、不进请求体），预算只有四桶 | 只在填了 `originCity` 时生效；每次规划多 1 次跨城请求；前端预算卡六格→七格、线路图与地图多一种"口岸"节点、超支那格标红 |
| 当日路线起讫点（2026-10-09 追加） | 排成 `首个景点 → 景点… → 酒店`：早上那段不计（游客自行抵达首站，不存在"从酒店发车"），只保留收工回酒店那一段。酒店仍按到当天景点中心的距离挑，只有"住宿位置＝当天点名的某个必去景点"时才锚到最后一站附近（用户另指了住宿位置时不锚，压的仍是最后那段回程）。旧口径是规则与高德两条路线都排成 `酒店 → 景点… → 酒店`，第一段永远是"从酒店出发" | **默认路径也生效**：每天少一段里程与分钟，当日 `estimatedCost` 随通勤成本一起降；前端折线（`buildRoutePoints`）同步去掉起点那个酒店节点 |
| `TripPlanRequest` 的住宿字段 | 多 `hotelAnchor` / `dayHotelAnchors` / `hotelContinuity`，全可空，空串归一成未填（旧口径是 8 个字段，没有住宿位置与连续性） | 请求体向后兼容，老请求照常 |
| `PlannerAgent` 选酒店 | `_select_hotel(hotels, attractions, dailyBudget)` 三项打分（价位惩罚 0/8/12/24/50 + 到当天景点中心公里数 + 预算≥1600 才有的质量加成），打分公式与常数**一字未改**；改的是进入打分的集合——先按锚点半径圈掉远处，再按远近分档（一档 1.5km、二档 3km），最低非空档才交给原打分（旧口径是候选就取全城那 10 家） | 只在用户填了住宿位置时生效 |
| `PlannerAgent` 选景点（2026-10-09 追加） | 排序前面加一档"离酒店距离"（≤5km / ≤10km / 其余，近档整档先选，同档内仍跑原 `_score_attraction` 一字未改）；硬顶从 25km 收到 15km（圈内不足 2 个仍回落全城，防某天选空）。初步景点趟无酒店、档恒 0，退化成纯分数，故酒店选取不受影响 | 每次带酒店的最终选点趟都生效（即任何一次真实规划） |
| `PlannerAgent` 必去景点（2026-10-09 追加） | 新增 `day_attraction_pins`（第几天→景点名列表，一次追问可点名多个）。**只在有必去景点那天生效、普通链路一行不动**：只 1 个→以它为中心补附近高分（酒店锚到它+就近档），它留作最后一站；2 个及以上→这天就=点名的这些、不再补别的，游玩顺序由 `_order_day_pins` 算（2026-10-10 改）：全排列取"相邻站＋末站回收工地点"里程最短，多绕≤3 公里才算并列、才让模型在并列里按时段常识挑，模型不可用则按写死的类别常识表排（山/公园＝上午、塔/夜景＝傍晚），判不出取最省路那条，依据写进备注。酒店分两种（2026-10-10 修正）：住宿位置就是当天点名的某个景点（确认卡上点了"挪"、后端代填那格）→ 从**这一天的候选池**（含按位置补搜回来的那批）取离最后一站最近的一家；住宿位置是另一个地名（用户亲口另指了地方）→ **不重挑**，主链路按它圈出的结果就是最终结果，并在备注写明没跟着末站挪走。景点名先池内按名字对上、对不上 `find_attraction_by_name` 补搜、还没有才记"未找到未排入"；进循环前先占住 `used` 防别天抢；超预算/多绕以具体数字写进备注 | 只在用户点名必去某景点时生效；不填逐天与现状一字不变 |
| `PlannerAgent` 逐天循环 | `hotelContinuity=strict` 时第 1 天定店后逐天沿用，并把两处入口的候选都收成那一家；同时在当天 `optimizationNotes` 写明它比最近的一家远多少公里。旧口径是每天独立选店、中途换店零代价，`reduce_budget` 分支还会再换一次（`_select_budget_friendly_hotel`） | 只在选"整趟同一家"时生效 |
| 高德调用次数 | 酒店除 `/v3/place/text`（按关键词相关性返回 10 家）外，填了锚点时多发两类：`/v3/place/text`（地名→坐标）与 `/v3/place/around`（锚点周边酒店，按距离排序） | 每个不同地名各一次，3 天最多 3+3 次；都走既有 curl 兜底与配额重试 |
| 高德限流处理 | `fetch_json` 统一加一层短退避重试（默认退避 0.8s、2.0s；`AMAP_RATE_LIMIT_RETRY_ATTEMPTS=0` 即关闭、退回只有路线降级的原行为）。重试打光才抛，路线原有的降级路径不变。旧口径是只有路线识别 `infocode=10021` / `CUQPS_HAS_EXCEEDED_THE_LIMIT` 并降级到规则路线；景点、天气、酒店撞限流直接抛，`raise_planning_errors` 把整个请求打成 400 | 只对配额/频率类失败生效，参数错、key 错不重试；撞限流的请求最多多等 2.8 秒。判定收敛在 `exceptions.is_quota_exhausted`，重试、路线降级、错误文案三处共用一套标准 |

另外几处小偏离，一并记下：

1. `RequestOptimizationStrategyService.copy_request` 逐项列举字段，那三格（`hotelAnchor` / `dayHotelAnchors` /
   `hotelContinuity`）要一起补上。漏掉的后果不是报错而是静默丢数据：用户改一次行程，上次填的住宿位置就没了。
2. `AskResponse` 从最初的 `{answer, traces}` 扩成 `{answer, intent, adjustmentPrompt, anchorHint, anchorDay}`，
   `AskAgent` 的最后一轮输出改成固定 JSON。
3. `static/index.html` 在"住宿偏好"下面加了「期望住宿位置」输入框与「住宿连续性」下拉，
   追问区块下面加了「确认修改 / 算了」一行。
4. 配置新增 `travel` 的锚点段：`TRAVEL_HOTEL_ANCHOR_RADIUS_KM`（默认 5.0）、
   `TRAVEL_HOTEL_ANCHOR_NEAR_KM`（1.5）、`TRAVEL_HOTEL_ANCHOR_MID_KM`（3.0）。
   这三个数只决定谁有资格进打分，不参与任何分数计算。
5. 追问 agent 的提示词补第 8 条：`pinHints` / `anchorHint` 只填用户说出口的地名原词，
   **不要求它先出现在方案或候选清单里**——下游会按名字补搜（`find_attraction_by_name`），
   补搜也没有才写进"未找到未排入"备注。
   起因是 2026-10-09 实测：用户点名"广州图书馆"，模型按第 1、4 条"事实只能来自工具返回值"把它判成做不到的诉求，
   直接回"方案里查不到，这条加不进去"而没判 `need_change`；而高德按名字检索是有的
   （`科教文化服务;图书馆`，坐标 113.326020,23.116225）。规则层没改，改的是模型的判定口径。

为什么"高德调用次数"那行的两类额外请求是必需的，不是锦上添花（2026-10-08 实测）：
杭州、预算 3000、3 天、舒适型酒店的真实候选池只有 10 家，它们到西湖的距离升序是
`6.1 6.2 6.3 9.1 10.5 11.1 18.1 22.3 63.3 110.1` 公里——在这种池子里画 5 公里的圈必然圈空，
"住得离西湖近一点"照样落不了地。换成 `/v3/place/around` 以西湖为圆心、半径 5 公里检索，
同一时刻返回的是 12m、408m、410m、414m 这一批真实存在的湖边住宿。

---

## 五、已知的怪口径

这一节记的是照代码读会绊一下、但暂时不打算改的地方。只写现状与踩到的表现。

**1. `RuleBasedHotelClient` 的酒店坐标写死。**
`clients/rule_hotel.py` 里每家酒店的 `Location` 都是 `longitude=116.4200, latitude=39.9300`，跟请求城市无关。
表现：`providers.*=mock` 时规则路线会算出上千公里的路程，`totalTransportation` 跟着很大。

**2. 一批写死的阈值，按用途分四组。**
- 餐费档位（`planner_agent.py` 的 `_build_meals`）：`luxury_budget = daily_budget_limit >= 2500`、
  `premium_budget >= 1600`、`budget_tight < 700`、`budget_very_tight < 520`、`budget_critical < 380`。
  早餐 58/42/12/15/20/30、午餐 168/118/25/35/45/60、晚餐 238/168/35/50/70/90，按上面的档位优先级取。
- 调整轮次：单天省预算循环里 `adjust_rounds >= 6` 就停，备注写
  "已达到预算压缩上限，当前方案为可生成的最低配版本"。
- 景点数：`_sanitize_attractions` 里净化后 `len(scenic_attractions) <= 3` 就直接返回，不做聚类。
- 距离阈值：聚类半径 `<= 35` 公里（`_sanitize_attractions`，聚类后不足 3 个仍回退全集）；
  普通天动线总长上限 `_DAY_ROUTE_CAP_KM = 25.0`，排不满逐级放宽到
  `_DAY_ROUTE_CAP_LADDER_KM = (25.0, 50.0)`；到达日/离开日的"附近"半径
  `_ANCHOR_DAY_RADII_KM = (10.0, 20.0, 30.0, None)`，圈里少于 `_ANCHOR_DAY_MIN_CHOICES = 2` 个就放宽一级。

**3. `_sanitize_attractions` 的黑名单与默认时长/票价。**
- `_is_food_like_attraction` 丢掉：类别含"美食"，或描述含"餐饮服务"/"中餐厅"/"咖啡厅"/"火锅"/"烧烤"。
- `_is_invalid_attraction` 丢掉：描述含"公司企业"/"金融保险服务"/"汽车服务"/"商务住宅"/"政府机构及社会团体"，
  或名字含"公司"/"有限公司"。
- `location is None` 的直接丢。
- 默认游览时长 / 票价由 `clients/amap_attraction.py` 的 `_infer_visit_duration` / `_infer_ticket_price`
  按类别定：自然风景 180 分钟 / 60 元、人文历史 150 / 50、美食购物 90 / 0、其余 120 / 30。

**4. 两处 `copy_request` 的净化不对称。**
`RequestOptimizationStrategyService.copy_request` 会调 `_sanitize_persisted_extra_requirements`
（按 `[；;\n]` 切句、丢掉命中 23 个调整关键词的子句，全空则置 `None`）；
`LoopPlanningAgent._copy_request` 直接新建对象，`extra_requirements` 原样带过、不净化。
表现：同一份 `extra_requirements` 走 loop 的两条复制路径结果不同——经过 `LoopDecisionExecutor` 的那份
会丢掉"预算/节奏/慢一点"之类表述，而 `requestSummary` 与 `requestSignature` 读到的仍是原文。

**5. `_apply_shared_action` 命中第一个关键词就 `return`。**
loop 与 adjust 共用四个统一策略动作（`REDUCE_BUDGET` / `COMPRESS_ROUTE` / `REPLACE_OUTDOOR_WITH_INDOOR` /
`UPGRADE_EXPERIENCE`，各带一组关键词），第一个命中的关键词生效后立刻 `return`。
表现：一条 prompt 里同时写"路线紧凑"和"住好一点"，`applied_changes` 只会有第一条"已应用统一…策略"。
另外 `_apply_shared_strategy_actions` 是规则解析的最后一个分支，它追加的备注排在其它文案之后。

**6. 规则解析里 `normalized_prompt` / `lower_prompt` 混用。**
`_adjust_by_rules` 里 `normalized_prompt` 只做 `strip()`、`lower_prompt` 再做 `.lower()`。
中文关键词两者结果相同，但每个分支用哪个并不统一：
`_apply_budget_adjustment` 的门禁 `"预算"` 与数字正则用 `normalized_prompt`，
同方法里"省一点/便宜点/控制预算"用 `lower_prompt`；
`_update_preference` 的 `wants_remove` / `wants_add` 用 `normalized_prompt`，
"轻松节奏"特例那支用 `lower_prompt`（这一支的关键词组是"轻松"/"慢一点"/"悠闲"，
比上面传给 `_update_preference` 的那组少一个"别太赶"）；
`_apply_transportation_adjustment` 的"智能推荐 / 你来安排交通"、
`_apply_accommodation_adjustment` 的"客栈"用 `lower_prompt`，同方法其余档位用 `normalized_prompt`。

**7. 23 个调整关键词有两份。**
`TripAdjustmentAgent._ADJUSTMENT_KEYWORDS` 与 `RequestOptimizationStrategyService._ADJUSTMENT_KEYWORDS`
各写一遍，是内容相同的 23 个词；`_is_adjustment_instruction` 也各有一份。
表现：只改一处时，`_extract_residual_requirements` 与 `_sanitize_persisted_extra_requirements`
的取舍会分叉。

**8. `appliedChanges` 出现在响应体的两个位置。**
`TripAdjustmentResponse.applied_changes` 直接拿 `decision.applied_changes`；
同一批字符串又被 `planning_notes.extend(...)` 追加到 `tripPlan.planningNotes` 末尾
（排在"改计划解析来源：…"和"本次改计划要求：…"两句之后）。
表现：同一份文案在响应体里出现两次。

**9. "LLM 解析失败，已自动回退到规则解析"排在末尾。**
LLM 解析抛异常时先走 `_adjust_by_rules`，再把这句 `append` 到 `applied_changes` 末尾，
所以它排在规则路径已经产生的所有文案之后，不在第一条。
`tests/test_trip_adjustment_agent.py::test_shouldFallbackToRulesAndMarkSourceWhenLlmResponseIsNotJson`
按这个顺序断言。

**10. 前端"出发城市"输入框不进请求体。**
`static/index.html` 里的 `#origin`（`value="上海"`、`required`）在 JS 里从没被读过，
`buildTripPayload()` 不取它，页面提交的请求体里也没有 `originCity`。
表现：页面上填的出发城市不影响方案。

**11. `RouteEvaluator.is_route_too_long` 的 `current_weather` 形参不参与判定。**
签名是 `is_route_too_long(route_plan, constraints, current_weather, indoor_adjustment_recommended)`，
判定只用 `determine_route_limit_minutes(...)` 算出的时长上限，加上 `route_plan.total_duration_minutes`
与 `route_plan.total_distance_km`（里程上限 `_DISTANCE_LIMIT_KM = 70`）。
表现：天气再差也不会让这条判定变真。

**12. `ReflectionAgent._recommend_trip_action` 的 `UPGRADE_EXPERIENCE` 一支不比大小。**
前三支 `REDUCE_BUDGET` / `COMPRESS_ROUTE` / `REPLACE_OUTDOOR_WITH_INDOOR` 都要跟其余三个严重度比
（`>=` 其余三个且自身 `> 0`），最后这支只判 `experience_severity > 0`。
表现：`experience_severity` 只要大于 0、且前三支都没命中，就落到 `UPGRADE_EXPERIENCE`。

**13. `LlmCriticAgent._critique_by_rules` 里 `reflection is None` 的兜底分支。**
此时 `budget_severity` / `route_severity` / `weather_severity` / `experience_severity` / `overall_score`
全取 0，`experience_too_conservative` 改由 `plan.budget` 与 `request.budget` 反算：
`plan.budget.within_budget and request.budget >= 5000 and plan.budget.remaining_budget > max(1800, request.budget // 3)`。
表现：这条路上只有"体验偏保守"一支能命中（动作 `UPGRADE_EXPERIENCE`），否则落到 `STOP`。

**14. `LoopPlanningAgent._build_request_summary` 只给交通那一格回落。**
`city` / `budget` / `accommodation` 直接插进 f-string，为 `None` 时拼出字符串 `"None"`（空白串也不替换）；
只有 `transportation is None` 那一格回落成 `"智能推荐"`。
另一处 `_signature_part`（供 `_build_request_signature` 用）的口径不同：`None` 渲染成 `"null"`、
列表走 `render_list`、其余走 `str()`。

**15. 空白 prompt 时两条路径的兜底文案不同。**
`TripAdjustmentAgent` 两条解析路径都用 `if not applied_changes:` 收尾：
规则路径追加"已保留原始规划条件，并记录新的调整要求"，
LLM 路径追加"LLM 已保留原始规划条件，并记录新的调整要求"（多两个前缀字）。
表现：同一句空 prompt 走哪条路，`appliedChanges` 里的文案不一样。

**16. `api/map_controller.get_map_config` 的两条 `message` 文案。**
`enabled` 时 `message="高德地图前端配置已就绪"`，否则
`"尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示"`；`provider` 恒为 `"amap-jsapi"`。

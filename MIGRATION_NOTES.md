# 迁移说明：Java Spring Boot -> Python + LangChain

源项目（只读，未修改其中任何文件）：`/Users/lwl/Documents/智能旅游助手-1`
新代码：`/Users/lwl/Documents/langchain-旅游智能体/travel-assistant-py`

事实来源是 Java 源码，不是 README。本文只记录两类内容：

1. Java 文件与 Python 文件的对应关系；
2. 两边不一致的地方（含本轮约定不做的部分），每条写清 Java 行为、Python 行为、影响范围。

---

## 一、本轮范围

已搬：`POST /api/trips/plan` 入口 + service + 四个高德数据源 client（含 mock/规则兜底开关）+ PlannerAgent +
预算/路线/天气三个 evaluator + 取数链路的 LangChain `create_agent` 编排 + Loop 多轮闭环
（LoopPlanningAgent、ReflectionAgent、LlmCriticAgent、LoopDecisionExecutor、RequestOptimizationStrategyService、
`travel.loop` 配置、四个 loop model）+ 行程调整链路（`POST /api/trips/adjust`、TripAdjustmentAgent、
TripAdjustmentRequest/TripAdjustmentResponse、TripPlanningService.adjustTrip）
+ 前端页面与地图配置接口（`GET /api/map/config`、MapConfigResponse、`static/index.html` 原样拷贝、
`main.py` 挂载 StaticFiles）。

至此 Java 侧的对外接口（`/api/trips/plan`、`/api/trips/adjust`、`/api/map/config`）与前端页面全部搬完，
Java 侧不再有未搬的 controller、model 或静态资源。

本轮补齐：无可用模型时的纯规则取数兜底（`PlanningDataAgent._collect_by_fixed_order`，即 Java 的固定顺序），
第三节第 1 条由"接受的不一致"改成"已实现的两条路径"；项目初始化 git 仓库并加 `.gitignore`。

---

## 二、Java -> Python 文件映射表

包路径 `com.lwl.travelassistant` 对应 Python 包 `travel_assistant`。

### 入口与配置

| Java | Python | 说明 |
| --- | --- | --- |
| `TravelAssistantApplication.java` | `travel_assistant/main.py` | Spring Boot 启动 -> FastAPI `create_app()` + uvicorn |
| `src/main/resources/application.yml` | `travel_assistant/config/settings.py` + `.env.example` | Python 侧没有 yml，逐项改为环境变量 |
| `config/TravelProviderProperties.java` | `config/settings.py` 的 `ProviderSettings` + `AmapSettings` | |
| `config/TravelLlmProperties.java` | `config/settings.py` 的 `LlmSettings` | |
| `config/TravelLoopProperties.java` | `config/settings.py` 的 `LoopSettings` | `enabled=true` / `maxRounds=3` / `maxNoImprovementRounds=2`，环境变量 `TRAVEL_LOOP_*` |
| `config/TravelClientConfiguration.java` | 未搬（只剩 `RestTemplate` 一个 Bean，Python 用 httpx，不需要 Bean 声明） | |
| `config/TravelRuntimeLogger.java` | `config/runtime_logger.py` | 启动日志文案一致，见第三节第 12 条 |
| （Spring 组件扫描 + 构造器注入） | `config/container.py` | Python 无 DI 容器，按 Java 的依赖关系手工装配 |

### 控制器与异常

| Java | Python |
| --- | --- |
| `controller/TripPlanController.java`（`/plan` + `/adjust`） | `api/trip_controller.py` |
| `exception/GlobalExceptionHandler.java` | `api/error_handlers.py` |
| `exception/TripPlanningException.java` | `exceptions.py` |
| `controller/MapConfigController.java` | `api/map_controller.py` |

### client 层

| Java | Python |
| --- | --- |
| `client/AttractionClient.java`、`HotelClient.java`、`RouteClient.java`、`WeatherClient.java` | `clients/base.py`（4 个接口合成一个模块） |
| `client/LlmClient.java` | `clients/llm.py` 里的 `LlmClient` 抽象类 |
| `client/impl/OpenAiCompatibleLlmClient.java` | `clients/llm.py` 里的 `OpenAiCompatibleLlmClient` |
| `client/impl/AmapAttractionClient.java` | `clients/amap_attraction.py` |
| `client/impl/AmapHotelClient.java` | `clients/amap_hotel.py` |
| `client/impl/AmapWeatherClient.java` | `clients/amap_weather.py` |
| `client/impl/AmapRouteClient.java` | `clients/amap_route.py` |
| （4 个 Amap client 里重复的 URL 拼装、curl 兜底、status 校验） | `clients/amap_http.py`（合并，见第三节第 6 条） |
| `client/impl/InMemoryAttractionClient.java` | `clients/local_attraction.py` |
| `client/impl/RuleBasedHotelClient.java` | `clients/rule_hotel.py` |
| `client/impl/RuleBasedWeatherClient.java` | `clients/rule_weather.py` |
| `client/impl/RuleBasedRouteClient.java` | `clients/rule_route.py` |
| `client/selector/Configurable*Client.java` | `clients/selector.py`（4 个类保留在同一模块） |

### agent / evaluator / service / model

| Java | Python |
| --- | --- |
| `agent/AttractionAgent.java` | `agent/attraction_agent.py` |
| `agent/WeatherAgent.java` | `agent/weather_agent.py` |
| `agent/HotelAgent.java` | `agent/hotel_agent.py` |
| `agent/RouteAgent.java` | `agent/route_agent.py` |
| `agent/PlannerAgent.java` | `agent/planner_agent.py` |
| `agent/ReflectionAgent.java` | `agent/reflection_agent.py`（`reflectDailyPlan` 回指 `evaluator/daily_plan_evaluator.py`，见第三节第 4 条） |
| `agent/LoopPlanningAgent.java` | `agent/loop_planning_agent.py` |
| `agent/LlmCriticAgent.java` | `agent/llm_critic_agent.py` |
| `agent/LoopDecisionExecutor.java` | `agent/loop_decision_executor.py` |
| `agent/TripAdjustmentAgent.java` | `agent/trip_adjustment_agent.py`（含静态内部类 `TripAdjustmentDecision`） |
| （Java 里 `PlannerInputBuilder` 固定顺序调 3 个 agent） | `agent/planning_context.py` + `agent/data_tools.py` + `agent/orchestration.py`，见第三节第 2 条 |
| `evaluator/BudgetEvaluator.java` | `evaluator/budget_evaluator.py` |
| `evaluator/RouteEvaluator.java` | `evaluator/route_evaluator.py` |
| `evaluator/WeatherEvaluator.java` | `evaluator/weather_evaluator.py` |
| `evaluator/DailyPlanEvaluator.java` | `evaluator/daily_plan_evaluator.py` |
| `service/PlannerInputBuilder.java` | `service/planner_input_builder.py` |
| `service/RequirementAnalysisService.java` | `service/requirement_analysis_service.py` |
| `service/LlmNarrationService.java` | `service/llm_narration_service.py` |
| `service/TripPlanningService.java` | `service/trip_planning_service.py`（`planTrip` + `adjustTrip`；`adjustTrip` 内调 `planTrip`，未另写规划逻辑，见第三节第 19 条） |
| `service/TravelKnowledgeService.java` | `clients/knowledge_base.py`（唯一调用方是 `InMemoryAttractionClient`，见第三节第 14 条） |
| `service/RequestOptimizationStrategyService.java` | `service/request_optimization_strategy_service.py`（loop 与 adjust 共用同一个实例：`LoopDecisionExecutor` 与 `TripAdjustmentAgent` 都注入它，公开方法 `applyAction`/`mergeExtraRequirement`/`copyRequest` 照搬） |
| `model/*.java`（37 个） | `models/*.py`（33 个模型，另加新增的 `base.py` 与 `__init__.py`，见下） |
| （Java 全局工具方法散落在各类中：`Math.round`、保留一位小数、haversine） | `utils.py` 的 `jround` / `round1`；haversine 仍在 `planner_agent.py` 内 |
| `src/main/resources/static/index.html`（2905 行） | `travel_assistant/static/index.html`（`cp` 原样拷贝，`main` 分支上与源文件字节级一致；`feature/ask-agent` 分支多了一块"方案追问"区块，见第六节） |
| Spring Boot 对 `src/main/resources/static/` 的默认挂载（根路径 + 欢迎页） | `main.py` 末尾 `app.mount("/", StaticFiles(directory=..., html=True))`，注册在两个 `include_router` 之后，见第三节第 22 条 |

未搬的 4 个 model：`TripPlanResponse`（源码里是死类，无人引用）、`BudgetSummary`、`HotelRecommendation`、
`MealPlan`（三者只被 `TripPlanResponse` 使用）。
`TripAdjustmentRequest`、`TripAdjustmentResponse` 已随 `/api/trips/adjust` 搬入，
`MapConfigResponse` 已随本轮 `GET /api/map/config` 搬入。

新增的 Python 文件（Java 无对应物）：`models/base.py`（`CamelModel`，驼峰字段名对齐用）、
`agent/planning_context.py`、`agent/data_tools.py`、`agent/orchestration.py`、`api/__init__.py`、`utils.py`、
`config/container.py`。

---

## 三、语义不一致清单

### 1. 取数链路两条路径：有模型走 create_agent，无模型退回 Java 固定顺序

- Java：`travel.llm.enabled=false` 时仍能出完整行程，取数是三个 agent 的固定顺序方法调用，LLM 只用于需求解析和文案润色，
  两处都有规则兜底（`analysisSource=rule`、`结果文案来源：规则文案`）。
- Python：该能力已补齐（早先版本这里会直接抛"未配置可用模型"，已删除）。`is_available()` 为真时走
  `create_agent` 由模型自选工具；为假时 `PlanningDataAgent._collect_by_fixed_order` 按
  `search_attractions -> query_weather -> recommend_hotels` 顺序逐个 `invoke({})`，就是 Java 的那个顺序；
  任一源报错立即 `break`，后面的源不再发请求，异常仍由 `data_tools.raise_planning_errors` 冒泡成同一句 400 文案。
- 与 Java 的剩余差别：Java 是直接调三个 agent 方法、异常当场逃出 `build()`；Python 复用同一批 LangChain 工具对象
  （工具内部仍是那三个 agent），异常先暂存进 `context.errors`、取数收尾时再抛。对外可见的提示文案、
  短路位置（第一个失败点之后的源不再被调用）与 Java 一致，只有"抛出时机"晚了几行代码。
- 判据：`is_available()` 的四个条件（`enabled` + 非空 `api-key` + `base-url` + `model`）任一不满足即走固定顺序，
  比 Java 只看 `travel.llm.enabled` 一个开关更严格；异常暂存的细节见第 3 条。

### 2. 取数编排：固定顺序 -> 模型自主选工具

- Java：`PlannerInputBuilder.build()` 里依次 `attractionAgent.search()` -> `weatherAgent.query()` -> `hotelAgent.recommend()`。
- Python：`create_agent(model, tools=[search_attractions, query_weather, recommend_hotels], system_prompt=...)`，
  调用顺序和轮次由模型决定。没有可用模型时不走这条路，退回上面那三个工具的固定顺序（见第 1 条）。
- 为保证事实仍来自数据源而不是模型编造：三个工具都是**零参数**，查询条件从请求级 `PlanningContext`（contextvar）里取，
  取到的结构化结果写回同一个 context；system prompt 明确禁止编造景点/酒店/价格/坐标/天气，只允许复述"数据已就绪"。
  PlannerAgent 只读 context 里的结构化对象，不读模型的自然语言。
- 影响：`agentTraces` 与 `planningNotes` 的内容不受调用顺序影响（builder 按固定的 4 条 trace 顺序生成），
  但一次请求消耗的模型轮数、耗时与 Java 不同。
- 已验证：contextvar 在 LangGraph 工具节点（含三个 tool_call 并发执行）中可见，取数结果非空、errors 为空。
- Loop 每一轮都复用这同一个入口（`LoopPlanningAgent` 每轮调 `planner_input_builder.build(workingRequest)`
  再 `planner_agent.build_plan(...)`），没有因为搬闭环而把取数改回固定顺序；代价是每轮都重新取一次数，
  单轮耗时线性放大，与 Java 每轮重新取数的行为一致。

### 3. 工具内异常会被 ToolNode 吞掉

- Java：agent 抛 `TripPlanningException` 直接向上传到 `GlobalExceptionHandler`。
- Python：LangChain 的 ToolNode 会把工具异常转成 `ToolMessage(status="error")` 交给模型，异常本身不会冒泡。
- 处理：`data_tools.py` 里每个工具用 `try/except` 把异常暂存进 `context.errors`，builder 在模型跑完后调用
  `raise_planning_errors(context)` 优先重抛 `TripPlanningException`，从而保住 Java 的 400 文案（如"天气信息暂时不可用"）。

### 4. PlannerAgent 的第二个依赖

- Java：`PlannerAgent(RouteAgent, ReflectionAgent, LlmNarrationService)`，`ReflectionAgent` 包一层 `DailyPlanEvaluator`。
- Python：`PlannerAgent(route_agent, daily_plan_evaluator, llm_narration_service)`，
  `reflect_daily_plan()` 落在 `evaluator/daily_plan_evaluator.py`。本轮已建 `agent/reflection_agent.py`，
  它的 `reflect_daily_plan()` 只是回指 evaluator 的薄封装，Java `ReflectionAgent` 里服务于每日反思的
  4 个私有方法（`recommendAction`/`buildReason`/`buildReflectionNotes`/`translateAction`）不在这里重复一份。
- 保持不变的：输出里的文案与 trace 仍写 `ReflectionAgent` —— `planningNotes` 追加
  "ReflectionAgent 已对每日方案完成预算、路线、天气反思评估"，`agentTraces` 里 `agentName="ReflectionAgent"`、
  action="反思第N天候选方案"、status="accepted|need_revision"。
- 影响：`config/container.py` 里 `ReflectionAgent(daily_plan_evaluator)` 与 `PlannerAgent(..., daily_plan_evaluator, ...)`
  共用同一个 evaluator 实例，和 Java 的"两个 Bean 指向同一个 DailyPlanEvaluator"效果一致。

### 5. 响应体是 TripPlan，loop 两字段本轮起真实填充

- Java：`TripPlanController` 返回 `TripPlan`；`model/TripPlanResponse.java` 全项目无人引用（死类），未实现也未新建。
- Java：`TripPlan.loopSummary` / `loopIterations` 由 `LoopPlanningAgent.appendLoopResult` 填充，
  **loop 关闭时也会填充**：`plan()` 的第一分支调用 `appendLoopResult(tripPlan, List.of(), List.of(), 1, "loop-disabled")`，
  于是 `loopSummary = {enabled:false, roundsExecuted:0, selectedRound:1, stopReason:"loop-disabled"}`、
  `loopIterations` 为空数组，并且 `planningNotes` 照样追加"LoopPlanningAgent 已完成生成-评估-反思-修正闭环"等三句。
- Python：与上面一致，两个字段类型也从第一轮的 `dict` 占位改成 `LoopExecutionSummary` / `LoopIterationTrace`。
- 更正第一轮记录：第一轮本文曾把 loop-disabled 的 `stopReason` 写成"规划循环已关闭，使用单轮 PlannerAgent 生成结果"，
  源码里的实际值是 `"loop-disabled"`，按源码实现。

### 6. 四个 Amap client 的重复抓取代码合并

- Java：每个 `Amap*Client` 各自实现候选 URL（http/https 互转）、URL 编码、curl fallback、`status` 校验。
- Python：统一放 `clients/amap_http.py`，各 client 保留自己原有的错误前缀与日志文案，逐字对齐：
  景点 `log_scope=高德 POI` / 错误前缀 `高德景点检索`（curl 分支文案 `curl fallback 未获取到有效 POI 响应：`）；
  酒店 `高德酒店检索`（curl 分支 `高德酒店检索 curl fallback 未获取到有效响应：`）；
  天气按动作分两条 `高德城市编码解析` / `高德天气查询`；
  路线 `log_scope=高德` / 状态错误 `高德路线规划调用失败：` / `高德路线规划请求异常：`。
- 保留的行为：URL 参数编码规则（Spring 风格，`,` 不转义；布尔按 `"true"`/`"false"`）、
  `User-Agent: travel-assistant-java/1.0`（字符串原样照搬，未改成 python）、候选 URL 顺序、curl 兜底开关
  `AMAP_CURL_FALLBACK_ENABLED`、`/usr/bin/curl -s --connect-timeout 10 --max-time 20`、限流错误码判定后切规则路线。

### 7. 日志与异常文案里的 api-key 会被打码

- Java：失败日志与异常信息直接拼原始 URL，即 `key=<真实 key>` 会进日志和响应 message。
- Python：`clients/amap_http.py::_redact()` 把所有对外可见字符串里的 `key=` 值替换成 `key=***`。
- 另外一层：`httpx` 在 INFO 级会自己打印完整请求 URL（含 `key=<真实 key>`），Java 的 `RestTemplate` 默认不打这种行。
  实测本机 `logging.basicConfig(level=INFO)` 起服务时，一次请求会在日志里落 11 条带真实 key 的 URL，
  因此 `main.py` 把 `httpx` / `httpcore` 两个 logger 压到 WARNING。
- 这是有意偏离：源项目 AGENTS.md 要求凭据不得进入日志/提交信息。若必须逐字对齐 Java 文案，需要显式撤回本条。

### 8. Java 引用相等语义的两处移植

- `selectHotel`/`refineDailyPlan` 里 `cheaperHotel != workingHotel` 是对象引用比较，Python 用 `is not`
  （pydantic 模型是值相等，`!=` 会把"同价同名的另一家酒店"判成同一家）。
- Java `List.remove(Object)` 按引用删除，Python `list.remove` 按 `__eq__` 删除，所以统一走
  `_remove_by_identity()`（按 `id()` 删）。
- 同类：`workingAttractions.set(index, replacement)` 的原地替换保留为下标赋值。

### 9. 数值与日期语义

- `Math.round(double)` = `floor(x + 0.5)`（负数方向与 Python 内置 `round` 不同），统一用 `utils.jround`；
  一位小数用 `utils.round1`。
- `request.getBudget() / request.calculateDays()` 是整数除法，用 `//`。
- `LocalDate.plusDays()/toString()` -> `datetime.date` + `isoformat()`（`yyyy-MM-dd`）。
- haversine 半径 `6371.0`、`Math.toRadians` 逐字照搬。

### 10. LLM prompt 里的列表渲染

- Java 用 `List.toString()` 拼接 prompt，输出形如 `[美食, 自然风景]`。
- Python 直接 f-string 会变成 `['美食', '自然风景']`（带引号），会改变模型输入。
- 处理：`utils.java_list_to_string()` 复刻 `[a, b]` 格式，用在 `RequirementAnalysisService` 与 `LlmNarrationService`
  的 prompt 构造处（system prompt 文本块本身逐字照搬）。
- `PlanningDataAgent` 的 user prompt 是 Python 侧新增（Java 无对应物），不受本条约束。

### 11. 参数校验与错误响应

| 项 | Java | Python |
| --- | --- | --- |
| 校验触发 | `@Valid` + Jakarta 注解 | `TripPlanRequest` 的 `field_validator` + `validate_default=True`（JSON 缺字段等价 Jackson 留 null，同样报错） |
| 错误体 | `ApiErrorResponse{code,message,details,timestamp}` | 同一模型，字段名保持驼峰 |
| 校验失败 details | `field + ": " + defaultMessage`，逐条 FieldError | 同格式；pydantic 的 `"Value error, "` 前缀被剥掉；`loc` 去掉 `body` 前缀并把蛇形名转驼峰，保证出现 `startDate: ...` 而不是 `start_date: ...` |
| 一个字段多条约束同时违反 | 每个约束各产生一条 FieldError（如 `endDate` 同时报 NotBlank 和 Pattern） | pydantic 每字段只报第一条 |
| 请求体缺失/JSON 语法错 | 落 `Exception` 分支 -> 500 `INTERNAL_ERROR` | FastAPI 自己返回 422/400，不是 Java 的错误体 |
| 类型不匹配（如 budget 传字符串） | Jackson 抛错 -> 500 `INTERNAL_ERROR` | pydantic 归入校验失败 -> 400 `VALIDATION_ERROR` |
| `TripPlanningException` | 400 `TRIP_PLANNING_ERROR` + `["请调整输入条件后重试"]` | 一致 |
| 时间戳 | `LocalDateTime.now()`，Jackson 输出纳秒精度 | `datetime.now()`，输出微秒精度，都是无时区 ISO 串 |

### 12. 启动日志

- Java：`java.version=<JDK 版本>`，由 `ApplicationRunner` 打印。
- Python：同一条文案，`java.version` 改为 `python.version`（值取 `platform.python_version()`）；
  其余字段（各 provider 名、`amap.baseUrl`、`amap.mockResponse`、`amap.curlFallbackEnabled`、`amap.webJsEnabled`）
  一致。两边都只打印开关和地址，不打印 key。

### 13. CORS

- Java：`@CrossOrigin(origins = "*")` 只挂在 `TripPlanController` 上，不携带 credentials。
- Python：FastAPI 无控制器级注解，用全局 `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`，
  未开 `allow_credentials`（保持与 `Access-Control-Allow-Origin: *` 相同的语义）。
- 影响：作用域从单个 controller 扩到整个 app（当前 app 只有 `/api/trips/plan` 与 `/api/trips/adjust`，
  两者本来就同属 `TripPlanController`）。

### 14. 目录结构上的偏离（除已注明的合并/新增）

- `TravelKnowledgeService` 从 Java 的 `service` 包移到 Python 的 `clients/knowledge_base.py`：它的唯一调用方是
  `InMemoryAttractionClient`，留在 `service/` 会让 `clients` 反向依赖 `service`，装配时形成循环导入。
- `config/__init__.py` 不导出 `container` / `runtime_logger`，同样是为了避开
  `clients -> config -> container -> agent -> clients` 的循环导入。
- 其余包结构（agent/client/evaluator/service/model/controller/config）与 Java 一一对应。

### 15. 配置项取值：以 application.yml 生效值为准

Java 代码里的字段初值与 `application.yml` 不一致（yml 覆盖代码值）。Python 侧没有 yml，默认值取 **yml 的生效值**：

| 配置 | Java 代码初值 | application.yml | Python 默认 |
| --- | --- | --- | --- |
| `providers.{attraction,weather,hotel,route}` | `mock` | `amap` | `amap`（环境变量 `TRAVEL_PROVIDERS_*`） |
| `amap.baseUrl` | `https://restapi.amap.com` | `http://restapi.amap.com` | `http://restapi.amap.com`（`AMAP_BASE_URL`） |
| `amap.mockResponse` | `true` | `false` | `false`（`AMAP_MOCK_RESPONSE`） |
| `llm.enabled` | `false` | `${TRAVEL_LLM_ENABLED:false}` | `False`（`TRAVEL_LLM_ENABLED`） |
| `amap.apiKey` / `llm.apiKey` | `""` | `${AMAP_API_KEY:}` / `${TRAVEL_LLM_API_KEY:${DASHSCOPE_API_KEY:}}` | `""`，key 只从环境变量/`.env` 读，`resolved_api_key()` 复刻两级回退 |

其余项（`route-strategy`、`route-extensions`、`curl-fallback-enabled`、`poi-page-size`、`city-limit`、
`web-js-key`、`security-js-code`、`server.port`）与 yml 一致。`web-js-key`/`security-js-code` 自本轮起有消费方：
`api/map_controller.py` 把它们原样写进 `GET /api/map/config` 的响应体，`config/runtime_logger.py` 只取
`web-js-key` 的非空布尔；后端取数链路（四个 Amap client）仍然不读这两个键。

兜底开关的两层语义照搬：`providers.<source> == "amap"`（忽略大小写）选择高德实现，否则用规则/本地实现；
选中高德后 `amap.mockResponse=true` 直接走该 client 内置的兜底实现；未配 `AMAP_API_KEY` 抛
"已选择高德景点 provider，但尚未配置 travel.providers.amap.api-key"（其余三个源文案见各自文件）。

### 16. 批评提示词里的 JSON 排版

- Java：`LlmCriticAgent.buildUserPrompt` 用注入的 `ObjectMapper.writerWithDefaultPrettyPrinter()`，
  Jackson 的美化打印是 `"key" : value`（冒号前一个空格）+ 两个空格缩进。
- Python：`json.dumps(..., indent=2, ensure_ascii=False)`，冒号前没有空格。
- 影响：只影响送给模型的文本外观，不影响批评动作判定；两边字段集合与非 ASCII 处理方式一致
  （都保留中文原样、都包含 null 字段）。`tests/test_llm_critic_agent.py` 里的提示词断言按 Python 排版写。

### 17. Jackson `JsonNode.asText(default)` 的取值语义

- Java：`node.path("action").asText("STOP")` 对缺失字段返回默认值，对 JSON `null` 返回字符串 `"null"`
  （`NullNode.asText()`），对数字返回字面量，对对象/数组返回 `""` 后再回落默认值。
- Python：`llm_critic_agent.py` 的 `_path_as_text()` / `_node_as_text()` / `_read_string_list()` 复刻这几种取值。
- 影响：模型返回 `"reason": null` 这类畸形字段时两边输出一致；`notes` 里的空字符串同样被丢弃。

### 18. `LoopCritique.isShouldReplan()` 是 getter 还是方法

- Java：`isShouldReplan()` 是无字段的可读属性，`ObjectMapper` 若序列化 `LoopCritique` 会多出一个 `shouldReplan` 键。
- Python：实现成方法 `is_should_replan()`，不会出现在任何 JSON 输出里。
- 影响：无。源码中 `LoopCritique` 从不进 HTTP 响应（进响应的是 `LoopIterationTrace.shouldReplan` 这个真字段，
  Python 已按字段搬），也不进任何提示词。

### 19. 其他结构层面的对应关系

- Java `ReflectionAgent.TripPlanRequestView` 是嵌套 record，Python 是 `reflection_agent.py` 模块级
  `@dataclass TripPlanRequestView`，字段名 `requested_budget`，不进 JSON。
- `agent` 包不能在运行期导入 `service` 包（`service/__init__.py` 会导入 `trip_planning_service`，
  后者又要 `agent.loop_planning_agent`，形成循环导入），因此 `loop_planning_agent.py` 与
  `trip_planning_service.py` 对跨包类型只用 `TYPE_CHECKING` + 字符串标注。
- `LoopPlanningAgent` 的哨兵值 `Double.MAX_VALUE` 用 `float("inf")` 等价替代（四项严重度各被 `clampScore`
  压在 100 以内，总分上限 400，两种写法在 `candidateScore < bestScore` 判定上行为一致）。
- `LoopDecisionExecutor.ExecutionResult` 保持 Java 的嵌套类写法。
- `TripPlanningService` 的构造器：Java 有 4 个参数，其中 `plannerInputBuilder`、`plannerAgent` 在
  `planTrip` 和 `adjustTrip` 里都没有直接使用（`tripAdjustmentAgent` 只被 `adjustTrip` 用），
  Python 保留 `trip_adjustment_agent` 与 `loop_planning_agent` 两个。
- `TripPlanningService.adjustTrip` 的重新规划走 `planTrip(decision.updated_request)`，即第一轮的 Loop 规划入口
  `LoopPlanningAgent.plan`，没有另写一份规划逻辑，与 Java 的 `this.planTrip(...)` 一致。

### 20. `/adjust` 的嵌套请求对象：Java 不级联校验，pydantic 会级联

- Java：`model/TripAdjustmentRequest.java` 上 `originalRequest` 只有 `@NotNull`、**没有 `@Valid`**，
  `adjustmentPrompt` 只有 `@NotBlank`。Spring 的 `@Valid` 不级联，所以 `TripPlanRequest` 上的
  `@NotBlank city` / `@Pattern startDate` / `@NotNull budget` 等约束在 `/adjust` 这条路上**不会被触发**。
  后果：`originalRequest.startDate` 传空串时 Java 照样进 `adjustTrip`，第一个炸点在取数链路里——
  `AmapWeatherClient.getWeather` 第 58 行在 `try` 之外调用 `RuleBasedWeatherClient.getWeather`，
  后者第 25 行 `LocalDate.parse(query.getStartDate())` 抛 `DateTimeParseException`（`TripPlanRequest.calculateDays()`
  里同样的解析更晚才会被调用），由 `GlobalExceptionHandler` 的 `Exception` 分支返回
  **500 `INTERNAL_ERROR` + `["DateTimeParseException"]`**；
  `city` 传空串则连异常都没有，只有 `AmapHotelClient.isHotelInRequestedCity` 因
  `normalizedRequestedCity.isBlank()` 返回 true 而放弃城市过滤，返回一份跨城酒店的计划。
- Python：`TripAdjustmentRequest.original_request` 是带约束的 pydantic 模型，FastAPI 在校验时**会级联**进内层字段，
  返回 **400 `VALIDATION_ERROR`**，details 用点号路径（如 `originalRequest.city: must not be blank`）。
  这是错误码、错误体结构和"能否进入规划链路"三处同时不同。
- 处理：接受 Python 侧的级联校验。Java 那种"非法内层请求进到底层再炸 500"的形状是缺 `@Valid` 的疏漏，
  若要逐字对齐，就得在 Python 里绕过内层校验（`model_construct`），而 `copy_request()` 本身走带约束的构造器，
  绕过后仍会在下一处报错，等于把 400 换成 500。因此这里刻意不对齐，并在
  `models/trip_adjustment_request.py` 的 docstring 里就地标注。
- 相同的部分：`originalRequest` 缺失/为 JSON null -> 两边都是 400 `VALIDATION_ERROR`，
  details 含 `originalRequest: must not be null`；`adjustmentPrompt` 缺失或全空白 -> 两边都是
  `adjustmentPrompt: must not be blank`（Java `@NotBlank` 对 null 同样报 "must not be blank"）。

### 21. 预算正则里的 `\d`

- Java：`Pattern.compile("(\\d{3,6})")` 默认不加 `UNICODE_CHARACTER_CLASS`，`\d` 只匹配 ASCII `0-9`。
- Python：`\d` 默认匹配 Unicode 十进制数字，全角 `３０００` 也会被识别成预算并改写入请求。
- 处理：Python 侧写成 `re.compile(r"([0-9]{3,6})")`，按 Java 收窄；"预算改成３０００" 两边都不改预算。
- 影响范围：仅 `TripAdjustmentAgent.applyByRules` 的预算分支；loop 侧的预算来自请求体 `budget` 字段，不过这个正则。

### 22. 静态资源挂载顺序

- Java：Spring Boot 把 `src/main/resources/static/` 挂在根路径上，控制器映射优先于静态资源，
  因此 `/api/trips/plan`、`/api/map/config` 不会被同名静态目录抢走；`/` 由欢迎页解析到 `static/index.html`。
- Python：`StaticFiles` 是 `Mount`，Starlette 按注册顺序匹配路由，先注册的优先。`main.py` 因此把
  `app.mount("/", StaticFiles(directory=..., html=True))` 放在 `include_router(trip_router)`、
  `include_router(map_router)` **之后**，效果与 Spring 一致。
- 验证：`app.routes` 实测顺序为 `/openapi.json`、`/docs`、`/docs/oauth2-redirect`、trips 路由、map 路由、
  最后的 `Mount("/")`；`tests/test_map_config_controller.py::test_should_serve_index_html_from_root_and_keep_api_routes_ahead_of_static`
  同时断言 `GET /` 返回页面 HTML、`POST /api/trips/plan` 非法体仍返回 `VALIDATION_ERROR` 错误 JSON（不是 HTML）、
  `GET /api/map/config` 返回 200。
- 差异：Spring 的静态资源默认带 `Cache-Control`/`Last-Modified` 与 `**/*.html` 的模板解析，`StaticFiles` 只提供
  文件本身与 `ETag`/`Last-Modified`；页面是单个 `index.html`，Java 侧也没有模板，行为等价。

### 23. `GET /api/map/config` 是响应体里回传密钥的端点

- Java：`MapConfigController` 把 `amap.web-js-key` 原样放进 `jsApiKey` 字段返回给浏览器（前端 SDK 加载地图需要），
  `security-js-code` 同样无条件透传；`enabled=false` 时 `jsApiKey` 回落成空串。
- Python：逐字照搬，包括 `enabled ? webJsKey : ""` 与 `securityJsCode` 的无条件透传（未配置时返回 `null`）。
- 边界：这是全项目唯一一个把凭据写进响应体的端点，与 Java 一致，未额外加脱敏。密钥仍然只从环境变量/`.env` 读，
  不进代码、注释、README、报告与日志；`runtime_logger` 只打印 `webJsEnabled` 布尔。

---

## 四、刻意保留的源项目行为（不是移植缺陷，未修）

- `RuleBasedHotelClient` 的酒店坐标写死为 `116.42, 39.93`（Java 源码原样），因此 `providers.*=mock` 时
  规则路线里会出现上千公里的路程和很大的 `totalTransportation`。Java 侧同样如此。
- `RouteEvaluator.evaluate(...)` 收到 `currentWeather` 后不参与判定（Java 里就没用这个参数），Python 保留形参。
- 预算分档阈值（`budgetVeryTight<=260`、`budgetTight<=420`、`premiumBudget>=700`、`luxuryBudget>=1100`、
  `budgetCritical<380` 等）、酒店预算惩罚分档、`adjustRounds >= 6` 上限、`<=3` 景点数下限、
  `35/25/30` 公里聚类与替换阈值，全部按 Java 数值保留。
- `sanitizeAttractions` 的餐饮/非法 POI 关键词黑名单、POI 类型映射、默认游览时长与票价（180/150/90/120 分钟，
  60/50/0/30 元）按 Java 原样。
- 两处 `copyRequest` 的净化行为不对称：`RequestOptimizationStrategyService.copyRequest` 会调
  `sanitizePersistedExtraRequirements`（按 `[；;\n]` 切句、丢掉命中 23 个调整关键词的子句，全空则置 null），
  `LoopPlanningAgent.copyRequest` 直接新建对象、不净化。因此同一份 `extraRequirements` 走 loop 的两条复制路径结果不同：
  经过 `LoopDecisionExecutor` 的那份会丢掉"预算/节奏/慢一点"之类表述，`requestSummary` 与 `requestSignature`
  读到的仍是原文。两个方法都照搬，未统一。
- `ReflectionAgent.recommendTripAction` 的 `UPGRADE_EXPERIENCE` 一支只判 `experienceSeverity > 0`，
  不参与四项比大小（另外三支都比）；`LlmCriticAgent.critiqueByRules` 同位置用的是 `experienceTooConservative` 布尔。
  两边分支顺序一致、条件形状不同，按 Java 各自保留。
- `LlmCriticAgent` 里 `reflection == null` 的兜底分支：四个严重度全取 0，此时除 `experienceTooConservative`
  （从 `plan.budget` 与 `request.budget` 反算）之外只能落到最后的 `STOP`。Java 的 `LoopPlanningAgent` 总是传非 null
  reflection，这段在原项目里同样不被走到，Python 原样保留未删。
- `LoopPlanningAgent.buildRequestSummary` 只对 `transportation == null` 回落成"智能推荐"，`city`/`budget`/`accommodation`
  为 null 时直接拼出字符串 `"null"`（Java 字符串拼接语义），空白串也不替换。Python 用 `_java_concat` 复刻。
- `TripAdjustmentAgent` 规则解析里"到底比较哪个串"是混用的，Python 逐处照搬：
  `normalizedPrompt` 只 `trim()`、`lowerPrompt` 再 `toLowerCase()`。中文关键词两边行为相同，
  但用哪个串是按 Java 一行一行来的：`applyBudgetAdjustment` 的门禁 `"预算"` 与数字正请用 `normalizedPrompt`、
  同方法里"省一点/便宜点/控制预算"用 `lowerPrompt`；`updatePreference` 的 `wantsRemove` 用 `normalizedPrompt`、
  `轻松节奏` 特例用 `lowerPrompt`（第 303 行那组关键词与特例那组并不相同：特例少了"别太赶"）；
  `applyTransportationAdjustment` 的"智能推荐 / 你来安排交通"、`applyAccommodationAdjustment` 的"客栈"用 `lowerPrompt`，
  同方法其余档位用 `normalizedPrompt`。未统一。
- `applySharedAction`（loop 与 adjust 共用的四个统一策略动作）命中**第一个**关键词就 `return`，
  因此一条 prompt 里同时写"路线紧凑"和"住好一点"时，`appliedChanges` 只会有第一条"已应用统一…策略"。
  并且 `applySharedStrategyActions` 是本轮最后一个分支，它追加的备注排在其它文案之后。原样保留。
- `adjustByLlm` 的 `transportation` 判断用 `"智能推荐".equals(transportation)` 精确等值，
  命中时把字段清成 null 并记 "LLM 已改为交通方式智能推荐"；`"智能推荐 "`（带空格）走的是下面那条
  `LLM 已调整交通方式为 …`。Python 保持同样的两条分支与顺序。
- `appliedChanges` 会出现在响应体的**两个**位置：`TripAdjustmentResponse.appliedChanges`，以及
  `tripPlan.planningNotes` 末尾（`adjustTrip` 先追加"改计划解析来源：…"和"本次改计划要求：…"两句，
  再 `addAll(appliedChanges)`）。Java 就是同一份列表两处引用，Python 用 `list(...)` 复制后追加，
  内容一致但不共享对象。
- `TripAdjustmentAgent.isAdjustmentInstruction` 的 23 个关键词与
  `RequestOptimizationStrategyService` 里那份**是同一段代码的两次抄写**（Java 原项目本身就抄了两份），
  Python 保留两份未合并。两份目前内容一致；若日后只改一处，两边的净化结果会分叉。
- 空白 prompt 时两条路径的兜底文案不同：规则路径是"已保留原始规划条件，并记录新的调整要求"，
  LLM 路径多两个前缀字"LLM 已保留原始规划条件，并记录新的调整要求"。各自按 Java 保留。
- `LLM 解析失败，已自动回退到规则解析` 这条备注是 `catch` 里**追加到列表末尾**的，
  所以它排在规则路径已经产生的所有文案之后，而不是排在第一条。
  `tests/test_trip_adjustment_agent.py::test_shouldFallbackToRulesAndMarkSourceWhenLlmResponseIsNotJson`
  按这个顺序断言。
- `MapConfigController` 的两个文案分支按 Java 原样：`enabled` 时 `message="高德地图前端配置已就绪"`，
  否则 `"尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示"`；`provider` 恒为 `"amap-jsapi"`。
  `securityJsCode` 不看 `enabled` 也无条件透传（未配置时为 `null`），只有 `jsApiKey` 会被清成空串。
- 前端页面 `index.html` 里的"出发城市"输入框（`required`，默认值"上海"）**不进请求体**：
  `buildTripPayload()` 不读它，Java 页面同样如此。搬过来时未补这个字段，也未把 `required` 去掉。
- 页面对 `enabled=false` 的处理是渲染 `map-placeholder`（"真实地图待开启"），不会去加载高德 SDK，
  因此没有 JS key 时地图区域只有文字占位、没有 Marker 和 Polyline。这是 Java 页面的既有分支，未加假底图。

---

## 五、验证

```
.venv/bin/python -m pytest -q
```

共 55 条用例（`55 passed`）。

- `tests/test_planner_agent.py` 对应 `PlannerAgentTest.java`（断言逐条照搬，含
  "ReflectionAgent 已对每日方案完成预算、路线、天气反思评估" 与 `agentName=ReflectionAgent`）。
- `tests/test_trip_plan_controller.py` 对应 `TripPlanControllerTest.java`（`@MockBean` 换成
  `app.dependency_overrides[get_trip_planning_service]`，断言 `overallSuggestions` /
  `days[0].transportation` / `budget.total` / `loopSummary` 四个字段 / `loopIterations[0]` 的
  `requestSummary`、`reflectionAction`、`appliedChanges`、`selectedAsBest`，以及 400 的 `VALIDATION_ERROR` 结构）。
  本轮追加 3 条 `/api/trips/adjust` 用例：200 的响应体键集合恰为
  `{tripPlan, updatedRequest, appliedChanges}`（Java `TripAdjustmentResponse` 只有这三个字段，
  `adjustmentSource` 不进响应体）、`updatedRequest` 反映调整后的预算/住宿/补充要求、
  `tripPlan.loopSummary` 由重新规划链路真实填充；以及缺 `originalRequest`、`adjustmentPrompt` 全空白
  两条 400 details 文案。
- Java 侧没有 loop 的测试类，因此 loop 的断言全部取自 Java 源码的真实分支与文案：
  `tests/test_reflection_agent.py`（严重度公式、`overallScore<=15` 可接受阈值、四个动作优先级与 `reason` 文案）、
  `tests/test_llm_critic_agent.py`（六个批评分支、`normalizeAction` 的非法值回落 STOP、Jackson 取值语义、提示词三段 JSON）、
  `tests/test_loop_planning_agent.py`（八条闭环分支：首轮通过 / loop 关闭 / 重规划一轮后选中第 2 轮 /
  批评拒绝重规划 / 连续无改善 / 达到最大轮次 / 无可执行调整 / 检测到重复请求，含 trace 顺序与
  `appendLoopResult` 的三句备注）、
  `tests/test_request_optimization_strategy_service.py`（四分支 `applyAction` 文案、`mergeExtraRequirement`
  的"已含则跳过"、`copyRequest` 的净化差异）。
- Java 侧 `src/test` 里没有任何 adjust 相关测试类（只有 `PlannerAgentTest.java` 与 `TripPlanControllerTest.java`），
  因此 `tests/test_trip_adjustment_agent.py` 的 10 条断言全部取自 `TripAdjustmentAgent.java` 与
  `TripPlanningService.java` 源码的真实分支与文案：
  规则路径 6 条（预算+住宿档位+移除偏好、打车+民宿+"路线紧凑"走统一策略、"祝旅途愉快"这类补充限制进
  `extraRequirements`、空白 prompt 落"已保留原始规划条件，并记录新的调整要求"、解析出的预算与原值相等时不记备注、
  `adjustmentPrompt=null` 不抛异常）；
  LLM 路径 3 条（结构化 JSON 产生的六句 "LLM 已…" 文案与顺序、system/user prompt 首尾逐字断言、
  畸形字段按 Jackson 取值语义处理、模型返回非 JSON 时 `adjustmentSource=rule-fallback`
  且回退备注在列表末尾）；
  再加 1 条用 stub agent 验证 `adjustTrip` 复用 `planTrip` 入口以及 `planningNotes` 追加的五句顺序。
- `providers.*=mock` 的整链路（HTTP 200 + 驼峰字段 + 预算/路线/天气结构）已用临时脚本跑通。
- 真实链路已在 2026-09-30 用本机密钥跑通（密钥只在 `.env`，未进代码与文档；这一条是 loop 未搬时的验证）：
  高德四源分别返回景点 16 个（`amap-poi-search`）、酒店 10 家（`amap-hotel-search`）、
  预报内天气 `source=amap-live`、路线 `高德路线已生成`；预报区间外日期按 Java 逻辑落 `source=rule-fallback`
  并写 `高德未返回该日期天气，已使用规则天气补全`；`POST /api/trips/plan` 两次（杭州 3 天 / 苏州 2 天）返回 200，
  其中一次出现 `need_revision` 触发 `refineDailyPlan` 的省预算调整，另一次 `within_budget`。
- Loop 闭环的真实请求验证（2026-09-30，`SERVER_PORT=8123`，四源 `amap` + 真实模型，密钥只在 `.env`）：
  杭州 3 天 / 预算 3000 一次得到 `loopSummary={enabled:true, roundsExecuted:2, selectedRound:1,
  stopReason:"检测到循环调整重复，保留当前最优方案"}`，`loopIterations` 两条、`selectedAsBest` 为
  `[true, false]`，批评来源 `llm`、动作 `REPLACE_OUTDOOR_WITH_INDOOR`；
  苏州 3 天 / 预算 1200 / 高端酒店 / 打车 一次得到 `roundsExecuted:3, selectedRound:2`，
  逐轮 `requestSummary` 显示修正真的作用到下一轮输入（住宿 高端酒店→舒适型酒店→经济型酒店、交通 打车→公共交通），
  `budgetSeverity` 75→56、`reflectionScore` 125→106，第 3 轮因请求与第 2 轮同签名而落到
  "检测到循环调整重复"。两次响应的 `planningNotes` 末尾三句与 Java `appendLoopResult` 逐字一致。
  服务日志 47 行里 `key=` 出现 0 次、两把真实密钥出现 0 次（`restapi.amap.com` 只在启动配置行出现一次，不带参数）。
- `/adjust` 的真实请求验证（2026-09-30，`SERVER_PORT=8123`，四源 `amap` + 真实模型，密钥只在 `.env`）：
  `adjustmentPrompt="预算改成6000，住经济型酒店，不要自然风景这类景点"`，`originalRequest` 为杭州 3 天 / 3000 元 /
  舒适型酒店 / 公共交通 / 偏好 `[美食, 自然风景]` / 补充要求"希望整体节奏轻松一些"。
  **HTTP 200，`time_total=66.795s`，响应 22647 字节**。走的是 LLM 解析路径，
  `appliedChanges` = `["LLM 已将预算调整为 6000 元", "LLM 已调整住宿偏好为 经济型酒店", "LLM 已移除偏好：自然风景"]`；
  `updatedRequest` 为 `budget=6000`、`accommodation="经济型酒店"`、`preferences=["美食"]`、
  `transportation="公共交通"`（未改）、`extraRequirements=null`（`copyRequest` 的净化按 23 个关键词丢掉了
  "希望整体节奏轻松一些"，第四节已有记录）；响应体键集合恰为 `{tripPlan, updatedRequest, appliedChanges}`。
  `tripPlan.planningNotes` 共 20 条，末尾五条依次为
  `改计划解析来源：llm`、`本次改计划要求：预算改成6000，住经济型酒店，不要自然风景这类景点`、
  以及上面三句 `appliedChanges`（同一份文案在响应体两处出现，与 Java 一致）。
  重新规划走的是搬好的 Loop 入口：`loopSummary={enabled:true, roundsExecuted:3, selectedRound:3,
  stopReason:"第 3 轮通过规则评估，直接停止"}`，逐轮 `requestSummary` 显示 `adjustmentPrompt` 解析出的
  经济型酒店进入第 1 轮输入、随后被 loop 的 `UPGRADE_EXPERIENCE` 依次上调为舒适型→高端，
  总花费 2319→3219→5019、`remainingBudget` 3681→2781→981，第 3 轮 `reflectionScore=0` 被选中。
  服务日志 30 行里 `key=` 出现 0 次、`api-key` 出现 0 次、两把真实密钥各出现 0 次，
  `restapi.amap.com` 只在启动配置行出现一次且不带参数；无 WARNING/ERROR 行。
- `/adjust` 的级联校验分支同次实测：`originalRequest` 内层字段全填非法值时返回
  **400 `VALIDATION_ERROR`**，details 为点号路径
  （`originalRequest.city: must not be blank` 等 6 条），对应第三节第 20 条记录的行为差异；
  `transportation`/`extraRequirements` 传空串不报错，与 Java 的 `@Nullable` 语义一致。
- Java 侧没有 MapConfig 的测试类，`tests/test_map_config_controller.py` 的 4 条断言取自
  `MapConfigController.java` 的两个分支：`enabled=true` 时响应体键集合恰为
  `{enabled, jsApiKey, securityJsCode, provider, message}` 且 `jsApiKey` 原样透传（测试用假值，不读 `.env`）、
  未配置与纯空白两种输入都落 `enabled=false` + `jsApiKey=""`（`securityJsCode` 仍按 Java 无条件透传）、
  以及静态挂载不遮 `/api/` 路由（见第三节第 22 条）。
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
  按 Java 既有的限流保护自动回退规则路线、不影响整体 200；日志里 `key=` 出现 0 次、两把真实密钥出现 0 次。
- 前端页面的浏览器验收（2026-09-30，真实 Chrome 打开 `http://localhost:8123/`，四源 `amap` + 真实模型）：
  页面自己组装出的请求体为
  `{"city":"杭州","startDate":"2026-09-30","endDate":"2026-10-02","budget":3000,"preferences":["自然风景","美食","轻松节奏"],"transportation":"公共交通","accommodation":"舒适型酒店","extraRequirements":""}`
  （界面上的"自然风光/美食/休闲"三个复选框，在 `buildTripPayload()` 里映射成"自然风景/美食/轻松节奏"，
  Java 页面同一份代码，未改）。
- 接口状态码：第一次 `POST /api/trips/plan` 返回 **400**，服务端文案
  `高德景点检索失败：CUQPS_HAS_EXCEEDED_THE_LIMIT（infocode=10021）` —— 外部高德个人 key 的每秒调用配额被打满，
  不是移植缺陷（同一份请求重试即 **200**，本轮共 3 次 400 / 1 次 200）；页面渲染结果后自行发起的
  `GET /api/map/config` 返回 **200**；根路径 `GET /` 返回 200 `text/html`。
- 字段对账：页面 JS 读取的 13 个数据结构（TripPlan / DayPlan / Attraction / Hotel / Meal / RoutePlan /
  RouteStep / Budget / WeatherInfo / AgentTrace / LoopExecutionSummary / LoopIterationTrace / MapConfigResponse）
  逐字段与 Python 响应体吐出的驼峰键比对，**页面读取但后端不吐的字段 0 条**，因此 `index.html` 零改动拷贝，
  既没改前端也没改后端序列化。
- 地图区结论（当次 `.env` 两项为空时）：**卡在缺 `AMAP_WEB_JS_KEY` / `AMAP_SECURITY_JS_CODE`**。
  `GET /api/map/config` 返回 `enabled=false`，页面走 Java 的 disabled 分支，`.map-box` 内只有
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

## 六、移植基线之外的能力扩展（分支 `feature/ask-agent`）

本节内容**不是 Java→Python 的差异**，而是源项目里根本不存在的能力，因此不计入第三节的对照表：

1. `POST /api/trips/ask`：方案生成后的只读追问 agent。Java 侧没有任何对应类或端点。
2. 新增文件：`models/ask_request.py`、`models/ask_response.py`、`agent/ask_context.py`、
   `agent/ask_tools.py`、`agent/ask_agent.py`、`api/ask_controller.py`、`tests/test_ask_agent.py`、
   `docs/ASK_AGENT_DESIGN.md`；改动 `main.py`、`config/container.py`、两个 `__init__.py` 与 `static/index.html`。
3. 对既有链路的影响：`/api/trips/plan`、`/api/trips/adjust`、`/api/map/config` 的语义、响应体与错误分支
   一字未改，原有 55 条用例未作修改且仍全绿（合并后共 62 条）。
4. 唯一一处会破坏"与 Java 一致"的地方是前端页面加了追问区块，因此该一致性声明只在 `main` 分支上成立。
5. 设计与逐题验证：见 `docs/ASK_AGENT_DESIGN.md` 第 11 节（真实 10 题全 200、单题 1.3~3.2 秒、
   护栏命中与留痕形状均有记录）。

# travel-assistant-py（Java 智能旅游助手的 Python + LangChain 等价移植）

源项目：`/Users/lwl/Documents/智能旅游助手-1`（Spring Boot 3 / Java 17，只读，未做任何修改）。
本项目只做语言与框架替换：FastAPI 顶掉 Spring Web，pydantic 顶掉 POJO + Jakarta Validation，
httpx 顶掉 RestTemplate，LangChain `create_agent` + `@tool` 顶掉 `PlannerInputBuilder` 里的固定顺序取数。
业务规则、阈值、文案、字段名一律照搬 Java 源码。
Java 侧的三个接口端点（`/api/trips/plan`、`/api/trips/adjust`、`/api/map/config`）、前端页面与静态资源挂载已全部搬完。

> ## 取数链路有两条路径
>
> 配了可用模型（`TRAVEL_LLM_ENABLED=true` 且 api-key / base-url / model 都非空）时，
> 候选景点 / 出行天气 / 候选酒店由 `create_agent` 驱动的模型自主选工具获取；
> 没有可用模型时自动退回 Java `PlannerInputBuilder` 的固定顺序（景点 → 天气 → 酒店），
> 需求解析与结果文案走规则兜底（`需求理解来源：rule`、`结果文案来源：规则文案`），
> 与 Java 关掉 `travel.llm.enabled` 的行为一致 —— 不配模型也能出完整行程。
> 详见 `MIGRATION_NOTES.md` 第三节第 1 条。

## 1. 安装

```bash
cd travel-assistant-py
uv venv .venv
uv pip install -p .venv/bin/python --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
```

直连 pypi 超时时用上面的阿里云镜像；已有 `.venv` 可跳过第一行。

## 2. 配置

```bash
cp .env.example .env   # 然后填 TRAVEL_LLM_API_KEY 与 AMAP_API_KEY（要出真实地图再填 AMAP_WEB_JS_KEY / AMAP_SECURITY_JS_CODE）
```

配置项来自环境变量或本地 `.env`，**不要**把 key 写进代码、README 或提交信息（`.env` 也别提交）。
`TRAVEL_LLM_API_KEY` 不填时会回退读 `DASHSCOPE_API_KEY`（与 Java 的
`${TRAVEL_LLM_API_KEY:${DASHSCOPE_API_KEY:}}` 一致）。模型端点默认是 DashScope 的
OpenAI-Compatible 地址 + `qwen-plus`（与 Java 相同），但走的是通用 OpenAI 协议：
把 `TRAVEL_LLM_BASE_URL` / `TRAVEL_LLM_MODEL` 换成任意 OpenAI-Compatible 端点即可，
本机实测用 DeepSeek 端点（`base_url` 已带 `/v1` 时不会重复追加）跑通全链路。

| 环境变量 | Java `application.yml` | 默认值 |
| --- | --- | --- |
| `TRAVEL_LLM_ENABLED` | `travel.llm.enabled` | `false`（置 `true` 才走模型自选工具，否则按 Java 固定顺序纯规则取数） |
| `TRAVEL_LLM_BASE_URL` | `travel.llm.base-url` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `TRAVEL_LLM_API_KEY` / `DASHSCOPE_API_KEY` | `travel.llm.api-key` | 空 |
| `TRAVEL_LLM_MODEL` | `travel.llm.model` | `qwen-plus` |
| `TRAVEL_LLM_TEMPERATURE` | `travel.llm.temperature` | `0.2` |
| `TRAVEL_PROVIDERS_ATTRACTION/WEATHER/HOTEL/ROUTE` | `travel.providers.*` | `amap` |
| `AMAP_API_KEY` | `travel.providers.amap.api-key` | 空 |
| `AMAP_BASE_URL` | `travel.providers.amap.base-url` | `http://restapi.amap.com` |
| `AMAP_MOCK_RESPONSE` | `travel.providers.amap.mock-response` | `false` |
| `AMAP_ROUTE_STRATEGY` / `AMAP_ROUTE_EXTENSIONS` | `route-strategy` / `route-extensions` | `0` / `base` |
| `AMAP_CURL_FALLBACK_ENABLED` | `curl-fallback-enabled` | `true` |
| `AMAP_POI_PAGE_SIZE` / `AMAP_CITY_LIMIT` | `poi-page-size` / `city-limit` | `10` / `true` |
| `AMAP_WEB_JS_KEY` / `AMAP_SECURITY_JS_CODE` | `amap.web-js-key` / `amap.security-js-code` | 空。只被 `GET /api/map/config` 读，用于前端地图 SDK；为空时页面显示占位提示 |
| `TRAVEL_LOOP_ENABLED` | `travel.loop.enabled` | `true` |
| `TRAVEL_LOOP_MAX_ROUNDS` | `travel.loop.max-rounds` | `3` |
| `TRAVEL_LOOP_MAX_NO_IMPROVEMENT_ROUNDS` | `travel.loop.max-no-improvement-rounds` | `2` |
| `SERVER_PORT` | `server.port` | `8080` |

### 兜底开关的两层语义（与 Java 一致）

1. `TRAVEL_PROVIDERS_<SOURCE>=amap` 走高德实现，其他任意值走规则/本地兜底实现（大小写不敏感）。
2. 选中高德后：`AMAP_MOCK_RESPONSE=true` 直接返回兜底实现的数据；未配 `AMAP_API_KEY` 抛
   "已选择高德X provider，但尚未配置 travel.providers.amap.api-key"；请求失败或结果为空时按各自源的规则
   兜底（酒店/天气/路线回退规则实现，景点回退本地知识库，POI/天气缺某天用规则补全，
   路线限流自动切规则估算）。景点/酒店的兜底与 Java 一样是静默的，只在 summary 与 `provider` 字段里体现来源。

## 3. 启动

```bash
PYTHONPATH=. .venv/bin/python -m travel_assistant.main
# 或者：.venv/bin/python -m uvicorn travel_assistant.main:app --host 0.0.0.0 --port 8080
```

启动日志与 Java 的 `TravelRuntimeLogger` 同一条文案（只打印 provider、base-url 和开关，不打印 key）。
起服务后浏览器直接打开 `http://localhost:8080/` 就是搬过来的前端页面（`travel_assistant/static/index.html`，
与 Java 的 `src/main/resources/static/index.html` 字节级一致）。

## 4. 接口

`POST /api/trips/plan`，请求体与响应体字段名保持 Java 的驼峰风格。

```bash
curl -X POST http://localhost:8080/api/trips/plan \
  -H "Content-Type: application/json" \
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

请求字段：`city`、`startDate`、`endDate`（`yyyy-MM-dd`）、`budget`（`>=1`）、`preferences`（非空数组）、
`transportation`（可空，为空时 PlannerAgent 自行决定）、`accommodation`、`extraRequirements`（交给
`RequirementAnalysisService` 解析成约束）。另有三个 Java 没有的可空字段（见
[MIGRATION_NOTES.md](MIGRATION_NOTES.md) 第七节）：`hotelAnchor`（期望住宿位置，如「西湖」）、
`dayHotelAnchors`（`{天数下标: 地名}`，只影响某一天）、`hotelContinuity`（`strict` 表示整趟住同一家，
留空即各天最优）。三个都不填时，逐天酒店与通勤与 Java 完全一致。
日期若超出高德天气预报窗口（本机实测约 4 天），对应天会按 Java 同款逻辑逐天落 `source=rule-fallback`
并写 `高德未返回该日期天气，已使用规则天气补全`，这不是错误。

响应体是 Java 的 `TripPlan`：`city`、`startDate`、`endDate`、`days[]`（含 `hotel`、`attractions[]`、
`meals[]`、`routePlan`、`estimatedCost`、`optimizationNotes`）、`weatherInfo[]`、`overallSuggestions`、
`budget`、`planningNotes[]`、`agentTraces[]`、`loopSummary`、`loopIterations[]`。

`loopSummary` / `loopIterations` 由 `LoopPlanningAgent` 填充，与 Java 一样在 `travel.loop.enabled=false` 时
也非 null（此时 `loopSummary.enabled=false`、`roundsExecuted=0`、`stopReason="loop-disabled"`、`loopIterations` 为空数组）。
Loop 是"生成 -> 反思评分 -> 批评 -> 修正输入 -> 再生成"的多轮闭环，每轮都复用上面那条 `create_agent` 取数链路：
`agentTraces` 前面会插入逐轮的 `LoopPlanningAgent` / `ReflectionAgent` / `LlmCriticAgent` / `LoopDecisionExecutor` 轨迹，
`planningNotes` 末尾追加三句（`LoopPlanningAgent 已完成生成-评估-反思-修正闭环`、
`LoopPlanningAgent 选定第 N 轮作为当前最优方案`、以及本次的停止原因）。
四个调整动作字符串照搬 Java：`REDUCE_BUDGET` / `COMPRESS_ROUTE` / `REPLACE_OUTDOOR_WITH_INDOOR` /
`UPGRADE_EXPERIENCE` / `STOP`。批评阶段 LLM 失败时按 Java 回退规则批评，并写
`LLM 批评阶段失败，已回退到规则批评`。

错误体与 Java 同构：`{code, message, details[], timestamp}`。

| 场景 | HTTP | code |
| --- | --- | --- |
| 参数校验失败 | 400 | `VALIDATION_ERROR` |
| 规划约束冲突、数据源不可用、未配置模型 | 400 | `TRIP_PLANNING_ERROR` |
| 其他异常 | 500 | `INTERNAL_ERROR` |

`POST /api/trips/adjust` 对应 Java 的 `TripPlanController.adjustTrip`：把自然语言改计划要求解析成新的请求副本，
再用这份副本重新走一遍上面的规划链路（含 Loop 闭环），不另写一份规划逻辑。

```bash
curl -X POST http://localhost:8080/api/trips/adjust \
  -H "Content-Type: application/json" \
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

请求字段：`originalRequest`（`@NotNull`，结构同上）、`adjustmentPrompt`（`@NotBlank`）。
响应体是 Java 的 `TripAdjustmentResponse`，只有三个字段：`tripPlan`（重新规划出的完整 `TripPlan`，
字段与上面 `/plan` 相同）、`updatedRequest`（解析后的新请求副本）、`appliedChanges[]`。
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

`GET /api/map/config` 对应 Java 的 `MapConfigController`，是给前端页面加载高德 JS SDK 用的：

```bash
curl http://localhost:8080/api/map/config
# {"enabled":false,"jsApiKey":"","securityJsCode":null,"provider":"amap-jsapi",
#  "message":"尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示"}
```

响应体是 Java 的 `MapConfigResponse` 五个字段：`enabled`（`AMAP_WEB_JS_KEY` 非空白才为 `true`）、
`jsApiKey`（`enabled=true` 时原样透传该 key，否则空串）、`securityJsCode`（无条件透传
`AMAP_SECURITY_JS_CODE`，未配置为 `null`）、`provider`（恒为 `amap-jsapi`）、`message`
（`高德地图前端配置已就绪` / 上面那句占位提示）。这是全项目唯一把凭据写进响应体的端点，与 Java 一致；
两个值只从环境变量或 `.env` 读，不进代码、日志与文档。

前端页面 `travel_assistant/static/index.html` 从 Java 的 `src/main/resources/static/index.html` 原样拷贝
（2905 行，接口地址与字段名无需适配），由 `main.py` 的 `StaticFiles(html=True)` 挂在根路径，
注册在所有 `include_router` 之后，因此不遮 `/api/` 路由。页面依赖 `/api/trips/plan`、`/api/trips/adjust`、
`/api/map/config` 三个接口；`enabled=false` 时地图区渲染"真实地图待开启"文字占位、不加载高德 SDK，
`.env` 里填上 `AMAP_WEB_JS_KEY` 与 `AMAP_SECURITY_JS_CODE`（必须是「Web端(JS API)」类型，与后端那把
「Web服务」key 不通用）之后才会出真底图、Marker 与当日路线 Polyline。
`main` 分支上这份页面与 Java 源文件字节级一致；`feature/ask-agent` 分支给它加了"方案追问"区块，见下一段。

`POST /api/trips/ask` 是**移植基线之外的能力扩展**（Java 无对应物，只在 `feature/ask-agent` 分支上）：
方案生成后，前端把上一次 `/plan` 的响应体与原始请求一起回传，agent 用只读工具回答关于这份方案的追问；
判出"其实想改"时也只产出一句话，**要用户点确认才发给 `/adjust`**，agent 自己没有任何写权限。

```bash
curl -X POST http://localhost:8080/api/trips/ask \
  -H "Content-Type: application/json" \
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
7. 设计、护栏与逐题验证记录在 [`docs/ASK_AGENT_DESIGN.md`](docs/ASK_AGENT_DESIGN.md)，
   改方案那一轮的设计在 [`docs/ADJUST_AGENT_DESIGN.md`](docs/ADJUST_AGENT_DESIGN.md)。

## 5. 目录

```
travel_assistant/
  main.py                 FastAPI 装配 + CORS + StaticFiles 挂载 + 启动（对应 TravelAssistantApplication）
  api/                    trip_controller / map_controller / error_handlers
                          / ask_controller（扩展，仅 feature/ask-agent）
  config/                 settings（yml 等价，含 travel.loop）/ container（Spring 注入等价）/ runtime_logger
  models/                 Java model 的 pydantic 等价物，字段名对外保持驼峰
                          （含 loop 四个 model、adjust 两个与 MapConfigResponse；
                           ask_request / ask_response 属扩展）
  clients/                高德四源 + 规则/本地兜底 + selector + LLM + amap_http
                          + amap_place（地名→坐标，扩展）
  agent/                  attraction/weather/hotel/route/planner + create_agent 取数编排
                          + loop 闭环（loop_planning / reflection / llm_critic / loop_decision_executor）
                          + trip_adjustment（自然语言改计划）
                          + ask_agent / ask_tools / ask_context（扩展，仅 feature/ask-agent）
  evaluator/              budget/route/weather/daily_plan（含搬过来的 reflect_daily_plan）
  service/                planner_input_builder / requirement_analysis / llm_narration / trip_planning
                          / request_optimization_strategy（loop 与 adjust 共用）
                          + hotel_anchor_service（住宿锚点，扩展）
  static/index.html       Java 前端页面原样拷贝（2905 行；main 分支与源文件字节级一致，
                          feature/ask-agent 上多了"方案追问"区块与两个住宿输入项）
tests/                    Java 两个测试类的等价断言 + loop 四个类、adjust 两个类的等价断言
                          + test_ask_agent.py / test_hotel_anchor.py（扩展）
docs/ASK_AGENT_DESIGN.md  方案 A（只读追问 agent）的设计与逐题验证记录
docs/ADJUST_AGENT_DESIGN.md 方案 B（追问驱动改方案）+ 第 9 节（选酒店补格子）的设计与判据
MIGRATION_NOTES.md        Java->Python 映射表 + 全部不一致清单（第七节记主动偏离）
```

## 6. 测试

```bash
.venv/bin/python -m pytest -q
```

`tests/test_planner_agent.py`、`tests/test_trip_plan_controller.py` 逐条照搬 Java 的两个测试类断言。
`tests/test_reflection_agent.py`、`tests/test_llm_critic_agent.py`、`tests/test_loop_planning_agent.py`、
`tests/test_request_optimization_strategy_service.py`、`tests/test_trip_adjustment_agent.py`
是 Java 侧没有对应测试类的部分新写的，
断言取自 Java 源码的真实分支与文案（严重度公式、六个批评分支、五种停止原因、`copyRequest` 的净化差异、
改计划两条解析路径的全部 `appliedChanges` 文案与 `rule-fallback` 回退备注位置）。
`test_trip_plan_controller.py` 里追加的 3 条 `/adjust` 用例同样取自 Java 源码：Java 没有对应测试方法。
`tests/test_map_config_controller.py` 的 4 条取自 `MapConfigController.java` 的两个分支
（`enabled` 真/假/纯空白，以及静态挂载不遮 `/api/` 路由）：Java 侧没有对应的测试类。
`tests/test_ask_agent.py` 的 14 条属于扩展能力（Java 无对应物），用一个"按脚本调工具"的假 graph 顶掉模型，
跑真工具、真上下文、真留痕：钉住"无模型直接 400"、只读工具各调一次的留痕、按天游标、
景点名对不上时 `not_found` 且不外发请求、联网失败仍出回答并记 `failed`、空白问题挡成 400，
以及 B 的意图判定三种回落、`need_change` 手上没有任何写动作、归一化那句能被 `/adjust` 的规则路径识别。
`tests/test_hotel_anchor.py` 的 17 条同理，钉的是"资格"而不是"分数"：不填新字段时候选集合原样不动、
填了之后圈外被划掉且优先取最近那一档、一档空退一档、圈内空则回落全城并留一句说明、
没有坐标的酒店不参与远近判断、`strict` 三天同名（含预算被压到必然触发换店那一支时仍同名）、
按天锚点只改那一天、`strict` 与按天锚点冲突时明写"未生效"、以及改一次行程不会把上次填的位置弄丢。

## 7. 已知差异

凡是与 Java 不一致的地方（模型自选工具取代固定顺序取数、`/adjust` 的嵌套校验会级联、api-key 在日志与异常文案里打码、
`TripPlanResponse` 是源项目死类、引用相等语义的移植、配置默认值取 yml 生效值等）
全部记在 [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md)，其中第四节列出了刻意保留、未修的源项目行为。

# 智能旅行助手

说一句去哪儿、几天、多少预算，出一份逐天的行程：每天去哪几个景点、住哪家酒店、怎么走、
花多少钱。方案出来之后还能**接着追问**（「第二天为什么排这家酒店」），
判出你想改的时候帮你改（「第二天住得离西湖近一点」）——但改不改由你点确认，它自己不擅自动手。

跨会话记得住你：不吃辣、同行的人对海鲜过敏、上次在杭州住的那家隔音差，
下一次规划会自动带上，冲突时会当面指出来而不是顺着方案说「这家不错」。

<!--
  产品图放这里：把图放进 docs/images/，然后把下面这一段外面的注释去掉
  （文件名按你实际用的改）。放在这一行下面的好处是它就在正文最上方。

| 首页 | 规划结果 |
| --- | --- |
| ![首页](docs/images/landing.png) | ![规划结果](docs/images/result.png) |

| 方案追问 | 登录 |
| --- | --- |
| ![方案追问](docs/images/assistant.png) | ![登录](docs/images/login.png) |
-->

## 功能

**规划**　景点 / 天气 / 酒店 / 路线四路数据，配了模型时由 `create_agent` 自主选工具取，
没配模型自动退回固定顺序取数 + 规则兜底文案，**不配模型也能出完整行程**。
出方案后走「生成 → 反思评分 → 批评 → 修正 → 再生成」的多轮闭环选最优。

**追问**　六个只读工具（方案总览 / 某天安排 / 景点详情 / 搜景点 / 查天气 / 荐酒店），
答不上就说答不上，不编。联网失败不整体失败，改用方案内数据作答并留一条 `failed` 留痕。
SSE 推工具调用当进度条。

**长期记忆**　两桶互斥：
- **偏好**——「不吃辣」记给本人，「老婆不吃辣」记给老婆，去重键是 `(用户, 角色, 内容)`，
  同一条内容两个角色各存一条
- **场景**——「上次杭州那家酒店隔音差」按城市存，**只在目的地是杭州时**才注入

注入提示词时明写两条规矩：本次说的优先、冲突要说出来。空的时候整段不拼，
不塞一句「暂无长期记忆」提醒模型这里本来该有点什么。

**账号**　注册 / 登录 / 登出。密码 bcrypt，会话 cookie `httponly` + `samesite=lax`。
长期记忆按 `user_id` 存，一个库装所有客户。

## 快速开始

### 1. 安装

```bash
uv venv .venv
uv pip install -p .venv/bin/python --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
```

直连 pypi 超时时用上面的阿里云镜像；已有 `.venv` 可跳过第一行。

### 2. 配置

```bash
cp .env.example .env   # 然后填 TRAVEL_LLM_API_KEY 与 AMAP_API_KEY
```

配置项来自环境变量或本地 `.env`，**不要**把 key 写进代码、README 或提交信息（`.env` 也别提交）。
`TRAVEL_LLM_API_KEY` 不填时会回退读 `DASHSCOPE_API_KEY`。
模型端点默认是 DashScope 的 OpenAI-Compatible 地址 + `qwen-plus`，
但走的是通用 OpenAI 协议：把 `TRAVEL_LLM_BASE_URL` / `TRAVEL_LLM_MODEL` 换成任意
OpenAI-Compatible 端点即可，本机实测用 DeepSeek 端点跑通全链路。

要出真实地图再填 `AMAP_WEB_JS_KEY` / `AMAP_SECURITY_JS_CODE`（必须是「Web端(JS API)」类型，
与后端那把「Web服务」key 不通用）；不填时地图区显示占位提示。

<details>
<summary>全部配置项</summary>

| 环境变量 | Java `application.yml` | 默认值 |
| --- | --- | --- |
| `TRAVEL_LLM_ENABLED` | `travel.llm.enabled` | `false`（置 `true` 才走模型自选工具，否则按固定顺序纯规则取数） |
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
| `AMAP_WEB_JS_KEY` / `AMAP_SECURITY_JS_CODE` | `amap.web-js-key` / `amap.security-js-code` | 空。只被 `GET /api/map/config` 读 |
| `TRAVEL_LOOP_ENABLED` | `travel.loop.enabled` | `true` |
| `TRAVEL_LOOP_MAX_ROUNDS` | `travel.loop.max-rounds` | `3` |
| `TRAVEL_LOOP_MAX_NO_IMPROVEMENT_ROUNDS` | `travel.loop.max-no-improvement-rounds` | `2` |
| `TRAVEL_DB_DSN` | —（扩展） | `postgresql:///travel_assistant`，本地 socket、无密码 |
| `SERVER_PORT` | `server.port` | `8080` |

**兜底开关的两层语义（与 Java 一致）**：

1. `TRAVEL_PROVIDERS_<SOURCE>=amap` 走高德实现，其他任意值走规则/本地兜底实现（大小写不敏感）。
2. 选中高德后：`AMAP_MOCK_RESPONSE=true` 直接返回兜底实现的数据；未配 `AMAP_API_KEY` 抛
   "已选择高德X provider，但尚未配置 travel.providers.amap.api-key"；请求失败或结果为空时按各自源的规则
   兜底（酒店/天气/路线回退规则实现，景点回退本地知识库，POI/天气缺某天用规则补全，
   路线限流自动切规则估算）。景点/酒店的兜底与 Java 一样是静默的，只在 summary 与 `provider` 字段里体现来源。

</details>

### 3. 数据库

登录与长期记忆要 Postgres。建库：

```bash
createdb travel_assistant
```

建表语句在 `travel_assistant/schema.sql`，由 `db.get_pool()` 在第一次用到数据库时**幂等执行**，
不上 alembic。连接串用 `TRAVEL_DB_DSN` 覆盖。

> `create table if not exists` 对**已存在的表整条跳过**，所以改列它一个字都不会做。
> 升级已有库要跑 `schema.sql` 里那几行 `alter table`（文件内有注释说明）。

`data/chroma/` 是景点知识库的向量索引，**不入库**。它由 `scripts/kb/` 下的建库工具按城市生成；
缺了它景点检索退回内置词表，不影响启动。

### 4. 启动

```bash
PYTHONPATH=. .venv/bin/python -m travel_assistant.main
# 或者：.venv/bin/python -m uvicorn travel_assistant.main:app --host 0.0.0.0 --port 8080
```

启动日志只打印 provider、base-url 和开关，**不打印 key**。
起服务后浏览器打开 `http://localhost:8080/` 就是前端页面，未登录会落到登录页。

## 接口

| 端点 | 说明 |
| --- | --- |
| `POST /api/auth/register` `login` `logout`、`GET /api/auth/me` | 注册 / 登录 / 登出 / 当前用户 |
| `POST /api/trips/plan` | 生成行程 |
| `POST /api/trips/adjust` | 用自然语言改计划 |
| `POST /api/trips/ask` | 就当前方案追问 |
| `POST /api/trips/ask/stream` | 同上，SSE 推工具调用 |
| `GET /api/map/config` | 前端地图配置（唯一不要求登录的端点） |

除 `/api/map/config` 外都要求登录。字段名保持 Java 源的驼峰风格，
错误体统一是 `{code, message, details[], timestamp}`。

**请求 / 响应字段、curl 示例、各端点行为细节见 [`docs/API.md`](docs/API.md)。**

## 目录

```
travel_assistant/
  main.py             FastAPI 装配 + CORS + StaticFiles 挂载 + 启动
  db.py schema.sql    Postgres 连接池 + 建表（幂等执行）
  api/                auth / trip / ask / map 四个 controller + 鉴权依赖 + 错误处理
  config/             settings（yml 等价）/ container（依赖注入）/ runtime_logger
  models/             pydantic 模型，字段名对外保持驼峰
  clients/            高德四源 + 规则兜底 + selector + LLM
                      + 用户库三个 store（auth / preference / episode）
  kb/                 景点知识库：编码、检索、词表
  agent/              四路数据 agent + planner + loop 闭环 + trip_adjustment
                      + ask_agent / ask_tools / ask_context（追问）
  evaluator/          budget / route / weather / daily_plan
  service/            planner_input_builder / requirement_analysis / trip_planning
                      / auth / preference / episode / hotel_anchor / intercity …
  static/             index.html + css/ + js/ + 字体 + 插画（FastAPI 挂在根路径）
tests/                512 条，见下
docs/                 设计稿与验收记录，见下
scripts/              建库工具、视觉验收探针、结果页渲染
data/                 景点资料、标签表；chroma/ 是本地生成的索引，不入库
```

## 测试

```bash
.venv/bin/python -m pytest -q      # 512 passed
```

全部是单元测试——喂死数据，**不调模型、不碰数据库**。真 SQL（unique 约束、`on conflict do nothing`）
由 `test_*_store_postgres.py` 三个文件单独守，它们要一个真的库。

端到端验收另有一套探针：起真服务、连真库、调真模型，跑一次几分钟，脚本在 `/tmp` 下不入库。
两层的分工是：单元测试证明**每个函数按写的做**，探针证明**整条链真的接通了**——
后者能抓到前者抓不到的（HTTP 那条路上 `user_id` 有没有传到底、真库认不认那个唯一键）。

## 与 Java 源项目的关系

本项目从 Java 版「智能旅行助手」移植而来（Spring Boot 3 / Java 17），
只做语言与框架替换：FastAPI 顶掉 Spring Web，pydantic 顶掉 POJO + Jakarta Validation，
httpx 顶掉 RestTemplate，LangChain `create_agent` + `@tool` 顶掉固定顺序取数。
业务规则、阈值、文案、字段名尽量照搬 Java 源码。

在此之上做了几处**基线之外的扩展**：登录与长期记忆、方案追问（`/ask`）、
住宿锚点（`hotelAnchor` / `dayHotelAnchors` / `hotelContinuity`）、城际交通、景点知识库。

凡是与 Java 不一致的地方（模型自选工具取代固定顺序取数、`/adjust` 的嵌套校验会级联、
api-key 在日志与异常文案里打码、`TripPlanResponse` 是源项目死类、引用相等语义的移植、
配置默认值取 yml 生效值等）全部记在 [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md)，
其中第四节列出了刻意保留、未修的源项目行为。

## 设计文档

`docs/` 下是各功能的设计稿与逐条验收记录，**改代码前先看这里**：

| 文档 | 内容 |
| --- | --- |
| [`docs/ASK_LONGTERM_MEMORY.md`](docs/ASK_LONGTERM_MEMORY.md) | 登录、长期偏好、场景记忆（分档设计 + 判据 + 实机验收） |
| [`docs/ASK_AGENT_DESIGN.md`](docs/ASK_AGENT_DESIGN.md) | 方案追问：工具、护栏、意图判定 |
| [`docs/ADJUST_AGENT_DESIGN.md`](docs/ADJUST_AGENT_DESIGN.md) | 追问驱动改方案 |
| [`docs/API.md`](docs/API.md) | 接口参考 |
| [`MIGRATION_NOTES.md`](MIGRATION_NOTES.md) | Java → Python 映射表 + 全部不一致清单 |

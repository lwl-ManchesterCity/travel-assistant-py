# 交接：给景点资料库补北京、上海、广州三城

> 写给执行者（另一个 agent）。**先通读本文件再动手**，尤其第二节——那 6 处硬编码会让不读的人
> 在"看起来成功"的情况下拿到一份污染的数据。
>
> 上游设计文档：`docs/ATTRACTION_KB_DESIGN.md`（**必读**，本文件是它的执行细则）。
> 本任务对应那份文档 **§五 第 6 步「通过后再铺其他城市」**，不是新设计。

---

## 一、这件事是什么、为什么做

`data/attractions.json` 是一张**离线策展的景点资料表**，34 条，**全部是杭州**。它只服务**追问侧**：
用户问「XX 门票多少 / 几点关门 / 周一闭馆吗 / 要预约吗」时，追问 agent 查这张表回答。

规划侧不碰它（那边走高德实时接口）。

所以现状是：问杭州景点答得上，问别的城市一律回「我没查到」——这是追问 agent 目前**唯一的短板**。
本任务就是把它补上。

### 1.1 数据链条：四个文件，别搞混

补城市不是改一个文件，是**在这条链的每一环各加一份**。先认清这四个：

```
高德 /v3/place/text
      │
      │  fetch_amap_fixture.py            ← 花高德配额，每城只跑一次
      ▼
scripts/kb/fixtures/amap_pois_hangzhou.json     860 KB  ← 高德原始响应（生肉）
      │
      │  normalize.py                     ← 离线转换，免费、可重跑
      │    +  data/llm_supplement.json    13 KB  ← 联网补的票价/简介/出处
      │    +  build_kb.py 合成 + 7 条校验
      ▼
data/attractions.json                            23 KB  ← 成品，34 条
      │
      │  build_index.py                   ← 向量化
      ▼
data/chroma/  (chroma.sqlite3)                  483 KB  ← **运行时真正查的是这个**
```

**每条记录的字段来自三层**，别把来源搞混：

| 层 | 字段 | 谁给的 |
|---|---|---|
| 第一层 | `amap_poi_id`、`name`、`city`、**`location`（经纬度）**、`type` | **高德** |
| 第二层 | `category`、`tags`、`duration_min` | 离线**从 `type` 推** |
| 第三层 | `ticket_price`、`description`、`source_url` | **你联网搜**（见第四节护栏） |

**坐标必须来自高德，不许自己编。** 规划侧靠它算通勤距离、在地图上打点——LLM 编出来的坐标
会错得看不出来。同理 `amap_poi_id`（官方稳定 ID，去重和主键都靠它）。

**为什么生肉要单独存一份、不能直接改 `attractions.json`**：成品里已经**删掉了** `type` 和
`biz_ext`（`build_kb.py:40` 的 `_INTERNAL_FIELDS`），而 `category` 是从 `type` 推的、`hours`
是从 `biz_ext.opentime2` 推的。留一份生肉，改解析逻辑时**不用重新花高德配额**——这是那张表
唯一的存在理由（设计文档 §五 第 1 步）。

### 1.2 挑选口径

**不是「热门 top N」**，是「**覆盖追问里真会被问到的那几类**」：室内（雨天/亲子）、
山（避暑）、湖与夜景（拍照/夜游）、免费街区（预算敏感）、周一闭馆的博物馆、古镇水乡。
照抄这个口径，别按高德热度排序取前 30。

---

## 二、⚠️ 硬前提：工具链写死了杭州，第一步必须先参数化

**不先做这一步就直接 `--city 北京`，会静默出错。** `--city` 这个参数是**半成品**：它只改发给
高德的 `city` 字段，**不改关键词表、也不改 fixture 路径**。

后果：用**杭州的关键词**（西湖风景名胜区、灵隐寺…）去北京搜，然后把结果写进
**`amap_pois_hangzhou.json`**。而且因为现有 34 个 slug 都在，循环会**全部跳过并打印"已存在"**——
输出看着一切正常，实际一份数据都没抓，或更糟：把北京的 POI 混进了杭州的 fixture。

### 要改的 6 处

| # | 文件 | 行 | 现状 |
|---|---|---|---|
| 1 | `scripts/kb/fetch_amap_fixture.py` | `:23` | `from scripts.kb.keywords import HANGZHOU_KEYWORDS` |
| 2 | `scripts/kb/fetch_amap_fixture.py` | `:28` | `FIXTURE_PATH = ... / "amap_pois_hangzhou.json"` 写死 |
| 3 | `scripts/kb/fetch_amap_fixture.py` | `:79` | 无 `--only` 时一律用 `HANGZHOU_KEYWORDS` |
| 4 | `scripts/kb/build_kb.py` | `:31` | `FIXTURE_PATH = ... / "amap_pois_hangzhou.json"` 写死 |
| 5 | `scripts/kb/keywords.py` | `:18` | `ATTRACTION_SPECS` 只有一张杭州表 |
| 6 | `scripts/kb/normalize.py` | `:405-407` | 默认吃 `ATTRACTION_SPECS`（杭州那张） |

**建议改法**：`keywords.py` 改成按城市分组（如 `SPECS_BY_CITY = {"杭州": (...), "北京": (...)}`），
`HANGZHOU_KEYWORDS` 作为兼容别名保留（有测试依赖）；两个脚本的 fixture 路径按城市派生
（如 `amap_pois_{city_slug}.json`），`--city` 一路串到底。

**改完先跑一次杭州全流程，确认输出与现有 `data/attractions.json` 逐字节一致**——
这是证明参数化没改坏东西的唯一办法。对不上就是改错了，别往下走。

### 还有一处会咬人的

`scripts/kb/normalize.py:257`：

```python
_CITY_PINYIN: dict[str, str] = {"杭州": "hz"}
```

`_make_id`（`:245-254`）用它生成主键 `{城市拼音}-{poi_id短码}`，取不到就落到字面量 `"city"`。
**不补这个映射，北京/上海/广州的 id 前缀会全部变成 `city-`**，三城共用一个假前缀。
把 `北京→bj`、`上海→sh`、`广州→gz` 加进去。

（`_make_id` 的 `name` 参数从头到尾没被用过，是死参数，顺手清理。）

---

## 三、每座城市的五步

严格按 `ATTRACTION_KB_DESIGN.md` §五 走。**一座做完再做下一座**，别三城并行。

### 第 1 步：写规格表（**唯一必须人工的一步**）

在 `keywords.py` 为这座城市加约 35 条。每条三格：

```python
{"keyword": "故宫博物院", "expect_name": "故宫博物院", "category": None},
```

- `keyword` —— 拿去打高德 `/v3/place/text` 的检索词
- `expect_name` —— **期望命中的 POI 名**（见下面护栏第 1 条，这是关键）
- `category` —— 只在「按 type 推是错的」时才覆写，否则 `None`

按第一节那个口径选景点，覆盖到各个类别。**别按热度排序取前 30。**

### 第 2 步：拉骨架存 fixture

```bash
.venv/bin/python -m scripts.kb.fetch_amap_fixture --city 北京
```

- 存的是**未归一化的原始响应**，保留 `biz_ext`——**必须带 `extensions=all`**，否则拿不到开放时间
- 脚本已内置 1.2 秒限速（高德个人 key QPS 很低，实测报 10021 CUQPS），**不要去掉**
- 单条失败不会中断整批，最后会汇总失败列表；退出码非 0 时看是哪些，**补齐再往下**

### 第 3 步：离线转换（免费、可重跑）

从 fixture 生成 `amap_poi_id` / `name` / `city` / `location` / `category`，并按映射表推 `tags` 与
`duration_min`。开放时间从 `biz_ext.opentime2` **离线推导**——文档 §十一.1 已实测：
杭州 20 个样本里 18 个的 `opentime2` 同时含开放时段、闭馆日、最晚进入时间。

`parse_opentime2` 已处理四个坑（全角破折号、`00:00-24:00` 是占位不是答案、
闭馆日与开放区间混在同一子句、闭馆关键词有六种写法）。**这些都有测试，别改逻辑。**

### 第 4 步：第三层联网补数据（**一条一条来**）

产出 `data/llm_supplement.json`，按 `amap_poi_id` 索引，每条给：

`ticket_price` / `hours` / `closed_days` / `description` / `tags` / `source_url` / `updated_at`

> **一条一条来，不要一次塞 30 条让模型批量输出**——批量必串味（把 A 的门票写到 B 上），
> 且这是**看不出来的静默错误**。

`build_kb.py:36-37` 只允许第三层覆盖上列字段；骨架字段（`amap_poi_id`/`location`/`category`…）
不许碰，否则「同一份 fixture 跑两次输出一致」这条判据会失效。

`updated_at` 默认值是 `"2026-10-10"`（`build_kb.py:61`），**新补的这批要写实际日期**。

### 第 5 步：校验 + 抽检

```bash
.venv/bin/python -m scripts.kb.build_kb
```

7 条校验（`kb_schema.py`）会跑。然后**抽 5 条人工质检**——这不是填表，是质检：

> 不做这步**无从知道错误率是 5% 还是 50%**，而它决定这张表能不能用。

对照 `source_url` 逐条核票价/时段/闭馆日。杭州那次是 5/5 正确。

---

## 四、四条护栏（文档 §六，**必须写进你的提示词**）

1. **必须返回你读的那个页面的 URL**（`source_url`）。**没有 URL 就不许填值。**
2. **不确定就填 `null`，不许猜。** 宁可留空也不要编一个看起来合理的数。
3. **`tags` 只能从 `data/tags.json` 的词表里挑**（3 组 14 词），词表外的词一律不许出现。
4. **一次只处理一个景点。** 禁止合并输出多条。

第 2 条是关键：不做这条，你会得到 300 条**看起来完整、实则一半是编的**表，**而且看不出来**。

**票价缺失必须是 `null`，不是 `0`**——这是整份文档里最重要的一条校验（把"免费"和"不知道"混成
一个值，追问「门票多少」会得到荒谬答案）。

---

## 五、已经踩过的坑（照做，别自己重新发现）

1. **高德 POI 首条不可靠，必须按名字挑。** 实测「楼观台」返回西安的起名馆、「龙井村」返回公交站、
   「河坊街」返回道路名。所以规格表里有 `expect_name` 这一格——`normalize.is_pickable_poi` 会
   过滤掉公交站/道路名这类非游览实体，但仍要按名字核对。
2. **`category` 要按 type 的末级判，不能整串匹配。** 灵隐寺的 type 是
   `风景名胜;风景名胜;国家级景点`，整串匹配先命中「风景」→ 误判成「自然风景」，
   门票估算随之从 50 变 60。已改为先判末级。
3. **`duration_min` 是「建议值」不是「事实值」**，第二层按 category 统一给的。
   杭州抽检时发现雷峰塔被给成 180 分钟、实际约 1 小时。已知偏差，不必修。
4. **离线表与运行时有一处 category 分歧**（`normalize.py:35-44` 注释）：
   离线表把 图书馆/纪念馆/展览馆/艺术馆/科技馆 归「人文历史」，运行时
   `amap_attraction._map_type_to_category` 会落到「城市漫游」。上一轮的红线是不改
   `travel_assistant/` 下的代码，所以**有意保留**。**动手前先确认这条还在不在**，
   并决定这次是继续保留还是两边一起改——分歧会随城市数放大。

---

## 六、补完之后：**必须重建索引**，否则追问侧看不见

运行时**不读** `attractions.json`，读的是建好的向量库：

```bash
.venv/bin/python -m scripts.kb.build_index
```

- `travel_assistant/kb/paths.py:7`：「运行时只读建成后的 `chroma/`，不读它」
- `build_index.py`：「索引是**产物**，不入库；`attractions.json` 一改就要重建」
- `data/chroma/` 在 `.gitignore:25`，**不入库**——每台机器要各自重建

检索侧按 `city` **精确匹配**收窄（`travel_assistant/kb/retrieve.py:39-40`），所以 KB 里那格
`city` 的值必须和请求里的城市名**逐字相同**。`normalize.py:418` 取的是 fixture 里存的
`city`（＝ `--city` 的传参），**不是**高德返回的 `cityname`（那是「北京市」）。
所以传 `--city 北京` 是对的，**别传「北京市」**。

---

## 七、验收

| # | 判据 |
|---|---|
| 1 | 三城各 ≥30 条，通过全部 7 条校验，`--strict` 零告警 |
| 2 | 无「未知写成 0」：`ticket_price` 为 `null` 的条数如实统计，付费条目的票价都能在 `source_url` 页面上查到 |
| 3 | `amap_poi_id` 跨三城全局唯一，且 id 前缀是 `bj-`/`sh-`/`gz-`，不是 `city-` |
| 4 | 杭州原有 34 条**逐字节未变**（参数化改造的回归证明） |
| 5 | 每城抽检 5 条，票价/时段/闭馆日/出处与 `source_url` 一致 |
| 6 | `pytest` 全绿（含 `tests/test_attraction_kb.py` 现有 93 条用例） |
| 7 | **真实追问实测**：重建索引后，问一个北京景点（如「故宫门票多少钱」）能答出票价与出处，不再回「没查到」 |

第 7 条是这件事**唯一真正要的结果**，别只跑完脚本就收工。

---

## 八、交付物与记录

- `scripts/kb/keywords.py` —— 新增北京/上海/广州三张规格表
- `scripts/kb/fixtures/amap_pois_{beijing,shanghai,guangzhou}.json`
- `data/llm_supplement.json` —— 追加三城的第三层数据
- `data/attractions.json` —— 三城合并后重新生成
- **`docs/ATTRACTION_KB_DESIGN.md` §十一** —— **追加一节「第二轮：北京/上海/广州」**，
  写清：实际条数、抽检结果、遇到的文档未预见的问题、以及 category 分歧这次怎么处理的

最后一件事：**别只汇报"全部通过"**。抽检错的、`source_url` 打不开的、某个景点搜不到的，
都要如实列出来——杭州那次的实施记录里就老实写了「浙博官网在本机不可达，已改用国家文物局年报页」
和一处 `duration_min` 偏差，那才是这份记录有用的原因。

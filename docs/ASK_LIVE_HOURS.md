# 追问侧「开放时间」接两层，并改掉规则 11 那条错划线

> 2026-10-13。上游：`docs/ASK_ATTRACTION_LOOKUP_FIX.md` §6.1 与 §七遗留第 3 条。
> **本文是待审的设计，还没动代码。**

---

## 一、这件事是什么

上一轮把「问一个具体景点」接到了本地库上。剩下的缺口是：**本地库没有的，就彻底答不了。**

```
用户：越秀公园几点关门
模型：先 search_attractions_by_tag(景点名=越秀公园) → 本地库命中 → 06:00-22:00   ✅
```

但如果问的是一个**本地库没收的景点**，或者**一个本地库不收录的城市**，今天只能回一句"查不到"。
而这两个缺口本来不该存在——**高德一直把开放时间返回给我们，是运行时把它扔了。**

同时把 §七遗留第 3 条一起改掉：`ask_agent.py` 规则 11 **第一条**把「几点关门」划给了
`get_attraction_detail`，可那个工具读的是方案字段，**方案里压根没有开放时间这一格**。
上一轮模型自己绕过去了（实测「越秀公园几点关门」改走了本地库），但规则本身还在误导。

两件事一次做完：**开放时间 → 本地库、联网两层；票价 → 只留本地库一层。**

---

## 二、根因

### 2.1 高德给了，运行时扔了

| 位置 | 现状 |
|---|---|
| `clients/amap_attraction.py:118` | 请求带 `"extensions": "all"` —— **本来就为拿开放时间而加的** |
| `clients/amap_attraction.py:130-148` `_to_attraction` | 只读 `poi` 的顶层字段 + `biz_ext.rating`（走 `_parse_rating`），**`biz_ext.opentime2` 从头到尾没被读过** |

`Attraction`（`models/attraction.py`）10 个字段里没有开放时间，所以 POI 里那个字段即使解析了也没地方放。

**覆盖率实测**（四城 fixture，本次现跑）：

| 城市 | POI | 有 `biz_ext` | `opentime2` 非空 |
|---|---|---|---|
| 北京 | 382 | 382 | **267（69%）** |
| 上海 | 315 | 315 | **211（66%）** |
| 广州 | 333 | 333 | **215（64%）** |
| 杭州 | 351 | 351 | **214（60%）** |
| 合计 | 1381 | 1381 | **907（65.7%）** |

内容形如：

```
杭州（中国丝绸博物馆）：
  全年 周二–周日 09:00–17:00开放 最晚进入16:30；全年 周一 全天不开放；
  清明节,劳动节,端午节,中秋节,国庆节 09:00–17:00开放 最晚进入16:30

广州（某公园）：
  周一至周日 08:00-22:00
```

时段、闭馆日都在里面，**不用联网补，高德一条就够。**

### 2.2 解析器在离线那边，运行时手上没有

`scripts/kb/normalize.py:130 parse_opentime2` 已经写好了，而且把四个坑都趟过了
（全角破折号 / `00:00-24:00` 是占位不是答案 / 闭馆日与开放区间混在同一子句 / 闭馆关键词六种写法），
`tests/test_attraction_kb.py:277-293` 有 4 条用例守着。

**但它在 `scripts/kb/` 下**——离线建库工具链的地盘。运行时 import 它，方向是反的。

### 2.3 联网那个工具按不了名字查

`ask_tools.py` 里的 `search_attractions()` 是**零参数**的，走 `attraction_agent.search(request)`，
按**偏好关键词**做发现式检索。用户问「越秀公园几点关门」，它返回的是"广州、自然风景"命中的一批，
**不保证里面有越秀公园**。

按名字查的能力**其实已经有了**：

- `clients/base.py:31` `AttractionClient.find_attraction_by_name(city, name)`（可选能力，基类默认返回 `None`）
- `clients/amap_attraction.py:36` 高德实现，`_fetch_poi_response(city, name)` 打 `/v3/place/text`

但它只接在 **pinHints（"某天必去某景点"）** 那条链上，追问侧没接。

再叠一层：`_attractions_text`（`ask_tools.py:96`）只打 `名称（类别，地址）`。
**就算 POI 把开放时间带回来了，也不在给模型看的文本里。**

### 2.4 规则 11 第一条那条划线（§七遗留第 3 条）

```
11. …问**方案里已有的那个景点**（要门票吗、几点关门）用 get_attraction_detail。
```

`get_attraction_detail`（`ask_tools.py:199-229`）读的是**方案**里那几格：
`类别 / 地址 / 门票（按类别估算）/ 停留时长 / 坐标 / 简介`。
**没有开放时间，没有闭馆日。** 把「几点关门」划给它，等于让模型去一个没有这格的柜子里翻。

上一轮已经确认过：模型这次自己绕过去了。下一轮不保证。

---

## 三、改法

### 3.0 链路对照

**改前**（「越秀公园几点关门」，越秀公园在方案里、但方案没有开放时间）：

```
① 前端发「越秀公园几点关门」+ 整份方案 → POST /api/trips/ask
② 模型按规则 11 第一条 → get_attraction_detail("越秀公园")
③ 工具读方案 → 类别/门票(按类别估算)/停留时长/地址/坐标  ← 没有开放时间
④ 模型要么照实说"方案里没写"，要么（上一轮实测）自己想起来改走本地库
```

**改后**（② 走哪条由规则 11 定，两层是串起来的）：

```
② 模型按新规则 11 → 客观信息走查库 → search_attractions_by_tag(景点名="越秀公园")
③ 本地库命中 → 06:00-22:00                                        ← 到此为止，不联网
   ── 本地库没命中（或城市不收录）时 ──
③'  模型再用 search_attractions(景点名="越秀公园")
④'  find_by_name → 高德 POI → biz_ext.opentime2 → 解析 → 挂在 Attraction 上
⑤'  工具文本带上「开放 06:00-22:00｜闭馆 无」→ 模型作答
   ── 两层都没命中 ──
⑥'  照实说查不到
```

### 3.1 解析器搬到运行时：新建 `travel_assistant/clients/amap_opentime.py`

原样搬三样：`parse_opentime2`（`normalize.py:130`）、`_closed_days_in`（`:187`）、`_pad`（`:230`），
以及它依赖的模块级正则 `_CLOSED_DAY_ORDER` / `_TIME_RANGE` / `_ALL_DAY_PLACEHOLDER`。

- **不搬 `_CLOSED_DAY`**（`normalize.py:64`）：定义了但全库没有任何地方用过，是死代码，搬家时丢掉。
- `scripts/kb/normalize.py` 改成 `from travel_assistant.clients.amap_opentime import parse_opentime2`
  并原样再导出，**`tests/test_attraction_kb.py` 一行都不用改**。
- **方向不是新开的口子**：`scripts/kb/` 已经在 import `travel_assistant` 了
  （`fetch_amap_fixture.py:24`、`build_index.py:14`、`kb_schema.py:21`），这是既有方向。
- 新模块只引 `re`，不引 chromadb、不引 torch —— 符合 `kb/store.py` 顶部那条
  "别为了写进工具说明就往服务启动里塞 0.3s + 35M" 的约定。

### 3.2 `Attraction` 加两格（`models/attraction.py`）

```python
hours: Optional[str] = None
closed_days: Optional[list[str]] = None
```

- 与本地库那两格**同名同形**（`retrieve.py:31` 的 `hours` / `closed_days`），
  工具层排版可以共用一套说法。
- 加字段对既有构造点零影响（都有默认值）；出 JSON 时多两个键。
  前端是**原样回传**整份方案的（`static/js/app.js:752` `tripPlan: currentTripData`，不重建对象），
  所以这两个键能穿过一轮往返。
- **但追问链不读方案里那两格**，理由见 §五第 2 条。

### 3.3 高德客户端解析它（`clients/amap_attraction.py`）

在 `_to_attraction` 里加一格，形状与既有的 `_parse_rating`（`:242`）保持一致：

```python
def _parse_opentime(self, poi: dict) -> tuple[str | None, list[str]]:
    biz_ext = poi.get("biz_ext")
    if not isinstance(biz_ext, dict):
        return None, []
    hours, closed_days = parse_opentime2(biz_ext.get("opentime2"))
    if hours is None:
        # open_time 是简版时段（实测覆盖 55%），opentime2 缺失时的兜底。
        # 与 normalize.py:339 同一条口径：简版里没有闭馆日，所以只取 hours。
        hours, _ = parse_opentime2(biz_ext.get("open_time"))
    return hours, closed_days
```

然后 `_to_attraction` 里 `hours=…, closed_days=…`。

### 3.4 编排层给一个按名字查的出口（`agent/attraction_agent.py`）

```python
def find_by_name(self, city: str, name: str) -> Attraction | None:
    return self._attraction_client.find_attraction_by_name(city, name)
```

放在 agent 上而不是让工具直接拿 client：与另外六个工具一致（工具 → agent → client，
`config/container.py:88` 就是这么装的）。

走的是**既有**的 `find_attraction_by_name`：基类（`base.py:31`）默认返回 `None`，
是"可选能力"，所以本地/规则那几个兜底 provider 不会因为这条链而报错。

### 3.5 联网那个工具加一格参数（`agent/ask_tools.py`）

```python
def search_attractions(attraction_name: str = "") -> str:
```

- 填了名字 → `find_by_name(city, name)`；**没填 → 原来的发现式检索，行为一字不变**。
- 这一格破的是本模块开头那条"联网的三个一律零参数"的约定（`ask_tools.py:7-9`）。
  **破例理由和本地库那个是同一条**：城市、日期、预算、住宿偏好让模型填会被编造，
  而"问的是哪个景点"是模型才知道、从上下文取不出来的判断。
  **模块开头的分类说明要跟着改**，把这条破例写明白，别让下一个人以为是漏改的。
- **不加新工具。** 两条理由：`tests/test_ask_agent.py:292` 把工具名单写成了一个固定集合，
  加工具要动那条判据；更要紧的是**三个景点工具会让模型多一个选错的机会**，
  而"本地库那个不许和联网那个撞名"已经在 `test_ask_agent.py:492` 被专门守着。
- **名字对不上要留痕**：高德的 `find_attraction_by_name` 在**没有同名**时会返回"最接近的那个"
  （`amap_attraction.py:55` `exact = […] or candidates`）。问「广州塔旋转餐厅」可能返回「广州塔」。
  所以工具在返回文本里显式标一句：

  ```
  高德只搜到名字最接近的「广州塔」，不确定是不是你要找的。
  ```

  **不做硬过滤**：`用户原词 in POI名` 这种判断会把「陈家祠」vs「陈家祠堂」这类简称误杀，
  而简称为难是已知的（`ASK_ATTRACTION_LOOKUP_FIX.md` §七第 1 条）。
  把判断交给模型，与规则 12（返回了不等于对得上，自己要核）同一条思路。

### 3.6 工具文本带上开放时间（`ask_tools.py` `_attractions_text`）

只在**按名字查**那条路上打（发现式检索一次 10 条，每条挂开放时间是噪声）：

```
越秀公园｜风景名胜｜广州市越秀区解放北路988号｜开放 06:00-22:00｜闭馆 无
```

- **不打票价。** 联网那条路上的 `ticket_price` 是 `_infer_ticket_price`（`amap_attraction.py:230`）
  **按类别估的**，不是高德的真实门票。写进给模型看的文本就会被当成事实转述出去。
  这正是"票价不联网"的技术原因——**不是数据源没有这个字段，是它没有这个含义**（见 §五第 1 条）。
- 两层都没命中时，工具文本要能区分"本地库没有"和"联网也没有"，否则模型分不清该不该说没查到。

### 3.7 规则 11（`agent/ask_agent.py`）

**第一条的划线改掉**：只留方案内部真有的东西，「几点关门」挪到客观信息那一侧。

```
11. 景点问题分三种问法，别用混。问**方案内部的信息**（它排在第几天、什么类别、逛多久、
在哪、方案给它估的门票）用 get_attraction_detail——**它只读方案**，方案里没有开放时间、
也没有闭馆日。另外两种都走查库 search_attractions_by_tag：问**有什么符合条件的**
（下雨天去哪、有什么免费的、哪里适合带孩子）传 tags；问**某个景点的客观信息**
（「广州塔门票多少钱」「陈家祠周一闭馆吗」「越秀公园几点关门」）**把景点名填进
attraction_name**——要门票吗、几点关门、周一闭馆吗、要预约吗这几样，景点在不在方案里
都一样，只能查库。
```

**再补一句两层**（接在"第三种最容易答错"那段之后）：

```
**开放时间分两层**：本地库没命中、或这个城市不收录，就再用 search_attractions 联网查一次，
同样填景点名——高德给开放时间，只是要现查。**两层都没有才说查不到**，
本地库没有不等于没有。但**票价只有本地库这一层**：联网那层没有门票价，查不到就照实说。
```

已有的那几段（第三种问法最容易答错、下雨天是筛选条件、城市范围以工具说明为准、tags 词表）
**全部保留不动**。

---

## 四、判据（怎么算改好了）

1. **搬家无回归**：`tests/test_attraction_kb.py` 那 4 条 `parse_opentime2` 用例**一行不改**仍过。
2. **单测（解析）**：`_to_attraction` 在带 `biz_ext.opentime2` 的 POI 上填出 `hours` / `closed_days`；
   `opentime2` 缺失但 `open_time` 有时回落；**两者都空时两格都是 `None`——不是空串、不是 `0`**。
3. **端到端（真实运行）**：广州方案下问一个**本地库没有**的景点开放时间，
   答出时段，并且轨迹里是 `search_attractions` 而非本地库那个工具。
4. **不回归（本地库优先）**：问「陈家祠周一闭馆吗」仍走 `search_attractions_by_tag`，
   答案与上一轮验收一致，**不产生一次多余的高德调用**。
5. **不回归（不硬凑）**：问库里和联网都没有的（「广州塔旋转餐厅」），仍如实说查不到，
   **不许拿高德返回的"最接近那个"顶替**。
6. **不回归（零参数那条路）**：`search_attractions` 不填名字时，返回值与改动前**逐字一致**。
7. `pytest` 全绿（现有 **325 passed**，2026-10-13 本机实测）。
8. `tests/test_ask_agent.py:292`（工具名单）与 `:492`（两个 search 工具不撞名）两条判据仍过。

---

## 五、明确不做

1. **票价不联网。** 高德的 `cost` **不是门票价**——本次现跑四城 fixture，`cost` 非空的只有
   37 条，且内容是：杭州「丰溢茶楼·农家菜 62」「与河餐厅 134」（餐饮人均）、
   上海「东方明珠·可口可乐欢乐餐厅 203」、北京「南锣鼓巷夜市 47」，
   还混着「什刹海兴华胡同 **147058**」「武康路新里 **155141**」（住宅小区的房价）。
   **一个字段里混着人均消费和房价**，没有任何过滤能把它变成门票价。
   这条一破，「广州塔门票多少钱」会答出一个看着像真的、实际是隔壁饭馆人均的数。

   > 订正：上一轮讨论里我说过"非空的 `cost` 全是餐厅/茶馆"。**那句不准确**，
   > 实际混着住宅小区的房价（5 位数到 6 位数）。结论不变，但依据要按实测的写。

2. **不动 `get_attraction_detail` 的字段**：不往它的返回里加开放时间。
   §3.2 之后**新生成的方案**里会带上 `hours` / `closed_days`，但**不要让模型从那里读**——
   本地库那层是**查证过、带 `source_url` 的**，高德那层是原始 POI 文本；
   两者打架时该赢的是前者。读方案那条路只留方案真有的东西，边界才清楚。
3. **不改 `find_attraction_by_name` 的匹配语义。** 它在 pinHints 那条链上要的就是"尽力找到"，
   收紧会改到"某天必去某景点"的行为（`AMAP_ATTRACTION` 补搜那条路）。
4. **不降 `DEFAULT_MIN_SCORE`、不动检索算法、不动 tags 那条硬筛**（沿用
   `ASK_ATTRACTION_LOOKUP_FIX.md` §五）。
5. **前端不加展示**：开放时间只进模型上下文，不进页面。要做展示另开一轮。

---

## 六、验收记录

> 待实现后填写（照 `ASK_ATTRACTION_LOOKUP_FIX.md` §六 的格式：判据 / 结果 / 轨迹原样）。

| # | 判据 | 结果 |
|---|---|---|
| 1 | 搬家无回归 | 待跑 |
| 2 | 解析单测（含两格都为 `None`） | 待跑 |
| 3 | 端到端：本地库没有的景点答出时段 | 待跑 |
| 4 | 不回归：本地库优先，不多打一次高德 | 待跑 |
| 5 | 不回归：两层都没有仍如实说查不到 | 待跑 |
| 6 | 不回归：零参数那条路逐字一致 | 待跑 |
| 7 | `pytest` 全绿 | 待跑 |
| 8 | 工具名单两条判据 | 待跑 |

---

## 七、遗留

- **高德 `opentime2` 覆盖 ~66%**（四城实测 907/1381）。没有的那 34% 两层都答不了，
  只能照实说"没查到"——**这是数据的边界，不是这次的 bug**。
- 节假日特殊安排在 `opentime2` 里是与常规时段混写的（「清明节,劳动节,端午节…」），
  解析器**有意只取常规时段**，不解释节假日。用户问"国庆开不开"答不了，列入遗留。
- `ASK_ATTRACTION_LOOKUP_FIX.md` §6.1 的 **JSON 信封回落**仍未处理，与本次无关。
- 本地库别名未覆盖简称（同上 §七第 1 条），本次不碰。

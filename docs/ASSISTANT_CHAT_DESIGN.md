# 把「方案追问」抽成独立的旅行助手页

> 2026-10-13。**§一~§九 全部已实现并验收**（§六 / §八 / §九 各有一份验收记录）。
> 上游：`docs/ASK_AGENT_DESIGN.md`（追问 agent 本体）、`docs/ASK_ATTRACTION_LOOKUP_FIX.md`（查库那条链）。
> **§九 §9.4 列了还没做的两条**，下一轮从那里接。

---

## 一、这件事是什么

现在「方案追问」是**结果页里的一节卡片**（`index.html:360-385`），夹在「Agent 轨迹」下面，
用户得一路滚到底才看得见。要把它抽出来，做成**整屏的聊天式助手页**——像豆包 / DeepSeek 那样
一屏对话，但它背后是**能调工具、能改方案的 agent**。

顺带把「Agent 轨迹」（`index.html:214` / `:350-358`）从结果页摘掉。

摘完之后结果页**滑到底正好是「每日行程」的最后一天**，这是这次调整想要的自然收尾。

### 本次做 / 不做

| | 做 | 不做 |
|---|---|---|
| 页面 | 新增 `#assistant-page` 整屏视图；结果页摘掉轨迹与追问两节 | **多轮对话**（模型不看历史） |
| 交互 | 聊天式消息列表；**假流式**；**实时工具调用** | 旧消息过期处理（见 §五第 2 条） |
| 后端 | 新增 SSE 流式端点 | **改动 `/api/trips/ask` 现有行为** |
| 方案 | 沿用同文档内存里的 `currentTripData` | 方案落库、登录 |

---

## 二、现状（对着代码，非推测）

### 2.1 单文档、多视图

`main.py:55` 挂的是 `StaticFiles(directory=_STATIC_DIR, html=True)`——**只有一个 `index.html`**。
里面是两个并列的顶层 section，靠 `.hidden` 切换：

| 视图 | 位置 |
|---|---|
| `#landing-page` | `index.html:15` |
| `#result-page` | `index.html:195`（初始 `.hidden`） |

追问是 `#result-page` 内部的**一节**（`index.html:360`），靠 `syncNavToScroll`（`app.js:90-104`）
做 scroll-spy 高亮。`:94-97` 还专门为它写了一句特判：

```js
// 滚到底就直接认最后一节：否则「方案追问」够不到激活线，高亮会退回上一节
```

**「方案追问」是页面最后一节，这条特判就是为它写的。** 它一走，这段要重新看。

### 2.2 方案活在内存里——所以"同文档"这个选择是零成本的

`app.js:26-27`：

```js
let currentTripData = null;   // 整份方案
let currentPayload = null;    // 生成它时的原始请求
```

`renderResult`（`app.js:707-708`）赋值。**换页面就没了，换视图还在。**
所以助手做成同文档的整屏视图（而不是独立 .html），方案**不用序列化、不用落库**，
直接读这两个变量——这正是它成本最低的原因。

### 2.3 后端一次性返回，工具调用数据是现成的

`ask_controller.py:16-21` 的 `POST /api/trips/ask` 返回一个完整的 `AskResponse`（含 `traces`）。

而 `traces` 的来源是 `AskContext.record()`（`ask_context.py:38`）——**它是每一次工具调用的唯一出口**，
七个工具全部经由它留痕（`ask_tools.py` 里每处 `context.record(...)`）。

```python
def record(self, tool_name, action, input_summary, output_summary, status="success") -> None:
    ...
    self.traces.append(AgentTrace(agent_name=..., action=..., input_summary=..., ...))
```

**这是本次设计的关键**：实时工具调用展示不需要重写任何 agent 逻辑，
只要在 `record()` 末尾加一个回调，把同一条数据同时推给前端。

### 2.4 轨迹现在被追问"吃掉"

`app.js:762-763`：每次追问完，强制展开轨迹区并**覆盖**成追问自己的 trace：

```js
renderAgentTraces({agentTraces: data.traces || []});
toggleAgentTraceSection(true);
```

`ASK_AGENT_DESIGN.md:212` 把这件事记成遗留（"追问的留痕会覆盖当次规划的 18 条轨迹"）。
**这次摘掉轨迹区，正好把这个遗留一并收掉**——追问的 trace 改到助手页里显示，两不相干。

---

## 三、改法

### 3.0 链路对照

**改前**（用户在结果页滚到底）：

```
① 生成方案 → renderResult → currentTripData / currentPayload 存内存
② 用户滚到底 → 看到「Agent 轨迹」卡片 → 再往下是「方案追问」卡片
③ 输入问题 → POST /api/trips/ask → 一次性 JSON
④ answerBox.textContent = data.answer          ← 单条，下次覆盖
⑤ renderAgentTraces + 展开                      ← 覆盖掉方案自己的轨迹
```

**改后**：

```
① 生成方案 → 同上（不变）
② 用户滚到底 → 「每日行程」最后一天收尾          ← 轨迹、追问两节都不在结果页了
③ 点「旅行助手」→ 切到 #assistant-page          ← 同文档整屏视图，方案仍在内存里
④ 输入问题 → fetch(POST /api/trips/ask/stream)  ← SSE
⑤ event: trace  × N                             ← 每调一次工具推一条，实时显示
⑥ event: answer                                 ← 完整答案一次推来
⑦ 前端打字机播放                                 ← 假流式，见 §3.4
⑧ 判成 need_change → 消息内确认卡片 → /adjust    ← 复用现有链路
```

### 3.1 新增 `#assistant-page`

> ⚠️ **这一条已被 §八 推翻**（用户要求左栏保留）。保留在此只为记录当时的取舍，别照着改。

与 `#landing-page` / `#result-page` **同级**的顶层 section（`index.html:193` 与 `:195` 之间或 `:399` 之后），
初始 `.hidden`。切换沿用既有的 `.hidden` 做法，不引入路由。

页面骨架：

```
#assistant-page
├── 顶栏：← 返回方案      旅行助手
├── #assistant-messages   消息列表（滚动区）
│   ├── .msg-user         用户气泡
│   └── .msg-assistant    助手气泡
│       ├── .msg-tools    工具调用条目（实时出现）
│       └── .msg-text     答案（打字机）
│       └── .msg-confirm  改方案确认卡片（按需）
└── #assistant-input      输入区（textarea + 发送）
```

**没有方案时**：直接复用 `app.js:734-737` 那句判断，提示"请先生成一次旅行计划"并给一个回首页的按钮。

### 3.2 摘掉「Agent 轨迹」

**这不是删一段 HTML 那么小——`app.js` 里有 8 处联动，加上 2 处 HTML 共 10 处，
漏一处就在启动时抛异常。**

| # | 位置 | 处理 |
|---|---|---|
| 1 | `index.html:214` 导航项 | 删 |
| 2 | `index.html:350-358` section | 删 |
| 3 | `app.js:15-16` 两个 `getElementById` | 删 |
| 4 | `app.js:110-112` 导航点击特判 | 删 |
| 5 | `app.js:123` `agentTraceToggle.addEventListener` | 删 —— **不删就崩** |
| 6 | `app.js:698-705` `toggleAgentTraceSection` | 删 |
| 7 | `app.js:717` `renderResult` 里的调用 | 删 |
| 8 | `app.js:762-763` 追问后的调用 + 展开 | 删，改推到助手页 |
| 9 | `app.js:1255-1329` `renderAgentTraces` | **删** —— 理由见 §五第 5 条 |
| 10 | `app.js:94-97` 滚到底特判 | 重新看还需要不需要（最后一节变成「每日行程」） |

**第 5 条是坑**：`agentTraceToggle` 是 `const`，元素一删就是 `null`，
`null.addEventListener` 在脚本初始化时**直接抛错**，后面所有绑定全挂——页面看起来"没反应"，
但控制台第一行就能看到。第 3、6、7 条同理，都是"元素没了但 JS 还在碰它"。

**第 9 条是删，不是留。** 读完那 65 行才定的：它**约 40 行在处理 `loopSummary` /
`loopIterations`**（`:1273-1307`），那是**规划链专用**的，助手页用不上；它的形态是
"事后翻日志"的 `<details>` 折叠块，与"聊天里的小条目"不是一回事；而且它绑死了
`agentTraceList` 与 `toggleAgentTraceSection`，这两个也都要删。助手页另写一个小渲染器。
git 里有历史，将来真要用 `git show` 取回即可。

### 3.3 聊天式布局——但**不是多轮**

**关键区分**：消息在**屏幕上**一条条堆着，但**模型看不到历史**。

- 前端：`askAboutCurrentTrip` 现在是把 `answerBox.textContent` 覆盖（`app.js:761`），
  改成**往 `#assistant-messages` 里 append 两条**（用户气泡 + 助手气泡）。
- 后端：`ask_agent.py:127` 每次只塞一条 `HumanMessage`，`create_agent`（`:152-156`）没接 checkpointer
  ——**这一层本次一行不动**。

所以是"聊天样子的单轮"。用户问「那开放时间呢」，「那」接不上——**这是已知且接受的**，
多轮那一档再补。

### 3.4 假流式 + 实时工具调用 = SSE

用户已确认走**假流式**。要拆成两半说，因为两者做法不同：

| | 怎么流 | 为什么 |
|---|---|---|
| **答案** | **假流式**：agent 照常跑完，拿到完整 `answer`，**一次性**推给前端，前端本地打字机播放 | 规则 9 要求模型输出 JSON 信封（`{"intent":"answer","answer":"..."}`）。真逐 token 流，用户会先看到 `{"intent":"answer","answer":"` 这堆脚手架。且 `ASK_ATTRACTION_LOOKUP_FIX.md §6.1` 记着实测答疑类 **4/4 不穿 JSON 壳**——模型本来就不老实 |
| **工具调用** | **真流式**：`record()` 时立刻推 `event: trace` | 工具调用发生在答案**之前**，是天然的进度条。等答案出来再一起给，就失去"实时"的意义了 |

**事件协议**（`POST /api/trips/ask/stream`，`text/event-stream`）：

```
event: trace
data: {"agentName":"search_attractions_by_tag","action":"查本地景点库",
       "inputSummary":"城市=广州，景点名=广州塔","status":"success"}

event: answer
data: {"answer":"广州塔开放时间是 09:30-22:30……","intent":"answer",
       "adjustmentPrompt":null,"anchorHint":null,"pinHints":[]}

event: error
data: {"message":"景点检索失败：…"}
```

**后端实现**：

- `AskContext` 加一个可选回调字段：`on_event: Callable[[AgentTrace], None] | None = None`，
  `record()` 末尾调用它（`ask_context.py:38-48`）。**默认 `None`，现有链路行为零变化。**
- `AskAgent` 新增流式入口，**`ask()` 的签名与行为一律不动**。
- 端点用 `StreamingResponse` + `asyncio.Queue`：`ask()` 是同步的，跑在线程里，
  回调从线程 `loop.call_soon_threadsafe` 往 queue 里放。
- **新增依赖为零**：`fastapi` 自带 `StreamingResponse`，不需要 sse-starlette。
  （这个项目的 `requirements.txt` 只有 10 行，是刻意省的。）

**前端实现注意**：`EventSource` **只支持 GET**，而这里的方案必须走 POST body。
所以要用 `fetch` + `response.body.getReader()` 手动解析 SSE 帧，**不能用 `EventSource`**。

**只推工具调用，不推 agent 自己的留痕**（实现时补的一条，2026-10-13）：
`finishPendingBubble` 之外，`AskAgent._emit_trace` 还会滤掉 `agentName == "AskAgent"` 的那几条
（"判定意图并生成回答" / "解析模型最后一轮输出" / "归一化改方案诉求"）。两个理由：

1. 它们**不是工具调用**，混进去只会在答案前面挤三条噪声；
2. **踩判据 5**：其中"解析模型最后一轮输出"那条的 `inputSummary` 装的是**模型原始输出**
   （`ask_agent.py:225` 的 `raw[:120]`）。模型吐个残缺 JSON 信封时，这段原文就会把
   `{"intent":"answer","answer":"` 直接摆进聊天里。

**数据不丢**：它们照旧进 `AskResponse.traces`，只是不在聊天里显示——与 §五第 5 条同一个口径。

### 3.5 改方案的确认卡片搬家

现有链路（`app.js:787-859`）**逻辑不动**，只换容器：

```
showAskConfirmation → showHotelMoveCard（可选一问）→ showFinalAskConfirm → confirmAskChange
```

今天它渲染到 `#ask-confirm` / `#ask-hotel-move` 两个固定 div（`index.html:374-383`）。
改后作为**一条消息里的卡片**插进 `#assistant-messages`。

`/adjust` 那条链（`runTripAdjustment`）与重规划遮罩（`index.html:401-407`）**完全不动**。

**实现时定的三条**（2026-10-13）：

1. **卡片不是新写的 DOM，是整体搬过去。** 两张卡的 ID 与事件绑定一个没动，
   只是把它们收进一个新容器 `#ask-cards`，容器住**消息区里**。每轮追问
   `moveAskCardsToEnd()` 把它挪到最末尾——于是"这张卡接在哪句答案后面"看得出来。
2. **`resetAssistant` 要把它捡回来。** 容器住在消息区里，而 `resetAssistant` 是
   `assistantMessages.innerHTML = ''`——不显式 `append` 回去，`getElementById('ask-confirm')`
   下一轮就返回 `null`，show/hide 全崩。这是本页第二个"元素没了 JS 还在碰它"的坑（第一个见 §3.2 第 5 条）。
3. **卡片等答案打完再显形。** 答案还在逐字写就弹"要按这句话重新生成吗"，
   用户还没读完就被催着做决定。`playTypewriter` 因此多收一个 `onDone`；
   配一个 `askTurnId` 作废过期回调——用户等不及先问了下一句，上一句挂的回调就不该再弹卡。

### 3.6 入口与返回

- `index.html:215` 的导航项「方案追问」→ 改成**按钮**（不再是 scroll target）：点它切到 `#assistant-page`。
- 助手页顶栏一个「← 返回方案」：切回 `#result-page`。
- **`syncNavToScroll` 要跟着改**：`navItems`（`app.js:23`）会少两个（轨迹、追问），
  其中「方案追问」原来是**最后一节**，`:94-97` 那条滚到底特判为它而写，要重新确认。

---

## 四、判据（怎么算改好了）

1. **结果页收尾正确**：生成方案后滑到底，**最后看到的是「每日行程」的最后一天**，
   页面上找不到「Agent 轨迹」和「方案追问」两节。
2. **无 JS 崩溃**：打开结果页，控制台**零报错**（重点验 §3.2 第 5 条那个 null 坑）。
3. **能进能出**：从结果页进助手、从助手返回结果页，**方案与地图状态还在**（不重新生成）。
4. **实时工具调用**：问「广州塔开放时间」，**答案出现之前**工具条目已经逐条显示出来。
5. **假流式**：答案**逐字出现**，且**从头到尾看不到 JSON 脚手架**。
6. **不回归（原端点）**：`POST /api/trips/ask` **一字不动**，行为与返回值与改动前逐字一致。
7. **不回归（改方案链）**：判成 `need_change` 时确认卡片正常出现，
   点确认后 `/adjust` 正常触发、重规划正常渲染。
8. **没有方案时不崩**：直接进助手页（无 `currentTripData`），提示要先生成方案，**不抛异常**。
9. `pytest` 全绿（现有 **325 passed**，2026-10-13 本机实测）。

---

## 五、明确不做

1. **不做多轮。** 模型不看历史（前端堆消息、后端单条）。问「那开放时间呢」接不上，
   这是**已知并接受**的，不是漏。多轮要动 `ask_agent.py:127`、`create_agent` 的 checkpointer，
   以及提示词规则 2 那句「**本轮**」——那是独立的一档。
2. **不做旧消息过期处理。** 方案被 `/adjust` 改掉后，聊天记录里关于旧方案的答案会**留在屏幕上**，
   与新方案并存。这是**有意接受**的：绝大多数对话产品也是这么做的，
   为它去"更新页面"反而把问题想复杂了。
3. **不做真流式。** 答案不逐 token 从模型流，理由见 §3.4。
4. **不落库、不登录、不做方案持久化。** 方案仍然只活在 `currentTripData` 内存变量里，
   刷新即失效——与今天一致。
5. **不保留旧的轨迹渲染函数。** `renderAgentTraces`（`app.js:1255-1329`）**删除**，
   助手页的工具调用条目另写一个小的渲染器。理由见 §3.2 第 9 条那段。
   **数据不删**：`AskResponse.traces` 照旧返回，只是换了显示的地方。
6. **不动 `/api/trips/ask`。** 新开流式端点，老的留着（判据 6 守着）。

---

## 六、验收记录

> 2026-10-13 实测。假流式那几条用 `/tmp/assistant_stream_probe.py`：它另起一个 stub 服务，
> mount 同一份 static 目录，把 `/api/trips/ask/stream` 换成**带 sleep 的真流**。
> 之所以不用 Playwright 的 `route.fulfill`——那是一次性给完整个 body，
> 帧会一瞬间全到，"答案之前工具条目已经在了"就根本验不出来。

| # | 判据 | 结果 |
|---|---|---|
| 1 | 结果页收尾为「每日行程」最后一天 | ✅ 视觉序 `summary→overview→budget→map→weather→**days**`，days 底边 4475 / 文档高 4737；轨迹与追问两节已不在页上 |
| 2 | 控制台零报错 | ✅ 探针 `console_errors: []`、`bad_responses: []`（唯一 404 是 stub 没实现的地图配置，补上后归零） |
| 3 | 进出助手方案不丢 | ✅ 进出前后 `currentTripData` 不变、滚动位置还原 |
| 4 | 实时工具调用先于答案 | ✅ 采样到第 1 条工具条目时 `answerText` 为空、标签还是"正在继续…"；**真链路**上工具帧比答案帧早 1.7s |
| 5 | 假流式且无 JSON 脚手架 | ✅ `partial_len` 1→5→50 逐字增长、是完整答案的前缀、全程无 `{"intent"` / `"answer"` |
| 6 | 原 `/ask` 端点逐字不回归 | ✅ `ask()` 默认 `on_event=None` 路径未动；`325 passed` 与开工前一致 |
| 7 | 改方案链不回归 | ✅ 普通改预算 → 确认卡 → `POST /adjust {adjustmentPrompt:"预算改成 2500"}`；pin 路线 → 挪酒店那一问 → `{pinHints:["西溪国家湿地公园"],pinDay:1,pinMoveHotel:false}` |
| 8 | 无方案时不崩 | ✅ 停在结果页，toast「请先生成一次旅行计划，再使用旅行助手」 |
| 9 | `pytest` 全绿 | ✅ `325 passed, 1 warning in 19.64s` |

**判据 7 的注意点**：`/adjust` 之后**不**清空聊天记录，这是 §五第 2 条有意接受的，
不是漏——写验收脚本时别把它当成 bug（我先写反过一次）。

---

## 七、遗留

- ~~**多轮**~~：已了（2026-10-14）。设计见 `ASK_MULTITURN_MEMORY.md`。
  当初这里记的三件事里有两件是错的，落地时纠正了：
  **不需要 checkpointer**（`create_agent` 默认 `checkpointer=None`，传一个多元素
  `messages` 列表就行，零新依赖）；**规则 2 的「本轮」不能改成「本会话」**——
  它正是防止把历史里的旧数字当事实用的那道闸门，改了反而是放开。
- ~~**`agent-trace-section` 的 CSS 归属**~~：已了。`#ask-answer` 整块随追问卡一起删了，
  静态目录里 grep `ask-answer` / `agentTraceToggle` / `agentTraceList` /
  `toggleAgentTraceSection` / `renderAgentTraces` 均无残留。
- ~~**`app.js:94-97` 滚到底特判**~~：已了，**保留**。它当初为「方案追问」而写（那一节在文档最末、
  够不到激活线），现在最后一节是「每日行程」——**长短取决于行程天数**，短行程同样够不到线。
  实测滚到底高亮正确落在「每日行程」。
- **真链路只验了一次答疑类问句**（2026-10-13，灵隐寺门票）。它走的是 JSON 回落那条路
  （`status: "fallback"`），与 `ASK_ATTRACTION_LOOKUP_FIX.md §6.1` 记的一致。
  **`need_change` 那条路没在真链路上跑过流式**——判据 7 是用 stub 验的。
  要补的话就是再花一次真实调用问一句"预算改成 2500"。
- **`#ask-cards` 是个例外**：本页别的地方都是「元素删了就删了」，只有它必须由
  `resetAssistant` 显式捡回来（理由见 §3.5 第 2 条）。以后谁再往消息区里塞固定 DOM，
  记得同一件事。
- **`ASK_LIVE_HOURS.md` 仍未落地**，与本次无关。
- **排期未定**：`planner_agent.py:484` 那个静默 under-fill（每天只去一个景点，上一轮未复现）
  仍未处理。

---

## 八、改版：左栏保留，只换右半边（2026-10-13 用户要求）

> **这一节推翻 §3.1 / §3.6 的「整屏视图」**。§一~§七 其余部分仍然有效，已实现的部分不回退。

### 8.1 要什么

用户原话：

- 「左边还是行程概览，只是右半部分抽取出来」
- 「旅行助手这个可以放在左边的最下面并且样式可以和导航栏不一样突出一点」

即：左栏（`aside.side-nav`）**全程在**，只有右半边在「方案」与「助手」之间切换。
「旅行助手」不再是 `.nav-list` 里的一个导航项，移到左栏**最底部**，样式独立、更显眼。

### 8.2 DOM

`#assistant-page` 从「与 `#result-page` 同级的顶层 section」改成 `.result-layout` 的**第二个格子**：

```html
<div class="result-layout">        <!-- grid: 220px 1fr -->
    <aside class="side-nav">…</aside>          <!-- 一个字不动 -->
    <main class="dashboard-main">…</main>      <!-- 方案 -->
    <section id="assistant-panel" class="assistant-panel hidden">…</section>
</div>
```

切换 = `dashboard-main` 与 `assistant-panel` 互相加/去 `.hidden`。
**`#result-page` 不再藏**，所以左栏天然留着——这也是这一版比上一版简单的地方。

### 8.3 必须锁页高（本改版唯一的硬骨头）

`.result-page` 是 `min-height: 100vh`、**整个文档滚**；而助手要求消息区自己滚、输入框钉在视口底。
两者不能共存，所以助手打开时给 `#result-page` 加 `is-assistant`：

```css
.result-page.is-assistant { height: 100vh; overflow: hidden; }
.result-page.is-assistant .result-scene { display: none; }   /* 页脚那 230px 不藏，输入框钉不到底 */
.result-page.is-assistant .geo-corner { display: none; }     /* 方案自己的角注，助手页上没意义 */
.result-page.is-assistant .result-layout { align-items: stretch; }
```

`.side-nav` 是 `position: sticky`，在 `overflow: hidden` 的容器里 sticky 会失效——
但那时**内容本来就不滚**，左栏自然贴顶，正是想要的。

### 8.4 我建议的四个决定

| # | 事项 | 决定 | 理由 |
|---|---|---|---|
| 1 | 助手自己的「← 返回方案」 | **宽屏删、窄屏留**（见 8.7） | 宽屏左栏一直在，点任一导航项就回去了，多一个"回"反而分不清；窄屏左栏收起，非得有它 |
| 2 | 顶部那行（返回首页 / 编辑行程 / 导出行程） | **保留** | 「返回首页」在助手页同样有用；它是 `.result-layout` 的兄弟，不动最省事 |
| 3 | 左栏 5 个导航项在助手页点了怎么办 | **切回方案页并滚到那一节** | 用户在助手页想问「第二天为什么这么赶」，点「每日行程」就能回去看一眼再切回来 |
| 4 | 助手页的 `#geo-corner` 坐标读数 | **藏掉** | 它是方案自己的角注。上一版就是因为漏了这类"结果页固定元素"才穿帮过一次 |
| 5 | 助手页的海岸线底纹（`.result-page` 的 `background-image`） | **藏掉** | 实现时才发现：它是 `bottom center` 给四五千像素长文档垫底的，页一锁成视口高就顶到视口中间、卡在输入框后面 |
| 6 | 助手面板自己的底 | **白卡**，与 `.side-nav` 同一套皮 | 用户提的：这一页上别的块都是白卡，只有它直接浮在天空渐变上，看着突兀。连带把助手气泡从 `--card` 改成 `--paper`——面板自己已经白了，气泡再白一层就分不出边界 |

### 8.5 「旅行助手」按钮

从 `.nav-list` 里拿出来，放到 `aside.side-nav` 的**最后**，与上面的导航列表用一条分隔线隔开：

```
┌─────────────────┐
│ 行程概览         │  ← .nav-head，蓝底
├─────────────────┤
│ 行程总览         │
│ 预算明细         │
│ 景点地图         │
│ 每日行程         │
│ 天气信息         │
├─────────────────┤  ← 分隔线
│ ✦ 旅行助手       │  ← 主色实底 + 白字，与导航项明显不同
└─────────────────┘
```

用 `--sea` 实底 + 白字（与 `.nav-head` 同色，读作"同一条主线上的东西"），hover 略深。
助手页打开时切到选中态：保持实底，另加一条向内的左侧竖条，与 `.nav-item.active` 呼应。

### 8.6 新增判据

| # | 判据 |
|---|---|
| 10 | 助手页左栏仍在，5 个导航项都在，「旅行助手」在最底部且样式与导航项明显不同 |
| 11 | 点左栏任一导航项 → 回到方案页并滚到对应节 |
| 12 | 助手页输入框钉在视口底、消息区自己滚；**整个文档不滚** |
| 13 | 助手页看不到 `.result-scene` 页脚风景带、看不到 `#geo-corner` |
| 14 | 375 / 1024 / 1440 三档都不横向溢出，且消息区高度够用 |
| 15 | 原判据 1~9 全部仍然成立（尤其 3 进出不丢方案、7 改方案链） |

### 8.7 窄屏：左栏让位给对话（实现时才发现，2026-10-13）

「左栏一直在」这条**只在真的有左栏的时候成立**。

`result.css` 的 `@media (max-width: 1100px)` 把 `.result-layout` 改成单列、`.side-nav`
改成 `position: static`——那一档起左栏就不再是"左边一栏"，而是**上面一坨**。
再加上页高被锁成 100vh，它直接把对话挤没：

| 视口 | 改前消息区高度 | 改后 |
|---|---|---|
| 375px | **48px**（对话根本没法看，是坏的） | 356px |
| 1024px | 81px | 502px |
| 1440px | 602px（两列，本来就对） | 602px |

所以加了一档媒体查询：**≤1100px 且助手打开时收起 `.side-nav`**，把整屏还给对话；
同时把「← 返回方案」还回来（它默认 `display: none`，只在这一档露面）。
上一版整屏助手在手机上本来就好用，靠的就是这个——现在只是把它限定在真正需要的宽度上。

不改这条的话，等于"为了桌面上的一个布局要求，把手机端弄坏了"。

---

## 九、排版与常驻顶栏（2026-10-13 用户第二轮反馈）

用户原话：

- 「往下滑的时候这个返回首页还是要有的，就是左边的导航栏位置固定」
- 「感觉这个页面可以再调整一下看起来不太舒服」
- 「像 deepseek 一样，左右两边留白，然后字体可以稍微小一点」

### 9.1 返回首页常驻（结果页）

`.top-actions` 原来是普通流内元素，跟着文档滚走——结果页四五屏长，滚到下半段就找不着了，
而它是**离开本页的唯一出口**。改成 `position: sticky; top: 0`。

两个坑：

1. **左栏也是 sticky，会叠在一起。** `.side-nav` 原来是 `top: var(--sp-4)`，
   和顶栏同在视口顶端。顶栏高度提成变量 `--top-actions-h`，
   左栏改成 `top: calc(var(--sp-3) + var(--top-actions-h))` 正好让开。
2. **底色不能一直挂着。** 一进页面它在蓝天顶上会显出一条浅横带。
   改成滚起来才由 `app.js:syncTopActionsBacking()` 加 `.is-stuck`
   （`rgba(--sky-low, .86)` + `backdrop-filter: blur(10px)`）。
   往下滚时 body 那条天空渐变本来就收到 `--sky-low`，越往下越融得进去。
3. **高度必须写 `min-height`。** ≤720px 时既有媒体查询把这三个按钮竖排，
   写死 `height: 54px` 会让它们溢出、压住助手的头（实测压到了）。

### 9.2 助手页排版

| 改动 | 原 | 现 | 为什么 |
|---|---|---|---|
| 内容列宽 | 跟着面板走，1440 屏上到 1044px | `--assistant-col: 720px` 居中 | 一行中文横跨一千像素没法读，气泡也被拉得很扁。就是 DeepSeek 那种两侧留白 |
| 正文字号 | `--fs-md` 16px | `--fs-sm` 14px | 对话是"读一段接一段"，比正文再小一档更耐看，一屏也多放得下两轮 |
| 标题字号 | `--fs-xl` 24px | `--fs-lg` 18px | 整页就它一个 24px，压得对话本身抬不起头。收到与 `.nav-head` 同量级 |
| 输入区 | 文本框 + 占满整行的 `发送` 大按钮 | 一个整体盒子，圆形箭头钮坐右下角 | 用户的参照物。前者读起来是"两个控件"，后者才是"一个输入框" |

**发送钮改成图标带来的连带改动**：`askAboutCurrentTrip` 里原来会写
`button.textContent = '追问中...'`——那会把钮里的箭头 SVG 冲掉。已删掉，
"在跑"改由左边的状态行 + 钮变灰呼吸表达。

**标题栏不吃那条限宽**（用户后续指出）：`.assistant-top` 一度和消息区一起被收进
720px 中线里，于是宽屏上「旅行助手」四个字离卡片左边缘两百多像素，看着像缩进错位。
它是**卡片自己的头**，不是对话内容——改成占满卡宽、贴左边缘；
只有 `.assistant-messages` 与 `.assistant-composer` 留在中线里。

**CSS 优先级坑**：`controls.css` 的 hover 规则是
`.textarea:hover:not(:disabled):not(:focus)`（0,3,1），
用 `.assistant-composer .textarea:hover`（0,2,1）**压不住**，输入框会被改回灰描边。
要照着它的形状写到更高一档。

### 9.3 三条交互修复（2026-10-13 用户第三轮反馈）

**① 往上翻会被拉回底部——这个是 bug。**

`playTypewriter` 每 16ms、`appendToolEntry` 每来一条都无条件调
`scrollAssistantToBottom()`。流还没跑完时用户想翻上去看旧消息，会被一帧一帧拽回底部，
根本翻不动。"跟随最新内容"变成了"锁死在底部"。

改法：加 `assistantStick` 状态，由消息区自己的 `scroll` 事件维护；
`scrollAssistantToBottom()` 默认尊重它，只有 `appendMessage`（轮次边界，用户刚发问）
才 `force`。

**状态必须在 scroll 事件里维护，不能每次现算**：新内容一 append，`scrollHeight` 就变高，
"离底多远"立刻失真——明明没动过的用户会被判成"已经翻上去了"，从此再不自动跟。
实测：把 `scrollTop` 归零后，流还在推帧的一秒里它停在 0；手动滚回底部后自动跟随恢复。

**② Enter 发送 / Shift+Enter 换行。**

关键细节是**组字期间必须放行**：中文输入法用 Enter 上屏，那一下是"确认候选词"不是"发送"。
`event.isComposing` 是标准信号，`keyCode === 229` 是部分 IME 的老写法，两个都看。

顺带补了一个 double-submit：`askAboutCurrentTrip` 开头加 `if (button.disabled) return`。
点按钮那条路走不到这儿（disabled 的按钮不触发 click），但 Enter 是直接调函数的，
上一轮还在跑时按 Enter 会并发出第二次请求。

**③ 空态摆三个可点的示例问题。** 光一句说明，用户未必知道能问到什么颗粒度。
用事件委托绑在 `#assistant-messages` 上——空态节点会被 `resetAssistant` 摘下来再挂回去，
绑在按钮本身上就得跟着搬。文案刻意不写死城市名（这份方案可能在任何城市）。

### 9.4 还没做的（留给下一轮）

1. **窄屏顶栏 142px。** ≤720px 三个按钮竖排，手机上占掉五分之一屏。
   可以收成一行，或者助手页只留「返回首页」。**用户说先不改，看看后面情况。**
2. **助手页没有清空对话的入口。** 现在只有"换一份新方案"才会清。同上，先不改。

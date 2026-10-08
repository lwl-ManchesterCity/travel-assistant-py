# 创造性增强 ·「出票」编排动效 + 两处物料补强

> 对象：执行前端重构的 agent
> 前置：`FRONTEND_FEEDBACK_LANDING_TICKET.md` 已执行完毕（当前着陆页物料齐、验收全过）
> 依据：`docs/FRONTEND_REDESIGN_PLAN.md` v1.3 全部沿用；frontend-design skill 原则：大胆只花在一个地方，其余保持安静
> 问题判定：当前页面"正确但平"——票据物料齐了但全部静态，没有一个被记住的瞬间。生成计划是产品最天然的高光时刻，现在只是一条细进度条

---

## 一、主创意：「出票」四幕编排动效（唯一的大动作）

把"提交 → 生成 → 出结果"变成一条完整的出票故事线。**总时长预算 ≤ 1.2s（不含接口等待），只用 `transform` / `opacity`，全部 `prefers-reduced-motion` 降级。**

### 第 1 幕 · 受理章（点击提交瞬间）

- 提交按钮上方（或按钮正中）盖下红章"受理"：`--stamp-red` 底白字、等宽、`rotate(-6deg)`
- 动效：`scale(1.6) rotate(-12deg) opacity(0)` → `scale(1) rotate(-6deg) opacity(1)`，0.25s `cubic-bezier(0.2, 1.4, 0.4, 1)`（回弹模拟盖章手感）
- 与结果页"已出票"章（-4°）角度错开，两章不重复

### 第 2 幕 · 打印中（接口等待期）

- 替换现有细进度条：一条从"打印口"（进度区上缘）向下延伸的 **2px dashed 竖线**（`--line-strong`），像纸从打印机吐出
- 右侧等宽百分比 `PRINTING… 47%`（`--font-mono`、`--fs-xs`、`--muted`），百分比沿用现有进度数据，不改数据来源
- 虚线延伸动画：`background-position` 循环位移 0.8s linear infinite（走纸感）

### 第 3 幕 · 票根打印滑出（结果页入场）

- 结果页票根头卡入场：`translateY(-110%)` → `translateY(0)`，0.7s `cubic-bezier(0.16, 1, 0.3, 1)`（先快后缓，纸落定）
- 票根顶部外沿加一条 8px 高的"出票口"深色细槽（`--ink` 10% 透明度），票根从槽里出来的错觉
- 其余结果页内容正常淡入，不做花样

### 第 4 幕 · 落章定格

- "已出票"章在票根滑停后 0.15s 落章（同第 1 幕动效参数，角度 -4°），定格即终态
- 四幕时序：1（提交时）→ 2（等待期循环）→ 3（结果渲染时）→ 4（3 结束后 150ms）

### 技术约束（红线）

- JS 只允许加 class 钩子与展示字符串（属方案约束 (b) 允许范围）：请求 / 分支 / 渲染流程一行不动
- 全部动画包在 `@media (prefers-reduced-motion: no-preference)` 里；reduced-motion 下直接显示终态
- 不引任何动画库，纯 CSS animation/transition
- 第 2 幕替换的是进度条的**视觉层**，原有进度数据流不动

---

## 二、辅助创意 A：委托单左侧打孔列（静态物料）

- 表单外壳（planner-shell 对应容器）左缘内侧加一列连续半圆孔，纵向均布，孔径 10px、间距 28px
- 画法：单列 `radial-gradient` 重复背景（纸底色圆点压白卡），**属渐变白名单 #2 既有用法**；在 `visual_acceptance.py` 的 `GRADIENT_WHITELIST` 同步加 `.punch-holes`
- 孔列右侧 12px 处加一条 1px 纵向 dashed 撕缝线（`--line`），与顶部打孔线构成完整"存根联"

## 三、辅助创意 B：天数翻牌（split-flap，小动作）

- 「旅行天数」只读框的数字（当前"3 天"）在日期变更时用机场信息屏翻牌动作更新：上半张向下翻 `rotateX(-90deg)` 0.15s → 换新值 → 下半张翻回 0.15s
- 字体本来就是 `--font-mono`，翻牌牌面加一条 1px 水平中线（`--line`）模拟翻牌轴
- reduced-motion 直接换文本；实现允许用 JS 切 class（展示层），不改天数计算逻辑
- 只做这一个字段，不扩散到其他输入框（克制）

---

## 四、验收（机器判据 + 动效专项）

1. `pytest` 86 项全绿
2. 探针 `--label creative`：四条判据全过（**先把 `.punch-holes` 加进 `GRADIENT_WHITELIST` 再跑**）
3. 动效专项（人工过一遍 + 探针截图佐证）：
   - 第 1/3/4 幕各截一帧存 `scripts/evidence/`（受理章、票根滑出中、落章定格）
   - DevTools 开 `prefers-reduced-motion: reduce` 重跑流程：无任何动画、终态直接显示
   - 全流程（不含接口等待）动画总时长 ≤ 1.2s
4. 1280 + 375 双视口截图对比：打孔列在 375px 下保留（宽度压缩但孔列可见）
5. `git diff` 检查：JS 改动仅限 class 钩子 / 展示字符串；`result.css` 除第 3/4 幕入场相关规则外无变更

## 五、红线（沿用并补充）

- 不新增色彩 / 字体 token；**允许新增两个动效 token**：`--dur-stamp: 0.25s`、`--ease-stamp: cubic-bezier(0.2, 1.4, 0.4, 1)`，写入 tokens.css 动效小节
- 渐变白名单封顶四选择器：`.ticket-barcode` / `.ticket-notch` / `.perforation` / `.punch-holes`
- 动画只用 `transform` / `opacity`（第 2 幕 `background-position` 为例外，性能无害）
- 创意只落在本单三处，不扩散——其余区域保持现状的安静

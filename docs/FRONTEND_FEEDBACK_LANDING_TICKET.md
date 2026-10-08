# 着陆页票据语言补落地 · 反馈单

> 对象：执行前端重构的 agent
> 前提：P0–P3 已完成，结果页验收通过。本单**只补着陆页**，结果页一行不动。
> 依据：`docs/FRONTEND_REDESIGN_PLAN.md` v1.3（设计方向、tokens、对比度定稿、验收标准全部沿用，不新增、不修改）
> 证据：`scripts/evidence/final-landing-1280.png`（当前状态） vs 方案 §2.5 设计板

---

## 一、问题判定

用户第一屏观感"太简化"。验收判据全过，但**方案的签名元素在着陆页没有落地**——执行把"克制"理解成了"空白"。三条实锤：

| # | 问题 | 证据 |
|---|---|---|
| 1 | 印章红在着陆页使用 **0 次**，整页只剩蓝白灰 | `grep "stamp-red" css/landing.css` → 0 命中（`result.css` 34 处，对比悬殊）。方案 §2.5 明确 section 标题要做"检票章"红标签，执行只做了蓝色编号 |
| 2 | "委托单"没有任何物料感 | 当前是白卡 + hairline 灰边压纸底，无打孔线、无机打编号、无单据细节，看上去就是普通表单 |
| 3 | 等宽字体在着陆页近乎不可见 | `--font-mono` 在 `landing.css` 仅 1 命中（section 编号）。日期（原生 date input 除外）、天数"3 天"、预算"3000"均未套等宽，方案"数据一眼可辨"未落地 |

**不要动的部分**（已完成且验收通过，回归即视为失败）：tokens.css、控件统一（controls.css）、字重收敛、结果页全部（result.css + 票根 + 印章 + 条码）、JS 逻辑。

---

## 二、修复项（共 4 项，全部为 landing.css / index.html 着陆页段的增量修改）

### 修复 1 · 表单外壳加"委托单"物料

位置：`index.html` 着陆页标题区（`<h1>` 与副标题所在块）+ `landing.css`。

1. 标题区改为左右两栏：左侧保持现有标题 + 副标题；右侧加一行等宽机打小字，右对齐：
   ```html
   <div class="form-serial">TRIP REQUEST FORM<br>NO.20261008</div>
   ```
   样式：`--font-mono`、`--fs-xs`、`--muted`、`letter-spacing: 0.08em`、行高 1.8、右对齐。
2. 标题区底部（副标题与第一张表单卡之间）加一条**打孔分隔线**，与结果页票根的打孔线同一画法，保证两页物料呼应：
   ```css
   .perforation {
       border-top: 2px dashed var(--line-strong);
       position: relative;
       margin: var(--sp-5) 0;
   }
   /* 两端半圆缺口：radial-gradient —— 属于渐变白名单 #2 的既有用法，
      在白名单注释中补记 .perforation 选择器 */
   ```
   缺口用 `radial-gradient` 画（沿用白名单 #2，在 `visual_acceptance.py` 的 `GRADIENT_WHITELIST` 中同步加入 `.perforation`，否则探针误报）。

### 修复 2 · section 编号改印章标签

位置：`landing.css` 的 `.section-no`（现蓝色等宽编号）。

- 改为红底白字小印章：`background: var(--stamp-red)`、`color: #fff`、`--font-mono`、`--fs-xs`、`--fw-bold`、`padding: 3px 8px`、`border-radius: var(--radius-sm)`，并整体 `transform: rotate(-2deg)`（印章随手盖的感觉，与结果页"已出票"印章 -4° 呼应但角度错开）。
- 对比度合规：白字压 `--stamp-red` = 4.57，过 AA（见方案 §2.7，这正是"红底白字"的既定用法）。
- 编号文案从 `01` 改为 `NO.1` / `NO.2` / `NO.3`（等宽字下更有机打感；HTML 里改文本即可）。

### 修复 3 · 数据字段套等宽字体

位置：`index.html` 着陆页表单 + `controls.css`（若控件类通用，注意只加规则不改旧规则）。

- 「旅行天数」只读框：内容 `font-family: var(--font-mono)`、数字部分 `--fw-bold`。
- 「总预算」输入框：`font-family: var(--font-mono)`（用户输入数字时即等宽显示）。
- 「开始日期 / 结束日期」：原生 date input 的字体由浏览器渲染，**不强改**；在其 label 右侧加一个等宽的 `YYYY/MM/DD` 格式提示小字（`--fs-xs`、`--muted`、`--font-mono`），把等宽存在感带到这一行。

### 修复 4 · 提交按钮上方加机打小字

位置：`index.html` 提交按钮上方。

```html
<p class="form-ticket-note">** 填写完整后提交 · 规划引擎预计运行 30 秒 **</p>
```

样式：`--font-mono`、`--fs-xs`、`--muted`、居中、`letter-spacing: 0.06em`。星号是票据机打体的特征字符，不是装饰。

---

## 三、验收（机器判据，同方案 §三）

完成后必跑，全部通过才算完：

1. `cd travel-assistant-py && .venv/bin/python -m pytest tests/ -q` —— 86 项保持全绿
2. `/Users/lwl/.workbuddy/binaries/python/envs/default/bin/python scripts/visual_acceptance.py --label polish-landing` —— 四条判据全过（**先按修复 1 把 `.perforation` 加进探针白名单再跑**）
3. 着陆页 1280 + 375 截图存 `scripts/evidence/`，与 `final-landing-1280.png` 对比：
   - 印章红出现 ≥ 3 处（三个 section 标签）且仅用于印章/星号，不铺大面积
   - 打孔线 1 处、机打小字 2 处（编号 + 按钮上方）
   - 天数 / 预算字段肉眼可辨等宽字体
4. 结果页零改动：`git diff` 中 `result.css` 与结果页相关 JS 无变更

## 四、红线

- 不新增 token，不改 token 值（所有颜色 / 字体 / 间距从 `tokens.css` 取）
- 不动 JS 逻辑（修复 2 改 HTML 文本 `01→NO.1` 属展示字符串，允许）
- 渐变只允许出现在白名单（`.ticket-barcode` / `.ticket-notch` / `.perforation`）
- 得意黑已就位（`fonts/SmileySans-Oblique.woff2`），标题字体不要换

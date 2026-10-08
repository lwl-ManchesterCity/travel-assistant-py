# 本地字体包

前端重构方案（`docs/FRONTEND_REDESIGN_PLAN.md` 2.3）要求字体本地打包，不走 CDN，保证答辩时离线可用。

## IBM Plex Mono（数据字体）

- 用途：日期、天数、金额、票号、统计数字
- 来源：`@fontsource/ibm-plex-mono@5`，jsdelivr 拉取 `latin` 子集 woff2，400/500/600/700 四档
- 授权：SIL Open Font License 1.1（IBM 官方发布）

## 得意黑 Smiley Sans（标题字体）

- 用途：仅页面主标题与结果页大标题，克制使用
- 文件：`SmileySans-Oblique.woff2`（942KB，**未做任何改动**）
- 版本：内部 name 表为 `Smiley Sans Oblique` / `Version 1.0.0`，与官方发布一致（得意黑官方只发布斜体版，斜体是它的设计特征而非错误）
- 来源：npm 包 `font-smiley-sans@1.0.0`，tarball `https://registry.npmjs.org/font-smiley-sans/-/font-smiley-sans-1.0.0.tgz`

**授权状况需留意**：该 npm 包是第三方 CDN 转载，其 `package.json` 自填 `license: MIT`，
字体文件本身的 name 表里没有版权声明串。得意黑上游项目（Atelier Anchor）声明的是
SIL Open Font License 1.1 —— 转载方的 MIT 声明不能覆盖原作者授权。

本机无法连 GitHub（直连超时），未能取回上游 LICENSE 原文核对。**若要对外分发，请自行确认上游授权。**
本仓库把它当作 OFL 资源使用：文件未修改（未做子集化，因此不涉及 OFL 的保留字体名条款），
也未在任何产物中主张对其的所有权。

## 为什么没做子集化

得意黑在结果页用于 `${城市}旅行计划` 这类动态标题，城市名不可枚举；子集化会漏字，
且改动了原字体文件即触发 OFL 的保留字体名（Reserved Font Name）条款。942KB 在本地静态目录下无加载压力。

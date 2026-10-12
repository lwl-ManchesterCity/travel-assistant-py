# 景点外部资料库

`docs/ATTRACTION_KB_DESIGN.md` 的执行产物。**这是一次性离线作业的产出，不在运行时链路上。**

## 这张表解决什么

运行时只查表，不联网。补上高德确定拿不到的几格：门票、开放时间、闭馆日。

## 文件

| 文件 | 说明 |
| --- | --- |
| `attractions.json` | 产物。34 条杭州景点，通过 7 条校验 |
| `tags.json` | 受控词表。`tags` 字段只允许取这里的词 |
| `llm_supplement.json` | 第三层补充数据（票价/简介/场景标签/出处），按 `amap_poi_id` 索引 |

## 复跑

```bash
# 1. 抓骨架（只花一次高德配额，已抓过就别再跑）
python -m scripts.kb.fetch_amap_fixture                # 增量补齐缺失的关键词
python -m scripts.kb.fetch_amap_fixture --only 灵隐寺   # 只补一个

# 2. 合成 + 校验 + 写出
python -m scripts.kb.build_kb
python -m scripts.kb.build_kb --check-only   # 只校验现有文件
python -m scripts.kb.build_kb --strict       # 有告警就返回非零（CI 用）
```

`build_kb` 是幂等的：同一份 fixture 跑两次输出一致（判据第 6 条）。

## 加一座城市

**照 `scripts/kb/keywords.py` 的格式写规格表，不要盲取高德首条。**
实测首条经常不可靠：「楼观台」返回西安的起名馆、「龙井村」返回公交站、「河坊街」返回道路名。

每条规格三个字段：

```python
{"keyword": "岳王庙", "expect_name": "杭州西湖风景名胜区-岳王庙", "category": "人文历史"},
```

- `keyword` —— 打给高德 `/v3/place/text` 的检索词
- `expect_name` —— 期望命中的 POI 名。留 `None` 则退回打分，会挑错
- `category` —— 人工覆写。只在「按 type 推是错的」时才写（如灵隐寺 type 是「风景名胜」，
  按 type 推会得到「自然风景」，但它是寺）。留 `None` 表示沿用推导

## 两条不可违反的约束

1. **票价未知写 `null`，不许写 `0`。** `0` 表示「确定免费」。把未知写成 0 会让预算里
   门票那一桶静默少算，而且**不报错、不告警**。校验器第 2 条会拦。
2. **`tags` 只能取 `tags.json` 里的词**，包括「语义相近」的同义词——「下雨天」不是「雨天」。

## 自检

```bash
pytest tests/test_attraction_kb.py -q    # 93 条用例，覆盖 7 条校验规则的正反例
```

校验器会拦住的脏数据（都有对应用例）：`ticket_price: true`、`location` 经纬度写反或写成字符串、
`closed_days: ["星期一"]`、`description` 超 200 字、`source_url` 不成形、`amap_poi_id` 重复。

**唯一例外**：`tags` 里混入词表外的词**不丢弃整条**，只丢那个 tag 并告警——
这是设计文档第七节规则 1 的明确规定。

## 已知差异

本表把 图书馆/纪念馆/展览馆/艺术馆/科技馆 归为「人文历史」；
运行时 `travel_assistant/clients/amap_attraction.py` 的 `_map_type_to_category`
会把它们落到「城市漫游」。本单不改运行时代码，差异只存在于这张离线表。
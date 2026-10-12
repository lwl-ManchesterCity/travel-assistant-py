"""杭州核心景点规格表（第 1 步的抓取清单 + 第二层的人工策展覆写）。

挑选口径不是「top30 热门」，而是**覆盖追问里真会被问到的那几类**：
室内（雨天/亲子）、山（避暑）、湖与夜景（拍照/夜游）、免费街区（预算敏感）、
闭馆日明显的博物馆（周一闭馆）、古镇水乡。

每条规格声明三件事：
  keyword      拿去打高德 /v3/place/text 的检索词
  expect_name  期望命中的 POI 名。高德首条**不可靠**——实测「楼观台」返回西安的起名馆、
               「龙井村」返回公交站、「河坊街」返回道路名。首条只是候选，要按名字挑。
  category     第二层 category 的人工覆写。只在「按 type 推是错的」时才写，
               留None 表示沿用 normalize.map_type_to_category 的推导。
               为什么允许覆写：灵隐寺的 type 是「风景名胜」，按 type 推会得到「自然风景」，
               但它是寺；门票估算（60 vs 50）与planner 对人文历史的处理都跟着category 走。
               只覆写有明确依据的，不做全表手改。
"""

ATTRACTION_SPECS: tuple[dict[str, str | None], ...] = (
    # 人文历史：室内为主，验证「雨天/亲子」这条线
    {"keyword": "浙江省博物馆", "expect_name": "浙江省博物馆(孤山馆区)", "category": None},
    {"keyword": "中国丝绸博物馆", "expect_name": "中国丝绸博物馆", "category": None},
    {"keyword": "良渚博物院", "expect_name": "良渚博物院", "category": None},
    {"keyword": "灵隐寺", "expect_name": "灵隐寺", "category": "人文历史"},
    {"keyword": "岳王庙", "expect_name": "杭州西湖风景名胜区-岳王庙", "category": "人文历史"},
    {"keyword": "胡雪岩故居", "expect_name": "胡雪岩旧居", "category": "人文历史"},
    {"keyword": "六和塔", "expect_name": "六和塔", "category": "人文历史"},
    # 自然风景：山/ 湖 / 湿地 / 公园
    {"keyword": "西湖风景名胜区", "expect_name": "杭州西湖风景名胜区", "category": None},
    {"keyword": "西溪国家湿地公园", "expect_name": "西溪国家湿地公园", "category": None},
    {"keyword": "千岛湖风景区", "expect_name": "千岛湖风景区", "category": None},
    {"keyword": "杭州动物园", "expect_name": "杭州动物园", "category": None},
    {"keyword": "杭州植物园", "expect_name": "杭州植物园", "category": None},
    {"keyword": "灵山风景区", "expect_name": "杭州灵山景区", "category": None},
    {"keyword": "湘湖", "expect_name": "湘湖国家旅游度假区", "category": None},
    {"keyword": "九溪十八涧", "expect_name": "九溪十八涧", "category": None},
    {"keyword": "云栖竹径", "expect_name": "云栖竹径", "category": None},
    # 城市漫游：免费街区与城市地标，预算敏感
    {"keyword": "京杭大运河杭州段", "expect_name": "京杭大运河杭州景区", "category": None},
    {"keyword": "武林广场", "expect_name": "武林广场", "category": None},
    {"keyword": "钱江新城", "expect_name": "钱江新城灯光秀", "category": "城市漫游"},
    {"keyword": "龙井村", "expect_name": "龙井村牌坊", "category": "城市漫游"},
    {"keyword": "西溪洪园", "expect_name": "西溪国家湿地公园洪园", "category": None},
    {"keyword": "宋城", "expect_name": "杭州宋城", "category": None},
    {"keyword": "雷峰塔", "expect_name": "雷峰塔景区", "category": None},
    {"keyword": "白塔公园", "expect_name": "杭州西湖风景名胜区-杭州白塔公园", "category": None},
    # 美食购物：预算与夜游相关
    {"keyword": "河坊街", "expect_name": "河坊街景区", "category": "美食购物"},
    {"keyword": "南宋御街", "expect_name": "南宋御街中山路步行街", "category": "美食购物"},
    {"keyword": "小河直街", "expect_name": "小河历史文化街区", "category": "美食购物"},
    {"keyword": "桥西历史街区", "expect_name": "拱宸桥西历史文化街区", "category": "美食购物"},
    # 其他
    {"keyword": "八卦田", "expect_name": "八卦田遗址公园", "category": None},
    # 补足覆盖面：室内亲子（图书馆/美术馆）、避暑（山）、夜游（河运/喷泉）
    {"keyword": "浙江图书馆", "expect_name": None, "category": None},
    {"keyword": "浙江美术馆", "expect_name": None, "category": None},
    {"keyword": "黄龙洞", "expect_name": None, "category": None},
    {"keyword": "茅家埠", "expect_name": "杭州西湖风景名胜区-茅家埠", "category": None},
    {"keyword": "西湖音乐喷泉", "expect_name": None, "category": None},
    {"keyword": "钱江新城夜游", "expect_name": None, "category": "城市漫游"},
)

# 兼容旧调用方：只要关键词列表时用这个。
HANGZHOU_KEYWORDS: tuple[str, ...] = tuple(spec["keyword"] for spec in ATTRACTION_SPECS)  # type: ignore[misc]

SPECS_BY_KEYWORD: dict[str, dict[str, str | None]] = {spec["keyword"]: spec for spec in ATTRACTION_SPECS}  # type: ignore[misc]
"""各城核心景点规格表（第 1 步的抓取清单 + 第二层的人工策展覆写）。

挑选口径不是「top30 热门」，而是**覆盖追问里真会被问到的那几类**：
室内（雨天/亲子）、山（避暑）、湖与夜景（拍照/夜游）、免费街区（预算敏感）、
闭馆日明显的博物馆、古镇水乡。**按热度排序取前 30 是错的**——
那样会漏掉免费街区与闭馆日这类最常被追问的点。

每条规格声明三件事：
  keyword      拿去打高德 /v3/place/text 的检索词
  expect_name  期望命中的 POI 名。高德首条**不可靠**——实测「楼观台」返回西安的起名馆、
               「龙井村」返回公交站、「河坊街」返回道路名。首条只是候选，要按名字挑。
  category     第二层 category 的人工覆写。只在「按 type 推是错的」时才写，
               留 None 表示沿用 normalize.map_type_to_category 的推导。
               为什么允许覆写：灵隐寺的 type 是「风景名胜」，按 type 推会得到「自然风景」，
               但它是寺；门票估算（60 vs 50）与 planner 对人文历史的处理都跟着 category 走。
               只覆写有明确依据的，不做全表手改。

**这张表是唯一必须人工的一步**，其余各步都可脚本化。
"""

from __future__ import annotations

_HANGZHOU_SPECS: tuple[dict[str, str | None], ...] = (
    # 人文历史：室内为主，验证「雨天/亲子」这条线
    {"keyword": "浙江省博物馆", "expect_name": "浙江省博物馆(孤山馆区)", "category": None},
    {"keyword": "中国丝绸博物馆", "expect_name": "中国丝绸博物馆", "category": None},
    {"keyword": "良渚博物院", "expect_name": "良渚博物院", "category": None},
    {"keyword": "灵隐寺", "expect_name": "灵隐寺", "category": "人文历史"},
    {"keyword": "岳王庙", "expect_name": "杭州西湖风景名胜区-岳王庙", "category": "人文历史"},
    {"keyword": "胡雪岩故居", "expect_name": "胡雪岩旧居", "category": "人文历史"},
    {"keyword": "六和塔", "expect_name": "六和塔", "category": "人文历史"},
    # 自然风景：山 / 湖 / 湿地 / 公园
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

_BEIJING_SPECS: tuple[dict[str, str | None], ...] = (
    # 人文历史：故宫/国博/天坛是追问高频，且都有明确票价与闭馆日
    {"keyword": "故宫博物院", "expect_name": None, "category": "人文历史"},
    {"keyword": "中国国家博物馆", "expect_name": None, "category": "人文历史"},
    {"keyword": "天坛公园", "expect_name": None, "category": "人文历史"},
    {"keyword": "颐和园", "expect_name": None, "category": "人文历史"},
    {"keyword": "八达岭长城", "expect_name": None, "category": "人文历史"},
    {"keyword": "首都博物馆", "expect_name": None, "category": None},
    {"keyword": "中国美术馆", "expect_name": None, "category": None},
    {"keyword": "北海公园", "expect_name": None, "category": None},
    {"keyword": "景山公园", "expect_name": None, "category": "城市漫游"},
    {"keyword": "什刹海", "expect_name": None, "category": None},
    # 自然风景：香山/颐和湖/圆明园
    {"keyword": "香山公园", "expect_name": None, "category": None},
    {"keyword": "圆明园", "expect_name": "圆明园遗址公园", "category": None},
    {"keyword": "北京动物园", "expect_name": None, "category": None},
    # 实测：北京没有独立的「北京植物园」POI，它已并入国家植物园（北园/南园）。
    # 照抄关键词会挑到园里的子园区（北京植物园梅园/集秀园），那不是景点本体。
    # 高德把北园那条直接叫「国家植物园」，没有「(北园)」后缀——expect_name 要按实际返回写。
    {"keyword": "国家植物园", "expect_name": "国家植物园", "category": None},
    {"keyword": "陶然亭公园", "expect_name": None, "category": None},
    {"keyword": "玉渊潭公园", "expect_name": None, "category": None},
    {"keyword": "青龙湖公园", "expect_name": None, "category": None},
    # 城市漫游：免费街区与胡同
    {"keyword": "南锣鼓巷", "expect_name": None, "category": "城市漫游"},
    {"keyword": "什刹海胡同", "expect_name": None, "category": "城市漫游"},
    {"keyword": "前门大街", "expect_name": None, "category": "美食购物"},
    {"keyword": "王府井步行街", "expect_name": None, "category": "美食购物"},
    # 实测：高德把「三里屯」本体标成「地名地址信息;热点地名」，被is_pickable_poi 过滤掉，
    # 剩下的全是「三里屯太古里」这类商圈。换用太古里这个实体名，别用地名条目。
    {"keyword": "三里屯", "expect_name": "三里屯太古里", "category": "美食购物"},
    {"keyword": "颐和园码头", "expect_name": None, "category": None},
    # 古镇水乡与专题
    {"keyword": "古北水镇", "expect_name": None, "category": None},
    {"keyword": "奥林匹克公园", "expect_name": None, "category": "城市漫游"},
    {"keyword": "中国园林博物馆", "expect_name": None, "category": None},
    {"keyword": "首都图书馆", "expect_name": None, "category": None},
    {"keyword": "恭王府", "expect_name": None, "category": "人文历史"},
    {"keyword": "明十三陵", "expect_name": None, "category": None},
    {"keyword": "慕田峪长城", "expect_name": None, "category": "人文历史"},
    {"keyword": "正阳门", "expect_name": None, "category": "人文历史"},
    # 补足覆盖面：免费街区（预算敏感）、室内亲子、夜游
    {"keyword": "北京欢乐谷", "expect_name": None, "category": None},
    {"keyword": "798艺术区", "expect_name": None, "category": "城市漫游"},
    {"keyword": "国家自然博物馆", "expect_name": None, "category": None},
    {"keyword": "北京海洋馆", "expect_name": None, "category": None},
    {"keyword": "紫竹院公园", "expect_name": None, "category": None},
    {"keyword": "北京石刻艺术博物馆", "expect_name": None, "category": "人文历史"},
    {"keyword": "首都剧场", "expect_name": None, "category": "城市漫游"},
    {"keyword": "北京野生动物园", "expect_name": None, "category": None},
    {"keyword": "什刹海西海", "expect_name": None, "category": "城市漫游"},
)

_SHANGHAI_SPECS: tuple[dict[str, str | None], ...] = (
    # 人文历史：博物馆密集，闭馆日各异，是闭馆日这条线的富矿
    {"keyword": "上海博物馆", "expect_name": "上海博物馆", "category": None},
    {"keyword": "上海科技馆", "expect_name": None, "category": None},
    {"keyword": "上海自然博物馆", "expect_name": None, "category": None},
    {"keyword": "中华艺术宫", "expect_name": None, "category": "人文历史"},
    {"keyword": "上海城市规划展示馆", "expect_name": None, "category": None},
    {"keyword": "中共一大纪念馆", "expect_name": "中共一大会址纪念馆保管部", "category": "人文历史"},
    {"keyword": "鲁迅纪念馆", "expect_name": "上海鲁迅纪念馆(暂停开放)", "category": "人文历史"},
    {"keyword": "龙华烈士陵园", "expect_name": "上海市龙华烈士陵园-长明火", "category": "人文历史"},
    {"keyword": "嘉定孔庙", "expect_name": None, "category": "人文历史"},
    {"keyword": "上海美术馆", "expect_name": "上海美术馆(中华艺术宫)", "category": None},
    # 自然风景：滨江/公园/湿地
    {"keyword": "外滩", "expect_name": None, "category": "城市漫游"},
    {"keyword": "东方明珠", "expect_name": "东方明珠广播电视塔", "category": "城市漫游"},
    {"keyword": "上海野生动物园", "expect_name": None, "category": None},
    {"keyword": "上海植物园", "expect_name": None, "category": None},
    {"keyword": "共青森林公园", "expect_name": "上海共青森林公园", "category": None},
    {"keyword": "顾村公园", "expect_name": None, "category": None},
    {"keyword": "滴水湖", "expect_name": None, "category": None},
    {"keyword": "崇明东平国家森林公园", "expect_name": "东平国家森林公园", "category": None},
    {"keyword": "佘山国家森林公园", "expect_name": "上海佘山国家森林公园", "category": None},
    # 城市漫游：免费街区与古镇水乡
    {"keyword": "南京路步行街", "expect_name": None, "category": "美食购物"},
    {"keyword": "田子坊", "expect_name": "上海田子坊", "category": "城市漫游"},
    {"keyword": "新天地", "expect_name": "上海新天地", "category": "美食购物"},
    {"keyword": "豫园", "expect_name": "上海豫园", "category": "人文历史"},
    {"keyword": "武康路", "expect_name": "武康路历史文化名街", "category": "城市漫游"},
    {"keyword": "朱家角古镇", "expect_name": "上海朱家角古镇旅游区", "category": None},
    {"keyword": "七宝古镇", "expect_name": None, "category": None},
    {"keyword": "陆家嘴", "expect_name": "陆家嘴中心", "category": "城市漫游"},
    {"keyword": "上海迪士尼度假区", "expect_name": None, "category": None},
    {"keyword": "上海海洋水族馆", "expect_name": None, "category": None},
    {"keyword": "上海塔", "expect_name": "上海长滩观光塔", "category": "城市漫游"},
    {"keyword": "1933老场坊", "expect_name": None, "category": "城市漫游"},
    {"keyword": "静安寺", "expect_name": None, "category": "人文历史"},
    {"keyword": "上海古城公园", "expect_name": "古城公园", "category": "城市漫游"},
)

_GUANGZHOU_SPECS: tuple[dict[str, str | None], ...] = (
    # 人文历史：陈家祠/广东省博物馆是室内雨天线的核心
    {"keyword": "陈家祠", "expect_name": "陈家祠堂", "category": "人文历史"},
    {"keyword": "广东省博物馆", "expect_name": None, "category": None},
    {"keyword": "广州博物馆", "expect_name": "广州博物馆(仲元楼展区)", "category": None},
    {"keyword": "广东革命历史博物馆", "expect_name": None, "category": "人文历史"},
    {"keyword": "广州十三行博物馆", "expect_name": None, "category": "人文历史"},
    {"keyword": "三元里人民抗英斗争纪念馆", "expect_name": None, "category": "人文历史"},
    {"keyword": "黄花岗七十二烈士陵园", "expect_name": "黄花岗公园", "category": "人文历史"},
    {"keyword": "南越王博物院", "expect_name": "南越王博物院(王宫展区)", "category": "人文历史"},
    {"keyword": "镇海楼", "expect_name": "广州博物馆(镇海楼展区)", "category": "人文历史"},
    # 自然风景：白云山/珠江/湿地
    {"keyword": "白云山", "expect_name": "广州市白云山风景名胜区山顶公园", "category": None},
    {"keyword": "越秀公园", "expect_name": None, "category": None},
    {"keyword": "华南植物园", "expect_name": "华南国家植物园", "category": None},
    {"keyword": "广州动物园", "expect_name": None, "category": None},
    {"keyword": "流花湖公园", "expect_name": None, "category": None},
    {"keyword": "海珠湿地公园", "expect_name": None, "category": None},
    {"keyword": "大夫山森林公园", "expect_name": None, "category": None},
    {"keyword": "帽峰山森林公园", "expect_name": None, "category": None},
    {"keyword": "广州湿地公园", "expect_name": "天河湿地公园", "category": None},
    # 城市漫游：小蛮腰/珠江夜游/老城西关
    {"keyword": "广州塔", "expect_name": None, "category": "城市漫游"},
    {"keyword": "珠江夜游", "expect_name": None, "category": "城市漫游"},
    {"keyword": "沙面岛", "expect_name": None, "category": "城市漫游"},
    {"keyword": "永庆坊", "expect_name": None, "category": "美食购物"},
    {"keyword": "上下九步行街", "expect_name": "上下九商业步行街", "category": "美食购物"},
    {"keyword": "北京路步行街", "expect_name": None, "category": "美食购物"},
    {"keyword": "石牌坊", "expect_name": "东石牌坊", "category": "人文历史"},
    {"keyword": "白云山风景名胜区", "expect_name": "广州市白云山风景名胜区山顶公园", "category": None},
    {"keyword": "增城白水寨", "expect_name": "白水寨风景名胜区", "category": None},
    {"keyword": "余荫山房", "expect_name": None, "category": "人文历史"},
    {"keyword": "广州海洋馆", "expect_name": None, "category": None},
    {"keyword": "永庆坊二期", "expect_name": None, "category": "美食购物"},
    # 补足覆盖面：免费街区（预算敏感）、室内亲子、避暑、免费地标
    {"keyword": "珠江新城花城广场", "expect_name": "花城广场音乐喷泉", "category": "城市漫游"},
    {"keyword": "广东省立中山图书馆", "expect_name": "广东省立中山图书馆中文图书馆", "category": None},
    {"keyword": "广州艺术博物院", "expect_name": "广州艺术博物院(广州美术馆)", "category": "人文历史"},
    {"keyword": "越秀公园五羊仙庭", "expect_name": "越秀公园-五羊仙庭", "category": None},
    {"keyword": "莲花山公园", "expect_name": "莲花山旅游区", "category": None},
)

# 按城市分组。键是**不带「市/省」后缀**的城市名——它必须与检索侧请求里的 city 逐字相同
# （retrieve._where 用的是精确匹配）。
SPECS_BY_CITY: dict[str, tuple[dict[str, str | None], ...]] = {
    "杭州": _HANGZHOU_SPECS,
    "北京": _BEIJING_SPECS,
    "上海": _SHANGHAI_SPECS,
    "广州": _GUANGZHOU_SPECS,
}

# 城市名 → fixture 文件名用的 slug（长）与主键前缀（短）**是两个东西**。
#
# 曾经合成一个 CITY_SLUGS 用两处，结果把杭州的前缀从 "hz" 变成 "hangzhou"，
# 34 条 id 全变——回归测试逐字节对比时才发现。
#   文件名要人认得出（hangzhou）→ CITY_SLUGS
#   主键要短（hz）→ CITY_ID_PREFIXES
CITY_SLUGS: dict[str, str] = {
    "杭州": "hangzhou",
    "北京": "beijing",
    "上海": "shanghai",
    "广州": "guangzhou",
}

CITY_ID_PREFIXES: dict[str, str] = {
    "杭州": "hz",
    "北京": "bj",
    "上海": "sh",
    "广州": "gz",
}


def city_slug(city: str) -> str:
    """城市名 → fixture 文件名用的 slug。取不到就用 sha1 前 8 位兜底。"""
    if city in CITY_SLUGS:
        return CITY_SLUGS[city]
    import hashlib

    digest = hashlib.sha1(city.encode("utf-8")).hexdigest()[:8]
    return f"c{digest}"


def city_id_prefix(city: str) -> str:
    """城市名 → 主键前缀。**不能**退化成字面量 "city"：
    那样多座城会共用一个前缀，主键就失去了区分城市的能力。"""
    if city in CITY_ID_PREFIXES:
        return CITY_ID_PREFIXES[city]
    return city_slug(city)[:2]


def specs_for(city: str) -> tuple[dict[str, str | None], ...]:
    return SPECS_BY_CITY.get(city, ())


def keywords_for(city: str) -> tuple[str, ...]:
    return tuple(spec["keyword"] for spec in specs_for(city))  # type: ignore[misc]


def known_cities() -> tuple[str, ...]:
    return tuple(SPECS_BY_CITY)


# ---- 旧调用方的兼容别名（杭州是第一座城，有测试与脚本引用这两个名字）----

ATTRACTION_SPECS = _HANGZHOU_SPECS
HANGZHOU_KEYWORDS: tuple[str, ...] = keywords_for("杭州")
SPECS_BY_KEYWORD: dict[str, dict[str, str | None]] = {
    spec["keyword"]: spec for spec in _HANGZHOU_SPECS  # type: ignore[misc]
}
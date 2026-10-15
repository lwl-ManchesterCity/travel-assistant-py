"""本地知识库景点源：杭州/北京/成都各有一组写死的景点，其余城市给通用占位。"""

from ..models.attraction import Attraction
from ..models.location import Location


class TravelKnowledgeService:
    def find_attractions(self, city_name: str | None) -> list[Attraction]:
        city = self._normalize(city_name)
        attractions: list[Attraction] = []

        if "杭州" in city:
            attractions.append(self._build("西湖", "杭州西湖景区", 120.1551, 30.2741, 180, "经典必去，适合慢节奏步行和拍照", "自然风景", 4.8, 60))
            attractions.append(self._build("灵隐寺", "杭州西湖区灵隐路", 120.1045, 30.2417, 120, "适合安静体验和人文游览", "人文历史", 4.7, 45))
            attractions.append(self._build("河坊街", "杭州上城区河坊街", 120.1712, 30.2429, 90, "适合夜游和本地小吃体验", "美食购物", 4.5, 0))
            attractions.append(self._build("西溪湿地", "杭州余杭区天目山路", 120.0635, 30.2666, 180, "适合轻松散步和避开高密度人流", "自然风景", 4.6, 80))
            return attractions

        if "北京" in city:
            attractions.append(self._build("故宫", "北京东城区景山前街4号", 116.3970, 39.9180, 180, "适合第一次到访，历史内容非常集中", "人文历史", 4.9, 60))
            attractions.append(self._build("什刹海", "北京西城区什刹海景区", 116.3852, 39.9430, 120, "适合傍晚散步和体验老北京氛围", "城市漫游", 4.6, 0))
            attractions.append(self._build("簋街", "北京东城区东直门内大街", 116.4342, 39.9418, 90, "适合晚上安排美食体验", "美食购物", 4.4, 0))
            attractions.append(self._build("颐和园", "北京海淀区新建宫门路19号", 116.2730, 39.9997, 180, "景观舒展，适合半天慢游", "自然风景", 4.8, 30))
            return attractions

        if "成都" in city:
            attractions.append(self._build("宽窄巷子", "成都青羊区宽巷子", 104.0496, 30.6662, 120, "适合第一次到访和城市气氛体验", "城市漫游", 4.6, 0))
            attractions.append(self._build("武侯祠", "成都武侯区武侯祠大街231号", 104.0436, 30.6419, 120, "适合历史文化偏好的用户", "人文历史", 4.7, 50))
            attractions.append(self._build("锦里", "成都武侯区锦里中路", 104.0461, 30.6404, 90, "适合安排夜间小吃和休闲购物", "美食购物", 4.5, 0))
            attractions.append(self._build("都江堰", "成都都江堰市公园路", 103.6487, 31.0028, 180, "适合做一日延伸路线", "自然风景", 4.8, 80))
            return attractions

        attractions.append(self._build("城市地标", f"{city_name}核心景点", 120.0000, 30.0000, 120, "适合第一次到访，快速建立城市印象", "城市漫游", 4.4, 30))
        attractions.append(self._build("本地美食街区", f"{city_name}热门商圈", 120.0100, 30.0050, 90, "适合晚上安排，补充城市烟火气", "美食购物", 4.3, 0))
        attractions.append(self._build("休闲公园", f"{city_name}城市公园", 120.0200, 30.0100, 120, "适合慢节奏行程和放松", "自然风景", 4.5, 20))
        attractions.append(self._build("文化展馆", f"{city_name}文化区域", 120.0300, 30.0150, 90, "适合补充本地历史与城市背景", "人文历史", 4.2, 40))
        return attractions

    def _build(
        self,
        name: str,
        address: str,
        longitude: float,
        latitude: float,
        visit_duration: int,
        description: str,
        category: str,
        rating: float,
        ticket_price: int,
    ) -> Attraction:
        return Attraction(
            name=name,
            address=address,
            location=Location(longitude=longitude, latitude=latitude),
            visit_duration=visit_duration,
            description=description,
            category=category,
            rating=rating,
            image_url=None,
            ticket_price=ticket_price,
        )

    def _normalize(self, value: str | None) -> str:
        return "" if value is None else value.lower().strip()

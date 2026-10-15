"""对应 Java service/RequirementAnalysisService.java：模型只做需求理解，失败即回到规则关键词。"""

import json

from ..clients.llm import LlmClient
from ..models.planning_constraints import PlanningConstraints
from ..models.trip_plan_request import TripPlanRequest
from ..utils import java_list_to_string
from .preference_service import build_injection_block
from .request_optimization_strategy_service import strip_system_requirements

_SYSTEM_PROMPT = """你是旅行需求理解助手。你的任务是把用户的旅行请求解析成结构化约束 JSON。
你必须只返回 JSON，不要返回解释、Markdown 或代码块。
JSON 字段固定为：
{
  "relaxedPace": boolean,
  "avoidSeafood": boolean,
  "wantsLandmarkCeremony": boolean,
  "prefersIndoorBackup": boolean,
  "extractedTags": string[],
  "extractedPreferences": [{"subject": string, "content": string}],
  "extractedEpisodes": [{"city": string, "content": string}]
}
字段含义：
relaxedPace：用户希望慢节奏、轻松、不要太赶。
avoidSeafood：用户不吃海鲜、海鲜过敏、避开海鲜。
wantsLandmarkCeremony：用户提到升旗、仪式感、地标打卡等。
prefersIndoorBackup：用户担心下雨、高温、天气不稳定，或希望室内备选。
extractedTags 用简短中文标签总结命中的约束。
extractedPreferences 与 extractedEpisodes 是**两个互斥的桶**：同一条信息只进一个，
**不许两边都写**。先看它是不是"上一次真实发生过的事"——是就进 episodes，
不是再看它是不是"关于用户本人的"——是就进 preferences，都不是就都不进。

extractedPreferences 从「额外要求」里挑出**长期偏好**（用户本人的，或他同行的人的），
会存下来、影响他以后每一趟。判断标准是「这个人的」还是「这一趟的」：
  要挑（这个人的）：「对海鲜过敏」「不吃辣」「不吃香菜」「预算敏感」「恐高」
    「带小孩出行」「腿脚不便」「喜欢安静」。
  不挑（这一趟的）：「想去看升旗」「这次轻松点」「多留一天」「想去西湖」。
  **拿不准就按「这一趟的」不挑**——挑错的那条会跟着他每一趟，而他不知道它在那儿。
  每条写成 {"subject":"本人","content":"不吃辣"}：`subject` 是**这条偏好属于谁**。
    **用户本人写「本人」**；**同行的人也要挑，写他在备注里说的那个称谓**——
    「我老婆不能吃辣」→ {"subject":"老婆","content":"不能吃辣"}，
    「我朋友对海鲜过敏」→ {"subject":"朋友","content":"对海鲜过敏"}，
    「我妈腿脚不好」→ {"subject":"我妈","content":"腿脚不好"}。
    称谓**照他写的原样**，不要归一化（「老婆」「妻子」就当两个词写着，
    是不是同一个人你判不准，判错了两个人会并成一条）。
    **content 里不要重复那个人**（写「不能吃辣」，不写「老婆不能吃辣」）。
  分界看是**谁**：同行的人**是谁**、有没有这个人，是他自己的情况
    （「带小孩出行」subject 写「本人」）；同行的人**自己**的口味和状况，subject 写那个人。
  每条只写偏好本身，短，不超过十几个字，不要把整句话抄进来。
  「偏好」那一栏里勾选的类型词（如「人文历史」「自然风景」）**不要挑**，
  那是他这一趟选的，不是他这个人的。
  没有就交空数组，不要为了填而填。

extractedEpisodes 从「额外要求」里挑出**上一次在这个城市发生过的事**（已经发生的事实），
  一条写成 {"city":"杭州","content":"上次那家酒店隔音差"}。
  要挑：「上次杭州那家酒店隔音差」「上回去成都第二天排太满」「上次在北京那家面馆排队太久」。
  都不挑的：这一趟的想法（还没发生，「想去看升旗」「这次轻松点」「想去看西湖」）；
    没地点的事（「上次那家酒店隔音差」——不知道是哪个城市，以后永远捞不出来，**整条丢**）。
  **city 只许来自「额外要求」那一格的原文**，不许拿上面的「目的地」补：
    用户写在备注里的"上次那家"，未必就是这一趟要去的城市——猜错了，
    这条就**永远挂在错误的城市**下。**读不出城市就整条丢**（宁可少记）。
  **content 里不要重复城市**，城市有它自己那一格。
  没有就交空数组，不要为了填而填。
"""



class RequirementAnalysisService:

    def __init__(self, llm_client: LlmClient):
        self._llm_client = llm_client

    def analyze(self, request: TripPlanRequest,
                long_term_preferences: list[str] | None = None,
                episodes: list[str] | None = None,
                companions: list[str] | None = None) -> PlanningConstraints:
        """解析需求。

        `long_term_preferences` / `episodes` / `companions` 是这个人的长期记忆**原文**，
        由调用方（PlannerInputBuilder）取好传进来。**这里不自己取**：留痕要写
        "注入了 N 条"，而只有拿到同一个列表，那条留痕才真的证明了注入发生过
        （§2.2.9 判据 7）。

        `companions` 是**同行的人的**偏好，每条带着是谁（「老婆不能吃辣」，§2.5.4）。
        与本人的分开传，注入时分两段写——混在一起模型就分不出哪条是谁的。

        `episodes` 由调用方**按城市筛好**再传（§2.4.3）——这里不认识城市，
        也不该认识：它只管理解需求。
        """
        if self._llm_client.is_available():
            try:
                return self._analyze_by_llm(request, long_term_preferences, episodes, companions)
            except Exception:  # noqa: BLE001 - 与 Java 一致：任何解析失败都退回规则结果
                fallback = self._analyze_by_rules(request)
                fallback.analysis_source = "rule-fallback"
                return fallback
        return self._analyze_by_rules(request)

    def _analyze_by_llm(self, request: TripPlanRequest,
                        long_term_preferences: list[str] | None = None,
                        episodes: list[str] | None = None,
                        companions: list[str] | None = None) -> PlanningConstraints:
        content = self._llm_client.chat(
            _SYSTEM_PROMPT,
            self._build_user_prompt(request, long_term_preferences, episodes, companions))
        result = json.loads(content)
        extracted_episodes = self._read_episodes(result.get("extractedEpisodes"))
        constraints = PlanningConstraints(
            relaxed_pace=self._as_bool(result.get("relaxedPace"), False),
            avoid_seafood=self._as_bool(result.get("avoidSeafood"), False),
            wants_landmark_ceremony=self._as_bool(result.get("wantsLandmarkCeremony"), False),
            prefers_indoor_backup=self._as_bool(result.get("prefersIndoorBackup"), False),
            extracted_tags=self._read_string_list(result.get("extractedTags")),
            # 偏好那一格要**先知道场景抽到了什么**：同一句两边都收时从偏好里剔掉（见 _read_preferences）。
            extracted_preferences=self._read_preferences(
                result.get("extractedPreferences"), request, extracted_episodes),
            extracted_episodes=extracted_episodes,
        )
        constraints.analysis_source = "llm"
        if not constraints.extracted_tags:
            constraints.extracted_tags = self._build_tags(constraints)
        return constraints

    def _build_user_prompt(self, request: TripPlanRequest,
                           long_term_preferences: list[str] | None = None,
                           episodes: list[str] | None = None,
                           companions: list[str] | None = None) -> str:
        # **喂给模型的额外要求要先洗过**：里面混着策略服务自己追加的句子
        # （「整体控制预算，优先保留高价值景点」等）。它们是"这一次"的调节，
        # 存下来会变成"每一趟都强制压预算"（§2.2.4）。用户原话才进这一格。
        user_requirements = strip_system_requirements(request.extra_requirements)
        lines = [
            f"目的地：{self._safe(request.city)}",
            f"日期：{self._safe(request.start_date)} 至 {self._safe(request.end_date)}",
            f"预算：{request.budget}",
            f"偏好：{java_list_to_string(request.preferences)}",
            f"交通：{self._safe(request.transportation)}",
            f"住宿：{self._safe(request.accommodation)}",
            f"额外要求：{self._safe(user_requirements)}",
        ]
        # 长期记忆**原文**进的是这一格，不是 _SYSTEM_PROMPT：那是模块级常量，
        # 而记忆是每人每趟不同的。规则（判断标准）在系统提示词里，事实（具体哪几条）
        # 在用户提示词里——同一次调用，位置只影响模型怎么读。
        # 这一段里还带着「表单优先」那条纪律（§2.2.6）。
        # 场景那一段的城市标签用 request.city：**调用方已经按它筛过了**（§2.4.3）。
        block = build_injection_block(
            long_term_preferences or [], companions or [], episodes or [], request.city)
        if block:
            lines.append(block)
        return "\n".join(lines)

    def _analyze_by_rules(self, request: TripPlanRequest) -> PlanningConstraints:
        content = (request.extra_requirements or "").lower()
        preferences = request.preferences or []

        relaxed_pace = "轻松" in content or "慢" in content or "轻松节奏" in preferences
        avoid_seafood = "海鲜过敏" in content or "不吃海鲜" in content
        wants_landmark_ceremony = "升旗" in content or "仪式" in content
        prefers_indoor_backup = "下雨" in content or "室内" in content

        constraints = PlanningConstraints(
            relaxed_pace=relaxed_pace,
            avoid_seafood=avoid_seafood,
            wants_landmark_ceremony=wants_landmark_ceremony,
            prefers_indoor_backup=prefers_indoor_backup,
            extracted_tags=self._build_tags_from_booleans(
                relaxed_pace, avoid_seafood, wants_landmark_ceremony, prefers_indoor_backup
            ),
        )
        constraints.analysis_source = "rule"
        return constraints

    def _build_tags(self, constraints: PlanningConstraints) -> list[str]:
        return self._build_tags_from_booleans(
            constraints.relaxed_pace,
            constraints.avoid_seafood,
            constraints.wants_landmark_ceremony,
            constraints.prefers_indoor_backup,
        )

    def _build_tags_from_booleans(self,
                                  relaxed_pace: bool,
                                  avoid_seafood: bool,
                                  wants_landmark_ceremony: bool,
                                  prefers_indoor_backup: bool) -> list[str]:
        extracted_tags: list[str] = []
        if relaxed_pace:
            extracted_tags.append("轻松节奏")
        if avoid_seafood:
            extracted_tags.append("餐饮避开海鲜")
        if wants_landmark_ceremony:
            extracted_tags.append("优先考虑仪式感景点")
        if prefers_indoor_backup:
            extracted_tags.append("预留室内备选方案")
        return extracted_tags

    def _read_preferences(self, node: object, request: TripPlanRequest,
                          episodes: list[dict] | None = None) -> list[dict]:
        """读 extractedPreferences（`[{"subject": …, "content": …}]`），并挡掉两种回声。

        一、**表单勾选框的回声**。用户提示词里那行「偏好：人文历史、自然风景」是这一次
        选的类型，模型有可能顺手把它们当成"关于这个人的偏好"吐回来。那不是长期偏好
        ——用户自己纠正过这条（见 docs/ASK_LONGTERM_MEMORY.md §1.1：写入点只有备注和
        助手输入两处）。精确相等就够：回声必然与勾选框一字不差。

        二、**同一句被两个桶都收下**（§2.4.4 要求互斥）。提示词里写死了互斥，但模型
        仍可能两边都填——规则拦不干净，而这一条漏了的表现是**同一句话被注入两遍**，
        且它是"上次发生的事"却挂在了"他这个人"名下、跟着他每一趟。
        两处都是**精确相等**判据：同一句原文才会撞上。

        **`subject` 缺了就不填这一格**，由 PreferenceService 兜成「本人」——兜底只许
        有一处，而它是两条链共用的那一处（§2.5.5）。
        """
        chosen = {item.strip() for item in (request.preferences or []) if item and item.strip()}
        already_episodes = {item["content"].strip() for item in (episodes or [])}
        values: list[dict] = []
        for item in self._read_preference_items(node):
            content = item["content"]
            if content in chosen or content in already_episodes:
                continue
            if item not in values:
                values.append(item)
        return values

    def _read_preference_items(self, node: object) -> list[dict]:
        """把那一格收成 `[{"subject": …, "content": …}]`。

        收 dict（约定的）与**裸字符串**（老模型只吐一串文本）；裸字符串只填 content，
        subject 留给 PreferenceService 兜。**只保证形状**——长度闸门与 5 条上限同理。
        """
        if not isinstance(node, list):
            return []
        values: list[dict] = []
        for item in node:
            if isinstance(item, str):
                pair: dict = {"content": item.strip()}
            elif isinstance(item, dict):
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                pair = {"content": content.strip()}
                subject = item.get("subject")
                if isinstance(subject, str) and subject.strip():
                    pair["subject"] = subject.strip()
            else:
                continue
            if pair["content"]:
                values.append(pair)
        return values

    def _read_episodes(self, node: object) -> list[dict]:
        """读 extractedEpisodes：只收 `{city, content}` 都是非空字符串的那种条目。

        **city 缺了就整条丢**（§2.4.4）：按城市取，没城市就永远捞不出来，
        存了等于没存。长度闸门与 5 条上限归 EpisodeService——与偏好同一条分工理由。
        """
        if not isinstance(node, list):
            return []
        values: list[dict] = []
        for item in node:
            if not isinstance(item, dict):
                continue
            city, content = item.get("city"), item.get("content")
            if not isinstance(city, str) or not isinstance(content, str):
                continue
            pair = {"city": city.strip(), "content": content.strip()}
            if pair["city"] and pair["content"] and pair not in values:
                values.append(pair)
        return values

    def _read_string_list(self, node: object) -> list[str]:
        values: list[str] = []
        if not isinstance(node, list):
            return values
        for item in node:
            value = item if isinstance(item, str) else ("" if isinstance(item, (dict, list)) else str(item))
            if value.strip():
                values.append(value)
        return values

    def _as_bool(self, node: object, default: bool) -> bool:
        if node is None:
            return default
        if isinstance(node, bool):
            return node
        if isinstance(node, (int, float)):
            return node != 0
        if isinstance(node, str):
            return node.strip().lower() in ("true", "t", "1")
        return default

    def _safe(self, value: str | None) -> str:
        return "" if value is None else value

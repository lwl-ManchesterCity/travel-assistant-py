"""判分引擎 —— 评测集本身可不可信，全看这一个文件。

`evals/run.py` 跑一次要花真模型的钱和好几分钟，所以**判分逻辑不能靠跑它来验**：
跑出来 38/41，你分不清是模型答错了三道，还是判分写错了三道。
`judge` 是个纯函数，这里拿合成响应把它钉死——不联网、不花模型钱。

顺带把题库也体检一遍：题写错了（工具名拼错、期望写空）在这里当场红，
而不是等跑完 41 道题、看着一个虚高的分数猜哪里不对。
"""

from evals.judge import TOOL_NAMES, judge, load_questions


def _body(answer: str = "", intent: str = "answer", tools: tuple = (),
          recorded: tuple = (), anchor: str | None = None) -> dict:
    """造一个响应。**那两条非工具留痕是默认带的**——真实响应里它们每次都有，
    漏掉会让「不该调任何工具」那类判据在测试里假过。"""
    traces = [{"agentName": "AskAgent"}, {"agentName": "MemoryService"}]
    traces += [{"agentName": name} for name in tools]
    return {
        "answer": answer,
        "intent": intent,
        "traces": traces,
        "recordedPreferences": list(recorded),
        "anchorHint": anchor,
    }


# ---------- 意图 ----------

def test_a_wrong_intent_is_reported_with_both_sides():
    """失败原因要写清「要什么、实际什么」——只报一句「意图不对」等于没报。"""
    reasons = judge({"意图": "need_change"}, _body(intent="answer"))
    assert len(reasons) == 1
    assert "need_change" in reasons[0] and "answer" in reasons[0]


def test_the_expected_intent_passes():
    assert judge({"意图": "need_change"}, _body(intent="need_change")) == []


# ---------- 工具轨迹 ----------

def test_the_tool_that_should_have_been_called_but_was_not():
    reasons = judge({"该调其一": ["search_attractions_by_tag"]}, _body(tools=("get_day_plan",)))
    assert len(reasons) == 1
    assert "search_attractions_by_tag" in reasons[0]


def test_any_one_of_the_listed_tools_is_enough():
    """「该调其一」是**至少一个**：模型先总览再细看，不该因为多调了就判失败。"""
    case = {"该调其一": ["get_plan_overview", "get_day_plan"]}
    assert judge(case, _body(tools=("get_day_plan",))) == []


def test_a_forbidden_tool_that_was_called_anyway():
    reasons = judge({"不该调": ["search_attractions"]},
                    _body(tools=("search_attractions_by_tag", "search_attractions")))
    assert len(reasons) == 1
    assert "search_attractions" in reasons[0]


def test_the_non_tool_traces_do_not_count_as_calling_a_tool():
    """`AskAgent` / `MemoryService` 每次都会留痕，**但它们是判定意图和注入记忆，
    不是调工具**。把它们算成「调了工具」的话，「不该调任何工具」这条永远判否——
    而它正是「你好」那种闲聊题唯一能挂的判据。
    """
    assert judge({"不该调任何工具": True}, _body()) == []


def test_calling_something_during_small_talk_is_caught():
    reasons = judge({"不该调任何工具": True}, _body(tools=("query_weather",)))
    assert len(reasons) == 1
    assert "query_weather" in reasons[0]


# ---------- 长期记忆 ----------

def test_expected_to_remember_but_nothing_was_recorded():
    assert len(judge({"记了": True}, _body(recorded=()))) == 1


def test_expected_not_to_remember_but_a_preference_got_recorded():
    reasons = judge({"记了": False}, _body(recorded=["不吃辣"]))
    assert len(reasons) == 1
    assert "不吃辣" in reasons[0]


def test_remembering_what_was_expected_passes():
    assert judge({"记了": True}, _body(recorded=["不吃辣"])) == []


# ---------- 锚点 ----------

def test_a_change_that_needed_an_anchor_but_got_none():
    reasons = judge({"有锚点": True}, _body(anchor=None))
    assert len(reasons) == 1
    assert judge({"有锚点": True}, _body(anchor="西湖")) == []


# ---------- 答案（不得已才读） ----------

def test_a_required_word_that_is_missing():
    assert len(judge({"答里要有": ["灵隐寺"]}, _body(answer="它在第二天。"))) == 1
    assert judge({"答里要有": ["灵隐寺"]}, _body(answer="灵隐寺在第二天。")) == []


def test_a_negation_passes_however_the_model_words_it():
    """这条是**这个文件存在的原因**。

    `scripts/opening_hours_probe.py` 判据 5 连踩三回：一开始写死「查不到」，
    模型说「都没给到」→ 红；改成列举，它说「未查询到」→ 又红；说「都没有查到」→ 再红。
    **描述性措辞是开放词表，永远追不上模型换说法。**

    所以判据换成封闭集合：只要有否定语素就算说了「没有」。下面三种说法都必须过，
    而一个**硬答了**的答案（拿广州塔的时段顶替）必须不过。
    """
    for wording in ("没查到", "未查询到", "都没有查到", "暂不提供", "无法确认"):
        assert judge({"答里有否定": True}, _body(answer=wording)) == [], wording

    hard_answer = _body(answer="广州塔旋转餐厅的营业时间是 10:00-22:00。")
    assert len(judge({"答里有否定": True}, hard_answer)) == 1


def test_a_forbidden_pattern_catches_a_number_that_should_not_be_there():
    """否命题好判：「不得出现 HH:MM」比「必须出现『查不到』」稳得多。"""
    case = {"答里不许有": [r"[0-9]{1,2}:[0-9]{2}"]}
    assert judge(case, _body(answer="开放时间是 09:00-17:00。")) != []
    assert judge(case, _body(answer="未查询到它的开放时间。")) == []


# ---------- 题库自检 ----------

def test_every_question_carries_a_category_and_at_least_one_expectation():
    """空期望的题**永远会过**，等于没出——它会悄悄把分数抬上去。"""
    for row in load_questions():
        assert row.get("类别"), row
        assert any(key in row for key in ("意图", "该调其一", "不该调", "不该调任何工具",
                                          "记了", "有锚点", "答里要有", "答里有否定",
                                          "答里不许有")), row


def test_no_question_names_a_tool_that_does_not_exist():
    """工具名拼错会让断言**静默失效**：`该调其一` 永远凑不齐、`不该调` 永远不命中。
    `load_questions` 会为这个抛，这里确认它真的在抛。"""
    import pytest

    for row in load_questions():
        for key in ("该调其一", "不该调"):
            for name in row.get(key) or []:
                assert name in TOOL_NAMES, (row["问"], name)

    bad = [{"问": "拼错的工具名", "类别": "测试", "该调其一": ["serch_attractions"]}]
    import tempfile
    from pathlib import Path

    import yaml

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "q.yaml"
        path.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValueError, match="静默失效"):
            load_questions(path)


def test_the_question_bank_is_not_tiny():
    """题库小到几十条以下就没法拿来比分了——一两道题的抖动会盖过真实变化。"""
    assert len(load_questions()) >= 30

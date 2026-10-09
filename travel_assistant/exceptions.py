"""对应 Java exception/TripPlanningException.java。"""

# 高德的「配额/频率」类失败：文案各不相同，但语义都是「这次不是你的参数有问题，是调用太密或额度用尽」，
# 因此值得重试或降级，与「参数不合法」必须分开对待。
# 这四个标记原本只写在 amap_route._should_fallback_to_rule_route 里（路线撞限流回退规则路线），
# 提到这里是因为现在有三处要就「什么算限流」达成一致：client 层的重试、路线层的降级、API 层的错误文案。
_QUOTA_MARKERS = (
    "infocode=10021",
    "CUQPS_HAS_EXCEEDED_THE_LIMIT",
    "USER_DAILY_QUERY_OVER_LIMIT",
    "ACCESS_TOO_FREQUENT",
)


class TripPlanningException(Exception):
    def __init__(self, message: str):
        super().__init__(message)


def is_quota_exhausted(error: object) -> bool:
    """判断这个异常是不是高德的配额/频率类失败。

    放在 exceptions 模块而不是 client 层：API 层要据此换错误文案、client 层要据此重试与降级，
    两边都依赖它，放进任何一侧都会让另一侧反向依赖。
    """
    message = str(error) or ""
    return any(marker in message for marker in _QUOTA_MARKERS)

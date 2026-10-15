"""本项目的异常类型，以及"这次失败算不算高德限流"的判据。"""

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


class UnauthorizedException(Exception):
    """没登录，或会话已过期/无效。

    API 层把它翻成 401 + 项目统一的 ApiErrorResponse 信封，而不是直接用 FastAPI 的
    HTTPException——后者回的是 `{"detail": ...}`，和这里别处的错误长得不一样，前端要写两套解析。
    """

    def __init__(self, message: str = "请先登录"):
        super().__init__(message)


class UsernameTakenException(Exception):
    """注册时用户名已存在。"""

    def __init__(self, username: str):
        super().__init__(username)
        self.username = username


def is_quota_exhausted(error: object) -> bool:
    """判断这个异常是不是高德的配额/频率类失败。

    放在 exceptions 模块而不是 client 层：API 层要据此换错误文案、client 层要据此重试与降级，
    两边都依赖它，放进任何一侧都会让另一侧反向依赖。
    """
    message = str(error) or ""
    return any(marker in message for marker in _QUOTA_MARKERS)

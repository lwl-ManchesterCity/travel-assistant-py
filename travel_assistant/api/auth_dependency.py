"""鉴权依赖：给需要登录的端点用。

单独一个模块而不是塞进 auth_controller：trip_controller / ask_controller 都要 import 它，
放控制器里会变成"控制器 import 控制器"，读起来莫名其妙。
"""

from fastapi import Depends, Request

from ..config.container import get_auth_service
from ..exceptions import UnauthorizedException
from ..models.auth import AuthUser
from ..service.auth_service import AuthService

# cookie 名。前端不直接碰它（httpOnly），改名的唯一影响面在浏览器里那些旧 cookie。
SESSION_COOKIE = "travel_session"


def session_token(request: Request) -> str:
    return (request.cookies.get(SESSION_COOKIE) or "").strip()


def require_current_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUser:
    """没登录 / 会话过期 / token 是伪造的，一律 401。

    三种情况**不给不同文案**：对调用方来说都是"你这个人我没认出来"，
    分开说只会告诉对方他手里的 token 是"过期了"还是"压根不存在"。
    """
    user = auth_service.current_user(session_token(request))
    if user is None:
        raise UnauthorizedException("请先登录")
    return user

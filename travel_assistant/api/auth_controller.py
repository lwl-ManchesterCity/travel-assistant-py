"""注册 / 登录 / 登出 / 当前用户。对应 docs/ASK_LONGTERM_MEMORY.md §2.1.5。"""

from fastapi import APIRouter, Depends, Request, Response

from ..config.container import get_auth_service
from ..models.auth import AuthCredentials, AuthUser, LogoutResult
from ..service.auth_service import AuthService
from .auth_dependency import SESSION_COOKIE, require_current_user, session_token

router = APIRouter(prefix="/api/auth")


@router.post("/register", response_model=AuthUser)
def register(
    credentials: AuthCredentials,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUser:
    """注册**成功后直接登录**（种 cookie），省用户注册完再输一遍。"""
    user, token = auth_service.register(credentials.username, credentials.password)
    _set_session_cookie(response, token, auth_service.session_days)
    return user


@router.post("/login", response_model=AuthUser)
def login(
    credentials: AuthCredentials,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUser:
    user, token = auth_service.login(credentials.username, credentials.password)
    _set_session_cookie(response, token, auth_service.session_days)
    return user


@router.post("/logout", response_model=LogoutResult)
def logout(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> LogoutResult:
    """删掉服务端那一行会话，再让浏览器把 cookie 丢了。

    没登录时调这个也回 200：登出是无副作用的幂等操作，为它报错只会让前端多写一个分支。
    """
    auth_service.logout(session_token(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return LogoutResult()


@router.get("/me", response_model=AuthUser)
def me(user: AuthUser = Depends(require_current_user)) -> AuthUser:
    """前端开页面先打这个，决定显示登录页还是规划页。"""
    return user


def _set_session_cookie(response: Response, token: str, session_days: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=session_days * 24 * 3600,
        httponly=True,      # JS 读不到，XSS 偷不走
        samesite="lax",     # 同源够用，顺带挡掉外站发起的跨站 POST
        path="/",
        # secure 故意没开：本机是 http，开了浏览器**直接不收这个 cookie**，登录会静默失效。
        # 上线换 https 时必须加 secure=True —— 这条记在 docs/ASK_LONGTERM_MEMORY.md §2.1.4。
    )

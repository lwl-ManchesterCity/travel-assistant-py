"""注册 / 登录的入参与出参。对应 docs/ASK_LONGTERM_MEMORY.md §2.1.5。

入参校验放在这里而不是 service 里：校验失败要变成 400 + 统一信封，
走的是 FastAPI 的 RequestValidationError 那条既有通路，在模型上写校验器最省事。
service 那边于是只管"已经合法的用户名密码"这个前提。
"""

from datetime import datetime
from typing import Optional

from pydantic import field_validator

from .base import CamelModel

# bcrypt **只看前 72 字节**，多出来的部分等于不存在；bcrypt 5.x 更是直接抛 ValueError
# （实测：`bcrypt.hashpw(b"a"*100, ...)` → ValueError）。所以这个上限不是"保险起见"，
# 是不卡就会让一个 100 字符的密码变成 500 而不是干净的 400。
# 而且必须按**字节**卡，不按字符——一个汉字 3 字节，24 个汉字就到顶了。
PASSWORD_MAX_BYTES = 72
PASSWORD_MIN_CHARS = 6
USERNAME_MIN_CHARS = 2
USERNAME_MAX_CHARS = 32


class AuthCredentials(CamelModel):
    """注册和登录共用一个入参。"""

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        name = (value or "").strip()
        if not (USERNAME_MIN_CHARS <= len(name) <= USERNAME_MAX_CHARS):
            raise ValueError(f"用户名需 {USERNAME_MIN_CHARS}-{USERNAME_MAX_CHARS} 个字符")
        if any(char.isspace() for char in name):
            raise ValueError("用户名不能含空格")
        return name

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        # 密码**不做 strip**：首尾空格是密码的一部分，擅自去掉会让用户下次登不进来。
        if len(value or "") < PASSWORD_MIN_CHARS:
            raise ValueError(f"密码至少 {PASSWORD_MIN_CHARS} 位")
        if len(value.encode("utf-8")) > PASSWORD_MAX_BYTES:
            raise ValueError(f"密码太长，最多 {PASSWORD_MAX_BYTES} 字节（一个汉字算 3 字节）")
        return value


class AuthUser(CamelModel):
    """回给前端的"当前用户"。**没有 password_hash**——那个字段不该离开服务端。"""

    id: int
    username: str
    created_at: Optional[datetime] = None


class LogoutResult(CamelModel):
    """登出的回执。有个字段而不是空 200，前端好判断。"""

    ok: bool = True

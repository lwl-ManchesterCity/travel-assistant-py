"""「我的偏好」：列出与删除。对应 docs/ASK_LONGTERM_MEMORY.md §2.3.1。

**单独一个控制器文件**，不塞进 ask_controller：偏好不是追问的一部分
（登出、删号这些将来也归它）。

**两条都挂 `require_current_user`**。注意 §2.1.6 那张"挡谁"的表列的是 `/api/trips/*`，
而 `/api/preferences` 是个新前缀、不在其中——判据还是原来那一条："会读写数据"，
而它读写的正是最私密的那一类。照着前缀抄会漏掉它。
"""

from fastapi import APIRouter, Depends

from ..config.container import get_preference_service
from ..models.auth import AuthUser
from ..models.preference import PreferenceDeleteResult, PreferenceItem, PreferenceList
from ..service.preference_service import PreferenceService
from .auth_dependency import require_current_user

router = APIRouter(prefix="/api/preferences")


@router.get("", response_model=PreferenceList)
def list_preferences(
    user: AuthUser = Depends(require_current_user),
    preference_service: PreferenceService = Depends(get_preference_service),
) -> PreferenceList:
    """这个人的全部偏好，**按写入先后**（与注入同序，§2.3.1）。

    只回他自己的：`user_id` 从门禁认出来的身份取，**不接受客户端传**。
    """
    return PreferenceList(items=[
        PreferenceItem(**row.to_payload()) for row in preference_service.list_for_user(user.id)
    ])


@router.delete("/{preference_id}", response_model=PreferenceDeleteResult)
def delete_preference(
    preference_id: int,
    user: AuthUser = Depends(require_current_user),
    preference_service: PreferenceService = Depends(get_preference_service),
) -> PreferenceDeleteResult:
    """删一条。**删别人的 / 已经不存在的都回 200 + `deleted:false`**（§2.3.3）：

    - 与 `POST /api/auth/logout` 同一条理由：幂等操作报错，前端只会多写一个分支；
    - 更要紧的是**不区分**。「不存在」和「不是你的」分成 404 与 403 两种回法，
      等于给攻击者一个"这个 id 存在"的探针——正是 §2.1.5 不区分
      "用户名不存在"与"密码错"的同一个理由。
    """
    return PreferenceDeleteResult(deleted=preference_service.forget(user.id, preference_id))

"""Who the caller is.

Single-user today: every request resolves to the same identity, `alice` by
default, with no password and no login screen. This exists as a FastAPI
dependency rather than a bare constant so that adding real authentication is a
change to `current_user_id` alone - every route and every store call already
carries a user id, and every query already filters on it.

Set SINGLE_USER_ID to something else to prove that scoping works: create a
thread as one value, restart with another, and the thread list comes back
empty and its history 403s. That test is the whole point of the seam.
"""

import os

from fastapi import Request

# Read per call rather than captured at import, so a test (or a restart with a
# different value) takes effect without reimporting the module.
DEFAULT_USER_ID = "alice"


def single_user_id() -> str:
    return os.environ.get("SINGLE_USER_ID") or DEFAULT_USER_ID


async def current_user_id(request: Request) -> str:
    """The owner to attribute this request to.

    When authentication arrives this reads a verified session cookie or bearer
    token off `request` and raises 401 when there isn't one. Until then the
    request is unused and everyone is the same person.
    """
    return single_user_id()

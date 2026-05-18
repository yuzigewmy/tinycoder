from __future__ import annotations

import errno
from typing import Any, Optional


def get_error_code(error: BaseException | Any) -> str | None:
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    err_no = getattr(error, "errno", None)
    if isinstance(err_no, int):
        return errno.errorcode.get(err_no)
    cause = getattr(error, "__cause__", None)
    if cause is not None and cause is not error:
        return get_error_code(cause)
    return None


def is_enoent_error(error: BaseException | Any) -> bool:
    return get_error_code(error) in {"ENOENT", "FileNotFoundError"} or isinstance(error, FileNotFoundError)

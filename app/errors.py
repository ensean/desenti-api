"""统一错误码与异常。

错误码及对应 HTTP 状态码见需求文档第 6 节。
"""

from __future__ import annotations


class ApiError(Exception):
    """业务异常基类。携带错误码、HTTP 状态码与可读消息。"""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str | None = None):
        self.message = message or self.code
        super().__init__(self.message)


class TextTooLongError(ApiError):
    code = "TEXT_TOO_LONG"
    http_status = 400

    def __init__(self, message: str = "输入文本超过 100KB 限制"):
        super().__init__(message)


class EmptyTextError(ApiError):
    code = "EMPTY_TEXT"
    http_status = 400

    def __init__(self, message: str = "输入文本为空"):
        super().__init__(message)


class InvalidOptionsError(ApiError):
    code = "INVALID_OPTIONS"
    http_status = 400

    def __init__(self, message: str = "参数格式错误"):
        super().__init__(message)


class AuthFailedError(ApiError):
    code = "AUTH_FAILED"
    http_status = 401

    def __init__(self, message: str = "认证失败"):
        super().__init__(message)


class RateLimitedError(ApiError):
    code = "RATE_LIMITED"
    http_status = 429

    def __init__(self, message: str = "请求频率超限"):
        super().__init__(message)


class InternalError(ApiError):
    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str = "服务内部错误"):
        super().__init__(message)

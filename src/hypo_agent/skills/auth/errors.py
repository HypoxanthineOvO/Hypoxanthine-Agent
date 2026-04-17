from __future__ import annotations


class AuthFlowError(RuntimeError):
    pass


class NetworkError(AuthFlowError):
    pass


class RiskControlError(AuthFlowError):
    pass


class QrCodeExpiredError(AuthFlowError):
    pass


class LoginTimeoutError(AuthFlowError):
    pass


class BrowserUnavailableError(AuthFlowError):
    pass


class VerificationRequiredError(AuthFlowError):
    pass


class CookieExtractionError(AuthFlowError):
    pass

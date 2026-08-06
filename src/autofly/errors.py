class AutoFlyError(Exception):
    """Base exception carrying a user-safe message."""


class ConfigError(AutoFlyError):
    pass


class SourceError(AutoFlyError):
    pass


class SourceRateLimited(SourceError):
    pass


class SourceOutputError(SourceError):
    pass


class QueryBudgetExceeded(AutoFlyError):
    pass


class LockUnavailable(AutoFlyError):
    pass

from __future__ import annotations


class AutoEnvOperationError(RuntimeError):
    """An operation failed, with its original protocol evidence attached."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.status = getattr(result, "status", "operation_failed")
        self.phase = getattr(result, "phase", None)
        super().__init__(getattr(result, "error_message", None) or str(self.status))

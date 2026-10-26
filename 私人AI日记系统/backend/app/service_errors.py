"""Domain failures with stable status/detail metadata for transport adapters."""
class ServiceOperationError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail

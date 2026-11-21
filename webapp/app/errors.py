class ApiError(Exception):
    """Erreur renvoyée au client sous forme JSON : {"error": {"code", "message"}}."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

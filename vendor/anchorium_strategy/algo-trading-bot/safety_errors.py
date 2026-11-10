"""Shared safety exception; importing it never loads a broker or market model."""


class SafetyError(ValueError):
    pass

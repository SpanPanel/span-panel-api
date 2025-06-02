from enum import Enum


class RelayState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return str(self.value)

"""Allocating local TCP ports for agent servers."""

from __future__ import annotations

import socket
import threading

from .errors import NoFreePortError


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


class PortAllocator:
    """Hands out ports from a fixed range, skipping ports that are reserved or in use."""

    def __init__(self, start: int, end: int) -> None:
        if start > end:
            raise ValueError("port range start must not exceed end")
        self._start = start
        self._end = end
        self._reserved: set[int] = set()
        self._lock = threading.Lock()

    def allocate(self) -> int:
        with self._lock:
            for port in range(self._start, self._end + 1):
                if port not in self._reserved and is_port_free(port):
                    self._reserved.add(port)
                    return port
        raise NoFreePortError(f"no free port in range {self._start}-{self._end}")

    def release(self, port: int) -> None:
        with self._lock:
            self._reserved.discard(port)

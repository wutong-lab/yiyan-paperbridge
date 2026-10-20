"""Worker network is closed except during our fixed-endpoint provider calls."""
from contextlib import contextmanager
import socket
import threading

_access = threading.local()


@contextmanager
def provider_network():
    previous = getattr(_access, "allowed", False)
    _access.allowed = True
    try:
        yield
    finally:
        _access.allowed = previous


def block_worker_network():
    def protect(original):
        def guarded(*args, **kwargs):
            if not getattr(_access, "allowed", False):
                raise OSError("排版资源离线使用；网络仅用于已选择的 DeepSeek 翻译服务")
            return original(*args, **kwargs)
        return guarded
    socket.socket.connect = protect(socket.socket.connect)
    socket.socket.connect_ex = protect(socket.socket.connect_ex)
    socket.create_connection = protect(socket.create_connection)

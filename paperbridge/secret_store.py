"""Per-user native credential storage, with no third-party runtime dependency.

macOS uses the login Keychain. Windows uses user-scoped DPAPI; only ciphertext
is written to disk. Callers never receive OS error details containing input.
"""
import ctypes
import os
from pathlib import Path
import sys
import uuid


class SecretStoreError(RuntimeError):
    pass


def atomic_private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


class MacKeychain:
    label = "macOS 钥匙串"

    def __init__(self, namespace):
        self.service = ("org.paperbridge.api." + namespace).encode("ascii")
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ptr, uint, chars, status = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int32
        signatures = {
            "SecKeychainFindGenericPassword": [ptr, uint, chars, uint, chars, ctypes.POINTER(uint), ctypes.POINTER(ptr), ctypes.POINTER(ptr)],
            "SecKeychainAddGenericPassword": [ptr, uint, chars, uint, chars, uint, ptr, ctypes.POINTER(ptr)],
            "SecKeychainItemModifyAttributesAndData": [ptr, ptr, uint, ptr],
            "SecKeychainItemDelete": [ptr], "SecKeychainItemFreeContent": [ptr, ptr],
        }
        for name, args in signatures.items():
            function = getattr(self.security, name)
            function.argtypes, function.restype = args, status
        self.core.CFRelease.argtypes, self.core.CFRelease.restype = [ptr], None

    @staticmethod
    def _check(status):
        if status:
            raise SecretStoreError("无法访问 macOS 钥匙串，请解锁登录钥匙串，并允许译研保存或读取 API 密钥")

    def _find(self, account, with_data=False):
        account = account.encode("ascii")
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(None, len(self.service), self.service,
            len(account), account, ctypes.byref(length) if with_data else None,
            ctypes.byref(data) if with_data else None, ctypes.byref(item))
        if status == -25300:  # errSecItemNotFound
            return None, None
        self._check(status)
        try:
            value = ctypes.string_at(data, length.value).decode("ascii") if with_data else None
        finally:
            if data:
                self.security.SecKeychainItemFreeContent(None, data)
        return item, value

    def get(self, account):
        item, value = self._find(account, True)
        if item:
            self.core.CFRelease(item)
        return value or ""

    def set(self, account, value):
        raw = value.encode("ascii")
        item, _ = self._find(account)
        try:
            if item:
                self._check(self.security.SecKeychainItemModifyAttributesAndData(item, None, len(raw), raw))
            else:
                account = account.encode("ascii")
                self._check(self.security.SecKeychainAddGenericPassword(None, len(self.service), self.service,
                    len(account), account, len(raw), raw, None))
        finally:
            if item:
                self.core.CFRelease(item)

    def delete(self, account):
        item, _ = self._find(account)
        if item:
            try:
                self._check(self.security.SecKeychainItemDelete(item))
            finally:
                self.core.CFRelease(item)


class WindowsDPAPI:
    label = "Windows 用户加密存储"

    def __init__(self, folder):
        self.folder = Path(folder)

    def _path(self, account):
        # Account IDs are created by the application, never filesystem paths.
        if len(account) != 32 or any(c not in "0123456789abcdef" for c in account):
            raise SecretStoreError("API 配置编号无效")
        return self.folder / (account + ".bin")

    @staticmethod
    def _crypt(value, decrypt=False):
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
        buffer = ctypes.create_string_buffer(value)
        source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        entropy_data = ctypes.create_string_buffer(b"org.paperbridge.api.v1")
        entropy = Blob(len(entropy_data.raw), ctypes.cast(entropy_data, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
        if decrypt:
            function = crypt.CryptUnprotectData
            function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        else:
            function = crypt.CryptProtectData
            function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        if not function(ctypes.byref(source), None if decrypt else "译研 API 密钥", ctypes.byref(entropy),
                        None, None, 1, ctypes.byref(output)):
            raise SecretStoreError("Windows 无法保存或读取密钥，请使用保存配置时的 Windows 用户账户")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel.LocalFree(output.data)

    def get(self, account):
        path = self._path(account)
        return self._crypt(path.read_bytes(), decrypt=True).decode("ascii") if path.exists() else ""

    def set(self, account, value):
        atomic_private_write(self._path(account), self._crypt(value.encode("ascii")))

    def delete(self, account):
        self._path(account).unlink(missing_ok=True)


def native_store(folder, namespace):
    if sys.platform == "darwin":
        return MacKeychain(namespace)
    if os.name == "nt":
        return WindowsDPAPI(Path(folder) / ".api-secrets")
    raise SecretStoreError("保存 API 密钥目前支持 macOS 和 Windows")

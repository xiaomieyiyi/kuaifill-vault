"""密码库加密层。

只依赖 Python 标准库 + Windows 自带的 CNG/BCrypt 加密接口：
- 主密码通过 scrypt 派生 64 字节，再 HKDF 分离出加密密钥与校验密钥；
- 每条记录使用 AES-256-GCM 认证加密，任何篡改都会解密失败；
- 数据库里不保存主密码，也不保存明文账号密码。
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import secrets
from ctypes import wintypes
from typing import Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

KDF_N = 2 ** 15
KDF_R = 8
KDF_P = 1
KDF_MAXMEM = 128 * 1024 * 1024
KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
BLOB_VERSION = 1

VERIFIER_LABEL = b"kuaifill-vault-verifier-v1"
HKDF_INFO = b"kuaifill-vault-keys-v1"


class CryptoError(Exception):
    """加密/解密失败。"""


class WrongPassword(CryptoError):
    """主密码不正确。"""


class TamperedData(CryptoError):
    """数据被篡改或损坏。"""


# ---------------------------------------------------------------------------
# Windows BCrypt (CNG) AES-GCM
# ---------------------------------------------------------------------------

if os.name != "nt":  # pragma: no cover - 本程序面向 Windows
    raise RuntimeError("本程序使用 Windows CNG 加密接口，仅支持 Windows。")

_bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)

NTSTATUS = ctypes.c_long
BCRYPT_ALG_HANDLE = ctypes.c_void_p
BCRYPT_KEY_HANDLE = ctypes.c_void_p
ULONG = wintypes.ULONG
PUCHAR = ctypes.POINTER(ctypes.c_ubyte)

BCRYPT_AES_ALGORITHM = "AES"
BCRYPT_CHAINING_MODE = "ChainingMode"
BCRYPT_CHAIN_MODE_GCM = "ChainingModeGCM"
BCRYPT_OBJECT_LENGTH = "ObjectLength"

STATUS_SUCCESS = 0
STATUS_AUTH_TAG_MISMATCH = 0xC000A002


class BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ULONG),
        ("dwInfoVersion", ULONG),
        ("pbNonce", PUCHAR),
        ("cbNonce", ULONG),
        ("pbAuthData", PUCHAR),
        ("cbAuthData", ULONG),
        ("pbTag", PUCHAR),
        ("cbTag", ULONG),
        ("pbMacContext", PUCHAR),
        ("cbMacContext", ULONG),
        ("cbAAD", ULONG),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", ULONG),
    ]


_bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
    ctypes.POINTER(BCRYPT_ALG_HANDLE),
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ULONG,
]
_bcrypt.BCryptOpenAlgorithmProvider.restype = NTSTATUS

_bcrypt.BCryptCloseAlgorithmProvider.argtypes = [BCRYPT_ALG_HANDLE, ULONG]
_bcrypt.BCryptCloseAlgorithmProvider.restype = NTSTATUS

_bcrypt.BCryptSetProperty.argtypes = [
    BCRYPT_ALG_HANDLE,
    wintypes.LPCWSTR,
    PUCHAR,
    ULONG,
    ULONG,
]
_bcrypt.BCryptSetProperty.restype = NTSTATUS

_bcrypt.BCryptGetProperty.argtypes = [
    BCRYPT_ALG_HANDLE,
    wintypes.LPCWSTR,
    PUCHAR,
    ULONG,
    ctypes.POINTER(ULONG),
    ULONG,
]
_bcrypt.BCryptGetProperty.restype = NTSTATUS

_bcrypt.BCryptGenerateSymmetricKey.argtypes = [
    BCRYPT_ALG_HANDLE,
    ctypes.POINTER(BCRYPT_KEY_HANDLE),
    PUCHAR,
    ULONG,
    PUCHAR,
    ULONG,
    ULONG,
]
_bcrypt.BCryptGenerateSymmetricKey.restype = NTSTATUS

_bcrypt.BCryptDestroyKey.argtypes = [BCRYPT_KEY_HANDLE]
_bcrypt.BCryptDestroyKey.restype = NTSTATUS

_bcrypt.BCryptEncrypt.argtypes = [
    BCRYPT_KEY_HANDLE,
    PUCHAR,
    ULONG,
    ctypes.c_void_p,
    PUCHAR,
    ULONG,
    PUCHAR,
    ULONG,
    ctypes.POINTER(ULONG),
    ULONG,
]
_bcrypt.BCryptEncrypt.restype = NTSTATUS

_bcrypt.BCryptDecrypt.argtypes = [
    BCRYPT_KEY_HANDLE,
    PUCHAR,
    ULONG,
    ctypes.c_void_p,
    PUCHAR,
    ULONG,
    PUCHAR,
    ULONG,
    ctypes.POINTER(ULONG),
    ULONG,
]
_bcrypt.BCryptDecrypt.restype = NTSTATUS


def _check(status: int, what: str) -> None:
    if status != STATUS_SUCCESS:
        code = status & 0xFFFFFFFF
        raise CryptoError(f"{what} 失败（Windows 错误 0x{code:08X}）")


def _as_buffer(data: bytes) -> tuple[object, PUCHAR, int]:
    """返回 (keepalive, pointer, length)。空数据返回 NULL 指针。"""
    if not data:
        return None, PUCHAR(), 0
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return buf, ctypes.cast(buf, PUCHAR), len(data)


class AesGcmKey:
    """一个 AES-256-GCM 密钥会话。务必调用 close()/使用 with。"""

    def __init__(self, key: bytes):
        if len(key) != KEY_LEN:
            raise ValueError("AES-256 密钥必须是 32 字节")
        self._alg = BCRYPT_ALG_HANDLE()
        self._key = BCRYPT_KEY_HANDLE()
        self._key_obj = None
        self._closed = False
        try:
            _check(
                _bcrypt.BCryptOpenAlgorithmProvider(
                    ctypes.byref(self._alg), BCRYPT_AES_ALGORITHM, None, 0
                ),
                "打开 AES 算法",
            )
            mode = BCRYPT_CHAIN_MODE_GCM
            mode_bytes = (mode + "\0").encode("utf-16-le")
            mode_buf = (ctypes.c_ubyte * len(mode_bytes)).from_buffer_copy(mode_bytes)
            _check(
                _bcrypt.BCryptSetProperty(
                    self._alg,
                    BCRYPT_CHAINING_MODE,
                    ctypes.cast(mode_buf, PUCHAR),
                    len(mode_bytes),
                    0,
                ),
                "设置 GCM 模式",
            )

            obj_len = ULONG()
            obj_len_result = ULONG()
            _check(
                _bcrypt.BCryptGetProperty(
                    self._alg,
                    BCRYPT_OBJECT_LENGTH,
                    ctypes.cast(ctypes.byref(obj_len), PUCHAR),
                    ctypes.sizeof(obj_len),
                    ctypes.byref(obj_len_result),
                    0,
                ),
                "读取密钥对象长度",
            )
            if obj_len_result.value != ctypes.sizeof(ULONG):
                raise CryptoError("读取 AES 密钥对象长度异常")
            self._key_obj = (ctypes.c_ubyte * obj_len.value)()
            secret_buf, secret_ptr, secret_len = _as_buffer(key)
            _check(
                _bcrypt.BCryptGenerateSymmetricKey(
                    self._alg,
                    ctypes.byref(self._key),
                    self._key_obj,
                    obj_len.value,
                    secret_ptr,
                    secret_len,
                    0,
                ),
                "导入 AES 密钥",
            )
        except Exception:
            self.close()
            raise

    def _auth_info(self, nonce_buf, nonce_ptr, tag_buf, tag_ptr, aad_buf, aad_ptr, aad_len, data_len, tag_len=TAG_LEN):
        info = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
        info.cbSize = ctypes.sizeof(info)
        info.dwInfoVersion = 1
        info.pbNonce = nonce_ptr
        info.cbNonce = NONCE_LEN
        info.pbAuthData = aad_ptr
        info.cbAuthData = aad_len
        info.pbTag = tag_ptr
        info.cbTag = tag_len
        info.pbMacContext = PUCHAR()
        info.cbMacContext = 0
        info.cbAAD = aad_len
        info.cbData = data_len
        info.dwFlags = 0
        return info

    def encrypt(self, plaintext: bytes, aad: bytes = b"", nonce: Optional[bytes] = None) -> bytes:
        if self._closed:
            raise CryptoError("密钥已关闭")
        if nonce is None:
            nonce = secrets.token_bytes(NONCE_LEN)
        if len(nonce) != NONCE_LEN:
            raise ValueError("nonce 必须是 12 字节")
        nonce_buf, nonce_ptr, _ = _as_buffer(nonce)
        aad_buf, aad_ptr, aad_len = _as_buffer(aad)
        out_len = len(plaintext)
        out_buf = (ctypes.c_ubyte * max(1, out_len))()
        tag_buf = (ctypes.c_ubyte * TAG_LEN)()
        info = self._auth_info(
            nonce_buf,
            nonce_ptr,
            tag_buf,
            ctypes.cast(tag_buf, PUCHAR),
            aad_buf,
            aad_ptr,
            aad_len,
            out_len,
        )
        ptext_buf, ptext_ptr, ptext_len = _as_buffer(plaintext)
        result = ULONG()
        status = _bcrypt.BCryptEncrypt(
            self._key,
            ptext_ptr,
            ptext_len,
            ctypes.cast(ctypes.byref(info), ctypes.c_void_p),
            None,
            0,
            ctypes.cast(out_buf, PUCHAR),
            out_len,
            ctypes.byref(result),
            0,
        )
        _check(status, "AES-GCM 加密")
        if result.value != out_len:
            raise CryptoError("AES-GCM 加密长度异常")
        return bytes(out_buf[:out_len]) + bytes(tag_buf)

    def decrypt(self, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
        return self.decrypt_parts(nonce, ciphertext, tag, aad)

    def decrypt_parts(self, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
        if self._closed:
            raise CryptoError("密钥已关闭")
        if len(nonce) != NONCE_LEN or len(tag) != TAG_LEN:
            raise TamperedData("密文结构损坏")
        nonce_buf, nonce_ptr, _ = _as_buffer(nonce)
        aad_buf, aad_ptr, aad_len = _as_buffer(aad)
        tag_buf, tag_ptr, _ = _as_buffer(tag)
        out_len = len(ciphertext)
        out_buf = (ctypes.c_ubyte * max(1, out_len))()
        info = self._auth_info(
            nonce_buf,
            nonce_ptr,
            tag_buf,
            tag_ptr,
            aad_buf,
            aad_ptr,
            aad_len,
            out_len,
        )
        ctext_buf, ctext_ptr, ctext_len = _as_buffer(ciphertext)
        result = ULONG()
        status = _bcrypt.BCryptDecrypt(
            self._key,
            ctext_ptr,
            ctext_len,
            ctypes.cast(ctypes.byref(info), ctypes.c_void_p),
            None,
            0,
            ctypes.cast(out_buf, PUCHAR),
            out_len,
            ctypes.byref(result),
            0,
        )
        if (status & 0xFFFFFFFF) == STATUS_AUTH_TAG_MISMATCH:
            raise TamperedData("认证标签不匹配：密码错误或数据被篡改")
        _check(status, "AES-GCM 解密")
        if result.value != out_len:
            raise TamperedData("AES-GCM 解密长度异常")
        return bytes(out_buf[:out_len])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._key:
            _bcrypt.BCryptDestroyKey(self._key)
            self._key = BCRYPT_KEY_HANDLE()
        if self._alg:
            _bcrypt.BCryptCloseAlgorithmProvider(self._alg, 0)
            self._alg = BCRYPT_ALG_HANDLE()
        self._key_obj = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def seal(key: AesGcmKey, plaintext: bytes, aad: bytes = b"") -> bytes:
    """格式：版本(1) | nonce(12) | 密文 | tag(16)。"""
    nonce = secrets.token_bytes(NONCE_LEN)
    body = key.encrypt(plaintext, aad, nonce=nonce)
    return bytes([BLOB_VERSION]) + nonce + body


def open_sealed(key: AesGcmKey, blob: bytes, aad: bytes = b"") -> bytes:
    if len(blob) < 1 + NONCE_LEN + TAG_LEN:
        raise TamperedData("密文长度不足")
    if blob[0] != BLOB_VERSION:
        raise TamperedData("不支持的密文版本")
    nonce = blob[1 : 1 + NONCE_LEN]
    body = blob[1 + NONCE_LEN :]
    ciphertext, tag = body[:-TAG_LEN], body[-TAG_LEN:]
    return key.decrypt_parts(nonce, ciphertext, tag, aad)


# ---------------------------------------------------------------------------
# 主密码派生
# ---------------------------------------------------------------------------

def derive_master_keys(password: str, salt: bytes) -> tuple[bytes, bytes]:
    """返回 (enc_key, mac_key)。scrypt 抗暴力破解，HKDF 做密钥分离。"""
    if not password:
        raise ValueError("主密码不能为空")
    if len(salt) != SALT_LEN:
        raise ValueError("salt 长度错误")
    material = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=KDF_N,
        r=KDF_R,
        p=KDF_P,
        maxmem=KDF_MAXMEM,
        dklen=64,
    )
    # 手写 HKDF-SHA256（RFC 5869）
    prk = hmac.new(salt, material, hashlib.sha256).digest()
    out = b""
    t = b""
    counter = 1
    while len(out) < 64:
        t = hmac.new(prk, t + HKDF_INFO + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:32], out[32:64]


def make_verifier(mac_key: bytes) -> bytes:
    return hmac.new(mac_key, VERIFIER_LABEL, hashlib.sha256).digest()


def verify_master_password(password: str, salt: bytes, verifier: bytes) -> tuple[bytes, bytes]:
    enc_key, mac_key = derive_master_keys(password, salt)
    if not hmac.compare_digest(make_verifier(mac_key), verifier):
        raise WrongPassword("主密码不正确")
    return enc_key, mac_key


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_LEN)


def random_password(length: int = 20, upper: bool = True, digits: bool = True, symbols: bool = True,
                    avoid_ambiguous: bool = True) -> str:
    """生成密码，保证每类至少一个。"""
    import string

    lower = "abcdefghijkmnopqrstuvwxyz" if avoid_ambiguous else string.ascii_lowercase
    upper_chars = "ABCDEFGHJKLMNPQRSTUVWXYZ" if avoid_ambiguous else string.ascii_uppercase
    digit_chars = "23456789" if avoid_ambiguous else string.digits
    symbol_chars = "!@#$%^&*()-_=+[]{};:,.?"
    pools = [lower]
    if upper:
        pools.append(upper_chars)
    if digits:
        pools.append(digit_chars)
    if symbols:
        pools.append(symbol_chars)
    if length < len(pools):
        length = len(pools)
    chars = [secrets.choice(p) for p in pools]
    all_chars = "".join(pools)
    chars += [secrets.choice(all_chars) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def password_strength(password: str) -> tuple[int, str]:
    """粗略强度评分 0-4，用于界面提示。"""
    import re

    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if re.search(r"[a-z]", password) and re.search(r"[A-Z]", password):
        score += 1
    if re.search(r"\d", password) and re.search(r"[^A-Za-z0-9]", password):
        score += 1
    labels = ["很弱", "较弱", "一般", "较强", "很强"]
    return score, labels[score]

"""加密密码库的持久化层（SQLite）。

数据库里只有：
- 明文的随机记录 ID / 时间戳 / 唯一性 HMAC；
- AES-256-GCM 加密后的整条账号记录；
- 主密码的 scrypt salt 与 HMAC 校验值（不保存主密码）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .crypto import (
    AesGcmKey,
    TamperedData,
    WrongPassword,
    derive_master_keys,
    make_verifier,
    new_salt,
    open_sealed,
    seal,
    verify_master_password,
)

SCHEMA_VERSION = 1
DEFAULT_CATEGORIES = ["常用", "工作", "学习", "购物", "社交", "其他"]
DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_lock_minutes": 5,
    "hotkey": "Ctrl+Alt+K",
    "autotype_submit": False,
    "clipboard_clear_seconds": 25,
    "autotype_delay_ms": 8,
}


class VaultError(Exception):
    """密码库业务错误。"""


class VaultNotFound(VaultError):
    pass


class VaultLocked(VaultError):
    pass


class DuplicateEntry(VaultError):
    pass


class ValidationError(VaultError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def normalize(text: str) -> str:
    return " ".join((text or "").strip().split()).casefold()


@dataclass
class Entry:
    id: str = ""
    title: str = ""
    account: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    category: str = "未分类"
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    created_at: str = ""
    updated_at: str = ""

    def identity_text(self) -> str:
        return f"{normalize(self.title)}\x00{normalize(self.account)}"

    def to_payload(self) -> bytes:
        data = {
            "title": self.title,
            "account": self.account,
            "password": self.password,
            "url": self.url,
            "notes": self.notes,
            "category": self.category,
            "tags": list(self.tags),
            "favorite": bool(self.favorite),
        }
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_payload(cls, entry_id: str, payload: bytes, created_at: str, updated_at: str) -> "Entry":
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise TamperedData("记录内容损坏") from exc
        return cls(
            id=entry_id,
            title=str(data.get("title", "")),
            account=str(data.get("account", "")),
            password=str(data.get("password", "")),
            url=str(data.get("url", "")),
            notes=str(data.get("notes", "")),
            category=str(data.get("category") or "未分类"),
            tags=[str(t) for t in data.get("tags", []) if str(t).strip()],
            favorite=bool(data.get("favorite", False)),
            created_at=created_at,
            updated_at=updated_at,
        )

    def masked_password(self) -> str:
        return "•" * min(max(len(self.password), 6), 18)


class VaultDB:
    """一个主密码对应一个 vault.db。"""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.conn: Optional[sqlite3.Connection] = None
        self._aes: Optional[AesGcmKey] = None
        self._mac_key: Optional[bytes] = None
        self._entries: dict[str, Entry] = {}
        self._settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.salt: bytes = b""

    # ------------------------------------------------------------------ 创建/打开
    @classmethod
    def exists(cls, path: str | os.PathLike[str]) -> bool:
        return Path(path).is_file() and Path(path).stat().st_size > 0

    @classmethod
    def create(cls, path: str | os.PathLike[str], password: str) -> "VaultDB":
        path = Path(path)
        if cls.exists(path):
            raise VaultError("密码库已存在，请直接解锁")
        if len(password) < 8:
            raise ValidationError("主密码至少 8 个字符")
        path.parent.mkdir(parents=True, exist_ok=True)
        db = cls(path)
        salt = new_salt()
        enc_key, mac_key = derive_master_keys(password, salt)
        db.salt = salt
        db._mac_key = mac_key
        db._aes = AesGcmKey(enc_key)
        try:
            db.conn = sqlite3.connect(str(path))
            db.conn.execute("PRAGMA journal_mode=DELETE")
            db.conn.execute("PRAGMA synchronous=FULL")
            db._create_schema()
            db._set_meta("schema_version", str(SCHEMA_VERSION).encode())
            db._set_meta("salt", salt)
            db._set_meta("verifier", make_verifier(mac_key))
            db._set_meta("created_at", utc_now().encode())
            db._set_meta("settings", json.dumps(DEFAULT_SETTINGS, ensure_ascii=False).encode())
            db.conn.commit()
            db._settings = dict(DEFAULT_SETTINGS)
        except Exception:
            db.close()
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        return db

    @classmethod
    def open(cls, path: str | os.PathLike[str], password: str) -> "VaultDB":
        path = Path(path)
        if not cls.exists(path):
            raise VaultNotFound("密码库文件不存在")
        db = cls(path)
        db.conn = sqlite3.connect(str(path))
        db.conn.execute("PRAGMA journal_mode=DELETE")
        db.conn.execute("PRAGMA synchronous=FULL")
        try:
            salt = db._get_meta("salt")
            verifier = db._get_meta("verifier")
            version = int(db._get_meta("schema_version").decode())
            if version > SCHEMA_VERSION:
                raise VaultError("这个密码库由更高版本的程序创建，请升级程序")
            enc_key, mac_key = verify_master_password(password, salt, verifier)
            db.salt = salt
            db._mac_key = mac_key
            db._aes = AesGcmKey(enc_key)
            db._settings = db._load_settings()
            db._reload_entries()
            return db
        except WrongPassword:
            db.close()
            raise
        except Exception:
            db.close()
            raise

    def _create_schema(self) -> None:
        assert self.conn
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                identity_hash BLOB NOT NULL UNIQUE,
                payload BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entries_updated ON entries(updated_at DESC);
            """
        )

    def _get_meta(self, key: str) -> bytes:
        assert self.conn
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            raise VaultError("密码库格式损坏：缺少元数据")
        return bytes(row[0])

    def _set_meta(self, key: str, value: bytes) -> None:
        assert self.conn
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, sqlite3.Binary(value)),
        )

    # ------------------------------------------------------------------ 生命周期
    @property
    def is_unlocked(self) -> bool:
        return self._aes is not None and self.conn is not None

    def _require_unlocked(self) -> None:
        if not self.is_unlocked:
            raise VaultLocked("密码库已锁定")

    def lock(self) -> None:
        if self._aes:
            self._aes.close()
        self._aes = None
        self._mac_key = None
        self._entries.clear()
        if self.conn:
            self.conn.close()
            self.conn = None

    def close(self) -> None:
        self.lock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ 设置
    def _load_settings(self) -> dict[str, Any]:
        try:
            raw = self._get_meta("settings")
            data = json.loads(raw.decode("utf-8"))
            settings = dict(DEFAULT_SETTINGS)
            if isinstance(data, dict):
                settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
            return settings
        except Exception:
            return dict(DEFAULT_SETTINGS)

    @property
    def settings(self) -> dict[str, Any]:
        return dict(self._settings)

    def update_settings(self, **kwargs: Any) -> dict[str, Any]:
        self._require_unlocked()
        for key, value in kwargs.items():
            if key not in DEFAULT_SETTINGS:
                raise ValidationError(f"未知设置项：{key}")
            self._settings[key] = value
        assert self.conn
        self._set_meta("settings", json.dumps(self._settings, ensure_ascii=False).encode())
        self.conn.commit()
        return self.settings

    # ------------------------------------------------------------------ 记录
    def _identity_hash(self, entry: Entry) -> bytes:
        assert self._mac_key
        return hmac.new(self._mac_key, b"identity\x00" + entry.identity_text().encode("utf-8"), hashlib.sha256).digest()

    def _encrypt_entry(self, entry: Entry) -> bytes:
        assert self._aes
        return seal(self._aes, entry.to_payload(), entry.id.encode("ascii"))

    def _decrypt_entry(self, row: tuple) -> Entry:
        assert self._aes
        entry_id, payload, created_at, updated_at = row
        try:
            plaintext = open_sealed(self._aes, bytes(payload), entry_id.encode("ascii"))
        except TamperedData as exc:
            raise TamperedData(f"记录 {entry_id[:8]}… 无法解密，可能被篡改") from exc
        return Entry.from_payload(entry_id, plaintext, created_at, updated_at)

    def _reload_entries(self) -> None:
        self._require_unlocked()
        assert self.conn
        rows = self.conn.execute(
            "SELECT id,payload,created_at,updated_at FROM entries"
        ).fetchall()
        entries: dict[str, Entry] = {}
        for row in rows:
            entry = self._decrypt_entry(row)
            entries[entry.id] = entry
        self._entries = entries

    def reload(self) -> None:
        self._reload_entries()

    def validate_entry(self, entry: Entry) -> None:
        if not entry.title.strip():
            raise ValidationError("请填写网站/应用名称")
        if not entry.account.strip():
            raise ValidationError("请填写账号")
        if len(entry.title) > 200 or len(entry.account) > 300:
            raise ValidationError("名称或账号过长")
        if len(entry.password) > 2000 or len(entry.notes) > 10000:
            raise ValidationError("密码或备注过长")

    def add_entry(self, entry: Entry) -> Entry:
        self._require_unlocked()
        self.validate_entry(entry)
        assert self.conn and self._mac_key
        entry.id = entry.id or str(uuid.uuid4())
        entry.created_at = entry.created_at or utc_now()
        entry.updated_at = utc_now()
        identity = self._identity_hash(entry)
        try:
            self.conn.execute(
                "INSERT INTO entries(id,identity_hash,payload,created_at,updated_at) VALUES(?,?,?,?,?)",
                (
                    entry.id,
                    sqlite3.Binary(identity),
                    sqlite3.Binary(self._encrypt_entry(entry)),
                    entry.created_at,
                    entry.updated_at,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            if "identity_hash" in str(exc) or "UNIQUE" in str(exc):
                raise DuplicateEntry(f"“{entry.title}”下已经有账号“{entry.account}”") from exc
            raise
        self._entries[entry.id] = entry
        return entry

    def update_entry(self, entry: Entry) -> Entry:
        self._require_unlocked()
        self.validate_entry(entry)
        assert self.conn and self._mac_key
        if entry.id not in self._entries:
            raise VaultError("记录不存在")
        entry.updated_at = utc_now()
        identity = self._identity_hash(entry)
        try:
            self.conn.execute(
                "UPDATE entries SET identity_hash=?,payload=?,updated_at=? WHERE id=?",
                (
                    sqlite3.Binary(identity),
                    sqlite3.Binary(self._encrypt_entry(entry)),
                    entry.updated_at,
                    entry.id,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise DuplicateEntry(f"“{entry.title}”下已经有账号“{entry.account}”") from exc
        self._entries[entry.id] = entry
        return entry

    def delete_entry(self, entry_id: str) -> None:
        self._require_unlocked()
        assert self.conn
        self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self.conn.commit()
        self._entries.pop(entry_id, None)

    def get_entry(self, entry_id: str) -> Optional[Entry]:
        self._require_unlocked()
        return self._entries.get(entry_id)

    def list_entries(self) -> list[Entry]:
        self._require_unlocked()
        return sorted(
            self._entries.values(),
            key=lambda e: (not e.favorite, e.title.casefold(), e.account.casefold()),
        )

    def search(self, query: str = "", category: Optional[str] = None) -> list[Entry]:
        self._require_unlocked()
        q = normalize(query)
        results = []
        for entry in self._entries.values():
            if category and category != "全部" and entry.category != category:
                continue
            if q:
                haystack = normalize(
                    " ".join(
                        [
                            entry.title,
                            entry.account,
                            entry.url,
                            entry.notes,
                            entry.category,
                            " ".join(entry.tags),
                        ]
                    )
                )
                if q not in haystack:
                    continue
            results.append(entry)
        return sorted(
            results,
            key=lambda e: (not e.favorite, e.title.casefold(), e.account.casefold()),
        )

    def categories(self) -> list[str]:
        self._require_unlocked()
        cats = {e.category for e in self._entries.values() if e.category}
        ordered = [c for c in DEFAULT_CATEGORIES if c in cats]
        ordered += sorted(cats - set(ordered))
        return ordered

    def count(self) -> int:
        self._require_unlocked()
        return len(self._entries)

    # ------------------------------------------------------------------ 主密码
    def change_master_password(self, old_password: str, new_password: str) -> None:
        self._require_unlocked()
        if len(new_password) < 8:
            raise ValidationError("新主密码至少 8 个字符")
        if old_password == new_password:
            raise ValidationError("新主密码不能与旧主密码相同")
        assert self.conn and self._aes and self._mac_key
        # 先验证旧密码
        verify_master_password(old_password, self.salt, make_verifier(self._mac_key))
        new_salt_bytes = new_salt()
        new_enc, new_mac = derive_master_keys(new_password, new_salt_bytes)
        new_aes = AesGcmKey(new_enc)
        try:
            # 记录先解密再重新加密
            rows = self.conn.execute(
                "SELECT id,payload,created_at,updated_at FROM entries"
            ).fetchall()
            reencrypted = []
            for row in rows:
                old_entry = self._decrypt_entry(row)
                payload = seal(new_aes, old_entry.to_payload(), old_entry.id.encode("ascii"))
                identity = hmac.new(
                    new_mac,
                    b"identity\x00" + old_entry.identity_text().encode("utf-8"),
                    hashlib.sha256,
                ).digest()
                reencrypted.append(
                    (
                        sqlite3.Binary(payload),
                        sqlite3.Binary(identity),
                        old_entry.updated_at,
                        old_entry.id,
                    )
                )
            with self.conn:
                for payload, identity, updated_at, entry_id in reencrypted:
                    self.conn.execute(
                        "UPDATE entries SET payload=?, identity_hash=?, updated_at=? WHERE id=?",
                        (payload, identity, updated_at, entry_id),
                    )
                self._set_meta("salt", new_salt_bytes)
                self._set_meta("verifier", make_verifier(new_mac))
                self._set_meta("password_changed_at", utc_now().encode())
        except Exception:
            new_aes.close()
            raise
        # 切换密钥
        self._aes.close()
        self._aes = new_aes
        self._mac_key = new_mac
        self.salt = new_salt_bytes
        self._reload_entries()

    # ------------------------------------------------------------------ 备份
    def backup(self, backup_dir: Optional[str | os.PathLike[str]] = None) -> Path:
        self._require_unlocked()
        assert self.conn
        if backup_dir is None:
            backup_dir = self.path.parent / "backups"
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"vault-{stamp}.db"
        n = 1
        while dest.exists():
            dest = backup_dir / f"vault-{stamp}-{n}.db"
            n += 1
        dst = sqlite3.connect(str(dest))
        try:
            self.conn.backup(dst)
            dst.commit()
        finally:
            dst.close()
        return dest

    def db_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

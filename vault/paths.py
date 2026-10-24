"""程序路径。数据默认放在程序目录的 data/ 下，便于备份和搬走。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    """打包后静态资源在 PyInstaller 的 _internal 目录中。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return app_dir()


def data_dir() -> Path:
    override = os.environ.get("KUAIFILL_DATA_DIR")
    path = Path(override).expanduser() if override else app_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    override = os.environ.get("KUAIFILL_DB")
    if override:
        return Path(override).expanduser()
    return data_dir() / "vault.db"


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return data_dir() / "app.log"

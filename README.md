# 快填密码库（KuaiFill Vault）

一个 Windows 本地账号密码库：账号和密码分开保存，登录时按一次快捷键，自动填入当前登录窗口。

## 直接使用

解压 `快填密码库-v1.0.zip`，进入 `快填密码库` 文件夹，双击：

- `启动快填密码库.bat`，或
- `KuaiFillVault.exe`

第一次使用需要设置主密码。详细步骤见 [使用说明.md](使用说明.md)。

## 功能

- AES-256-GCM 加密的 SQLite 数据库（Windows CNG，无第三方加密库）
- scrypt + HKDF 主密码派生；数据库中没有明文账号密码
- 按“网站/应用 + 账号”区分多个账号，支持搜索、分类、标签、备注、收藏
- 默认 `Ctrl+Alt+K` 呼出快速填充：账号 → Tab → 密码
- 自动锁定、剪贴板定时清除、加密备份、修改主密码
- 完全离线，不联网、不上传

## 从源码运行

需要 Windows + Python 3.12 + Tkinter：

```bat
python app.py
```

运行测试：

```bat
python -m unittest discover -s tests -v
```

代码只使用 Python 标准库和 Windows 系统 API。

## 目录

```
app.py                 程序入口
vault/crypto.py        AES-GCM / scrypt / HKDF
vault/db.py            SQLite 加密存储
vault/autotype.py      全局快捷键 / SendInput
vault/overlay.py       快速填充浮窗
vault/dialogs.py       新建、设置、修改主密码
vault/ui.py            主界面
tests/                 自动化测试
release/               已打包的 Windows 程序
```

## 安全提醒

主密码没有后门，忘记后无法找回。请定期备份 `data` 文件夹，并确保电脑本身没有木马或键盘记录器。

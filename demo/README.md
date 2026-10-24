# 演示密码库

`demo-vault.db` 是一个真实的加密密码库文件，用于体验程序界面和快速填充。

- **演示主密码：`DemoOnly!2026`**
- 内含 3 条虚构账号：GitHub、Gmail、学校教务系统
- 所有账号、密码、备注都是编造的，不包含任何真实凭据
- 这个主密码是公开的，**绝对不要**在演示库中保存真实账号

## 打开方式

在程序目录执行：

```bat
KuaiFillVault.exe --db demo\demo-vault.db
```

或者先备份自己的 `data\vault.db`，再把演示库复制为 `data\vault.db`：

```bat
copy demo\demo-vault.db data\vault.db
```

## 说明

程序不会自动打开任意位置的数据库；默认只读取程序目录下的 `data\vault.db`。所以演示库要么通过 `--db` 参数指定，要么复制到 `data\vault.db`。

演示结束后，请恢复自己的数据库备份，或删除演示库，重新创建自己的密码库。

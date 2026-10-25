# 一键打包 Windows 便携版（需要联网安装 PyInstaller）
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File devtools/build_release.ps1
#
# 产物：
#   dist\KuaiFillVault\            exe + _internal
#   release\KuaiFillVault-<版本>-win64.zip
#   release\SHA256.txt

param([string]$Version = "1.0.1")

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$python = $env:PYTHON
if (-not $python) { $python = "python" }

Write-Host "== 安装 PyInstaller 到 .buildtools =="
& $python -m pip install --no-cache-dir --disable-pip-version-check --target ".buildtools" pyinstaller
$env:PYTHONPATH = Join-Path $root ".buildtools"

Write-Host "== 打包 =="
& $python -m PyInstaller --noconfirm --clean --windowed --name KuaiFillVault `
    --icon "assets\icon.ico" --add-data "assets;assets" app.py

Write-Host "== 组装便携包 =="
$stage = Join-Path $root ".release\快填密码库"
if (Test-Path ".release") { Remove-Item ".release" -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item "dist\KuaiFillVault\*" $stage -Recurse -Force
Copy-Item "使用说明.md", "第三方组件说明.md", "启动快填密码库.bat" $stage -Force

$zip = Join-Path $root "release\KuaiFillVault-$Version-win64.zip"
New-Item -ItemType Directory -Force "release" | Out-Null
if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stage, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)

$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
"$hash  KuaiFillVault-$Version-win64.zip" | Set-Content "release\SHA256.txt" -Encoding ascii

Write-Host "== 完成 =="
Write-Host $zip
Write-Host "SHA256 $hash"

# README

**asr tob 相关client demo**

# Notice
python version: python 3.x

使用示例：
    ./\.venv/bin/python sauc_websocket_demo.py --file ./your.wav

## Hammerspoon 快捷键检测（Phase 1）
本项目提供一个最小的 Hammerspoon 脚本用于验证“全局快捷键检测 + 逐字注入”是否打通。

### 1) 安装 Hammerspoon
- 官网下载安装：`https://www.hammerspoon.org/`

### 2) 放置配置并重载
把本仓库的 `hammerspoon/init.lua` 复制到：
- `~/.hammerspoon/init.lua`

然后打开 Hammerspoon：
- 点击菜单栏图标 → **Reload Config**

### 3) macOS 权限（必须）
系统设置 → 隐私与安全性：
- **辅助功能（Accessibility）**：允许 Hammerspoon（用于 `hs.eventtap.keyStrokes` 注入）
（如系统提示，也允许“输入监控/监听键盘”相关权限）

### 4) 热键
- `Cmd+Shift+Space`：切换 START/STOP（目前只提示/打印）
- `Cmd+Shift+V`：向当前输入框打字测试字符串（验证注入）




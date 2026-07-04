以下是一份适用于 GitHub 项目的完整 `README.md` 文件。内容为中文，结构清晰，涵盖了项目介绍、功能特性、技术栈、安装使用、配置说明、版本历史、贡献指南等所有必要章节，可直接复制到你的仓库中。

---

```markdown
# 🖥️ 桌面隐身工具 (Desktop Invisible Tool)

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-green)
![License](https://img.shields.io/badge/license-MIT-orange)

> **智能桌面助手**：当您离开电脑时自动隐藏桌面图标，单击即可恢复，让桌面始终保持整洁。

---

## 📌 项目简介

**桌面隐身工具** 是一款运行于 Windows 系统的实用小工具。它能自动监测系统的空闲状态，在您设定的时间无任何鼠标/键盘操作后，自动隐藏所有桌面图标；当您再次点击桌面时，图标会立即恢复显示。此外，它还提供了白名单、定时计划、全局热键、日志查看、配置备份等丰富功能，是办公、演示、专注工作场景下的得力助手。

---

## ✨ 主要功能

### 核心功能
- **空闲检测与自动隐藏**：连续空闲（3~60 秒可调）后自动隐藏桌面图标
- **单击唤醒**：任意位置点击桌面即可恢复图标显示
- **系统托盘驻留**：后台静默运行，右键菜单可快速退出或打开设置

### 智能控制
- **白名单模式**：指定应用（如游戏、播放器、PPT）在前台时，暂停自动隐藏
- **全局热键**：默认 `Ctrl+Win+H`，一键切换图标显示/隐藏
- **定时计划**：设置每天的工作时段（如 9:00-18:00），仅在此时段内启用监控

### 用户体验
- **动态托盘图标**：不同颜色（绿/红/黄）直观显示当前状态（监控中/已隐藏/暂停）
- **内置日志查看器**：实时查看运行日志，方便排查问题
- **配置备份与恢复**：一键导出所有设置（空闲时间、白名单、热键等），随时导入还原

### 健壮性与可靠性
- **自动修复**：当系统桌面进程（explorer.exe）意外重启后，自动重获句柄并恢复监控
- **优雅退出**：退出程序前自动恢复桌面图标，避免“遗忘”隐藏状态
- **多显示器支持**：同时管理所有屏幕上的桌面图标

### 更新与分发
- **自动检查更新**：启动 2 秒后静默检测新版本，有更新时弹窗提示
- **手动检查更新**：通过界面按钮或托盘菜单随时检查
- **服务端版本管理**：基于 PHP 的简易 API，便于发布新版本

---

## 🛠️ 技术栈

| 组件 | 技术选型 |
|------|----------|
| 开发语言 | Python 3.8+ |
| GUI 框架 | Tkinter + ttk |
| 系统 API | Windows API (ctypes) |
| 全局钩子 | pynput (键盘/鼠标) |
| 热键注册 | keyboard |
| 托盘图标 | pystray + Pillow |
| 配置存储 | JSON 文件 |
| 日志记录 | logging 模块 |
| 更新服务端 | PHP (原生) |
| 打包工具 | PyInstaller (单文件 EXE) |

---

## 📦 安装与使用

### 方式一：直接运行 EXE（推荐）
1. 从 [Releases](https://github.com/yourusername/desktop-invisible-tool/releases) 下载最新 `DesktopTool.exe`
2. 双击运行，程序即出现在系统托盘中
3. 右键托盘图标可进行设置或退出

### 方式二：源码运行（需 Python 环境）
```bash
git clone https://github.com/yourusername/desktop-invisible-tool.git
cd desktop-invisible-tool
pip install -r requirements.txt
python main.pyw
```

> **提示**：若需开机自启，请在程序界面勾选“开机自动启动”（会写入当前用户的注册表 `Run` 项）。

---

## ⚙️ 配置说明

配置文件 `config.json` 位于程序同目录下，首次运行会自动生成。示例内容：

```json
{
  "idle_seconds": 5,
  "whitelist": ["notepad.exe", "chrome.exe", "powerpnt.exe"],
  "hotkey": "ctrl+win+h",
  "schedule_enabled": true,
  "schedule_start": "09:00",
  "schedule_end": "18:00"
}
```

- **idle_seconds**：空闲秒数，范围 3~60
- **whitelist**：进程名列表，匹配时暂停监控
- **hotkey**：全局热键组合
- **schedule_enabled**：是否启用定时计划
- **schedule_start / end**：每日有效时间范围（格式 `HH:MM`）

> 修改配置后，程序会自动加载生效（无需重启）。

---

## 📸 截图预览

（此处可放置界面截图，例如主窗口、日志查看器、托盘菜单等）

---

## 📊 版本历史

| 版本 | 亮点更新 |
|------|----------|
| **v1.0.0** | 核心空闲检测 + 单击唤醒 |
| **v1.1.0** | 系统托盘 + 开机自启 |
| **v1.2.0** | 自动检查更新 + 版本管理 |
| **v1.2.2** | 退出前恢复桌面（防止图标遗留） |
| **v1.3.0** | 自定义空闲时间 + 白名单 + 热键 + 动态图标 + 自动修复 |
| **v1.4.0** | 日志查看器 + 配置备份/恢复 + 定时计划 + 多显示器支持 |
| **v1.5.0** | 自动下载更新 + 统计面板 + Windows 11 适配 + 多语言 (计划中) |

---

## 🤝 贡献指南

欢迎任何形式的贡献！请遵循以下步骤：

1. Fork 本仓库
2. 创建您的特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交您的更改 (`git commit -m 'Add some amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 打开一个 Pull Request

请确保代码风格符合 PEP 8，并添加必要的注释。

---

## 📄 许可证

本项目采用 MIT 许可证，详情见 [LICENSE](LICENSE) 文件。免费用于个人及商业用途。

---

## 📬 联系与反馈

- **GitHub Issues**：[提交问题或建议](https://github.com/yourusername/desktop-invisible-tool/issues)
- **邮箱**：your-email@example.com

---

## ⭐ 支持我们

如果这个工具对您有帮助，欢迎点个 Star ✨，让更多人发现它！

---

**Made with ❤️ for a cleaner desktop experience.**
```

---

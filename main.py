# -*- coding: utf-8 -*-
"""
=====================================================================
桌面图标空闲隐藏工具 v11（1.4.0：日志查看/配置备份/定时计划/多显示器）
=====================================================================
运行环境：Windows 10 / Windows 11
权限要求：默认【不需要】管理员权限即可运行；
          若 pynput 全局钩子在某些前台应用下失效（无法捕获输入），
          请右键脚本 "以管理员身份运行"。
开机自启：通过修改 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 注册表实现，
          通常无需管理员权限；若失败请以管理员身份运行。
启动方式（任选其一）：
    1. 调试：python main.py        （带控制台，print 日志可见）
    2. 无控制台：双击 main.pyw     （pythonw.exe 启动，仅 GUI + 托盘）
    3. 打包 exe：见 build.bat，产物 dist\\DesktopHider1.4.0.exe 双击运行
v11 新增（1.4.0 四项扩展功能）：
    · [FEATURE-6] 内置日志查看器：Toplevel 窗口 + ScrolledText 实时显示 + 复制/刷新。
    · [FEATURE-7] 配置备份与恢复：导出/导入 JSON，含完整性校验与 GUI 即时刷新。
    · [FEATURE-8] 定时计划：每天固定时间段自动启用/暂停监控，独立线程每分钟检查。
    · [FEATURE-9] 多显示器支持：遍历所有 Progman/WorkerW 下的 SysListView32 句柄。
    · 配置文件 config.json 新增 schedule_enabled/schedule_start/schedule_end 字段。
    · 版本号升级至 1.4.0，同步 PHP API 与 build.bat。
v10 新增（1.3.0 五大功能增强）：
    · [FEATURE-1] 自定义空闲时间：滑动条 3~3600s，持久化到 config.json，修改即生效。
    · [FEATURE-2] 白名单模式：前台进程在白名单时暂停隐藏，支持手动添加。
    · [FEATURE-3] 快捷键唤醒：Ctrl+Win+H 全局热键手动切换显示/隐藏，不干扰空闲计时。
    · [FEATURE-4] 动态托盘图标：监控中(绿)/已隐藏(红)/暂停(灰)三色状态图标。
    · [FEATURE-5] 错误自动修复：worker 健康检查，HIDDEN 态异常时自动强制恢复。
打包命令（详见 build.bat）：
    pyinstaller -F -w --name=DesktopHider1.4.0 --icon=app.ico --add-data "app.ico;." main.py
历史：
    · v10：自定义空闲/白名单/热键/动态图标/自动修复 + 版本 1.3.0。
    · v9：安全退出 + 版本 1.2.2。
    · v8：自动检查更新 + PHP API。
    · v7：PyInstaller 打包就绪 + resource_path 资源路径兼容。
    · v6：开机自启注册表开关。
    · v5：无控制台 + 文件日志。
    · v4：动态句柄 + 强制复位防卡死。
    · v3：枚举状态机 + 回调 try-except + 监听器监管自愈。
    · v2：tkinter 控制面板 + 托盘菜单。
=====================================================================
"""

import ctypes
import logging                                             # [LOG] 日志模块
import os
import sys
import threading
import time
import tkinter as tk                                       # [GUI]
from tkinter import ttk, messagebox                        # [GUI]
from tkinter import scrolledtext                           # [FEATURE-6] 日志查看器滚动文本框
from tkinter import filedialog                             # [FEATURE-7] 配置备份/恢复文件对话框
from enum import Enum                                      # [STATE]
import winreg                                              # [STARTUP] 注册表操作
import urllib.request                                      # [UPDATE] HTTP 请求
import json                                                # [UPDATE][FEATURE-1] JSON 解析 + 配置读写
import webbrowser                                          # [UPDATE] 打开下载链接
import subprocess                                          # [FEATURE-2] 查询前台窗口进程名
import datetime                                            # [FEATURE-8] 定时计划时间判断
import collections                                         # [FEATURE-6] 环形缓冲区
from queue import Queue, Empty                             # [FEATURE-6] 日志队列线程通信

from pynput import mouse, keyboard as pynput_kb
import pystray
from PIL import Image, ImageDraw

# [FEATURE-3] keyboard 库用于全局热键；导入失败时降级（仅禁用热键功能）
try:
    import keyboard as kb_lib                              # [FEATURE-3]
    _KB_LIB_OK = True
except Exception as _kb_err:                               # [FEATURE-3]
    kb_lib = None
    _KB_LIB_OK = False


# =====================================================================
# [LOG] 日志配置：同时写入文件，并兼容 pythonw（无控制台）环境。
#   - pythonw.exe 下 sys.stdout 为 None，print 会被静默丢弃；
#   - 用 _msg() 同时尝试 print + 写日志文件，便于无控制台时排查问题。
#   - [FEATURE-6] 额外挂载 MemoryLogHandler，将日志推入队列供 GUI 实时查看。
# =====================================================================
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "desktop_hider.log")
logging.basicConfig(
    filename=_LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("desktop_hider")


# =====================================================================
# [FEATURE-6] 内存环形缓冲日志处理器
#   · 同时维护一个 deque（最多 1000 行）和一个 Queue（供 GUI 实时拉取）
#   · 挂载到 root logger，所有模块的 logging 输出都会被捕获
#   · GUI 主线程定时（1s）从 Queue 取出新增日志追加到文本框
# =====================================================================
_LOG_RING_BUFFER = collections.deque(maxlen=1000)            # [FEATURE-6] 环形缓冲区
_LOG_QUEUE = Queue()                                        # [FEATURE-6] 实时推送队列


class MemoryLogHandler(logging.Handler):                    # [FEATURE-6]
    """将日志记录同时存入环形缓冲区与队列，供 GUI 日志查看器消费。"""
    def emit(self, record):                                 # [FEATURE-6]
        try:
            line = self.format(record) if self.formatter else record.getMessage()
            _LOG_RING_BUFFER.append(line)                   # [FEATURE-6] 环形缓冲（自动丢弃旧条目）
            _LOG_QUEUE.put_nowait(line)                     # [FEATURE-6] 推入队列供 GUI 拉取
        except Exception:
            pass  # 日志处理器自身异常绝不能影响主流程


# 挂载内存日志处理器（格式与文件日志一致）
_mem_handler = MemoryLogHandler()                            # [FEATURE-6]
_mem_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"))
_mem_handler.setLevel(logging.INFO)                         # [FEATURE-6] 与文件日志同级别
log.addHandler(_mem_handler)                                # [FEATURE-6] 挂载到本 logger


def _msg(level, msg):                                      # [LOG]
    """统一输出：尝试 print（有控制台则可见）+ 写日志文件（pythonw 下保留）。"""
    try:
        print(f"[{level}] {msg}")
    except Exception:
        pass  # pythonw 下 stdout 为 None，忽略
    lvl = getattr(logging, level, logging.INFO)
    log.log(lvl, msg)


def _fatal_msgbox(title, text):                            # [LOG]
    """pythonw 下无控制台，致命错误用 Win32 消息框提示用户。"""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


# =====================================================================
# 可配置参数
# =====================================================================
IDLE_SECONDS = 5  # 默认空闲秒数，可被 GUI 滑动条动态调整
MIN_IDLE = 3                                              # [GUI] 滑动条下限
MAX_IDLE = 3600                                           # [GUI] 滑动条上限（支持最长 1 小时空闲计时）
HANDLE_RETRIES = 3        # [HANDLE] 句柄获取失败时的重试次数
HANDLE_RETRY_INTERVAL = 0.2  # [HANDLE] 每次重试间隔（秒）

# [FEATURE-1][FEATURE-2] 配置文件路径（与脚本/exe 同目录）
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config.json")                  # [FEATURE-1]
_DEFAULT_CONFIG = {                                        # [FEATURE-1] 默认配置
    "idle_seconds": IDLE_SECONDS,                           # [FEATURE-1]
    "whitelist": [],                                        # [FEATURE-2] 进程名列表
    "hotkey": "ctrl+win+h",                                 # [FEATURE-3] 全局热键
    "schedule_enabled": False,                              # [FEATURE-8] 定时计划开关
    "schedule_start": "09:00",                              # [FEATURE-8] 计划启用开始时间
    "schedule_end": "18:00",                                # [FEATURE-8] 计划启用结束时间
}
# 全局配置缓存（启动时加载，GUI 修改后同步并回写文件）
_config_cache = dict(_DEFAULT_CONFIG)                       # [FEATURE-1]
_config_lock = threading.Lock()                             # [FEATURE-1]

# [UPDATE] 版本与更新检查配置
VERSION = "1.4.0"                                         # [VERSION][UPDATE] 客户端当前版本号
UPDATE_API_URL = "http://347735.xyz/check_update.php"  # [UPDATE] 更新检查 API 地址（部署后改为实际 URL）
UPDATE_CHECK_DELAY = 2000                                 # [UPDATE] 启动后延迟检查的毫秒数
UPDATE_TIMEOUT = 5                                        # [UPDATE] HTTP 请求超时秒数
# =====================================================================


# =====================================================================
# [FEATURE-1][FEATURE-2] 配置文件读写
#   · 启动时 load_config() 加载 config.json，文件不存在/损坏用默认值。
#   · GUI 修改后 save_config() 回写文件。
#   · 所有读写均 try-except，损坏/权限错误时回退默认值，绝不崩溃。
# =====================================================================
def load_config():                                          # [FEATURE-1]
    """从 config.json 加载配置，失败时使用默认值。返回配置 dict。"""
    global _config_cache
    try:
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 合并默认值（缺字段补默认，避免 KeyError）
            merged = dict(_DEFAULT_CONFIG)
            merged.update(data)
            # 校验 idle_seconds 范围
            try:
                v = int(merged.get("idle_seconds", IDLE_SECONDS))
                merged["idle_seconds"] = max(MIN_IDLE, min(MAX_IDLE, v))
            except (TypeError, ValueError):
                merged["idle_seconds"] = IDLE_SECONDS
            # 校验 whitelist 为 list
            if not isinstance(merged.get("whitelist"), list):
                merged["whitelist"] = []
            merged["whitelist"] = [str(x).lower() for x in merged["whitelist"]]  # [FEATURE-2] 统一小写
            # [FEATURE-8] 校验定时计划字段
            merged["schedule_enabled"] = bool(merged.get("schedule_enabled", False))  # [FEATURE-8]
            merged["schedule_start"] = _validate_time_str(   # [FEATURE-8] 校验 HH:MM 格式
                merged.get("schedule_start", "09:00"), "09:00")
            merged["schedule_end"] = _validate_time_str(     # [FEATURE-8]
                merged.get("schedule_end", "18:00"), "18:00")
            with _config_lock:
                _config_cache = merged
            _msg("INFO", f"[配置] 已加载 config.json：{merged}")
            return merged
    except (json.JSONDecodeError, OSError) as e:            # [FEATURE-1] 文件损坏/权限错误
        _msg("WARNING", f"[配置] 加载失败，使用默认值：{type(e).__name__}: {e}")
    except Exception as e:
        _msg("WARNING", f"[配置] 加载异常，使用默认值：{type(e).__name__}: {e}")
    with _config_lock:
        _config_cache = dict(_DEFAULT_CONFIG)
    return _config_cache


def save_config():                                          # [FEATURE-1]
    """将当前配置缓存回写到 config.json。失败仅记日志，不抛异常。"""
    try:
        with _config_lock:
            data = dict(_config_cache)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _msg("INFO", f"[配置] 已保存 config.json：{data}")
    except OSError as e:                                    # [FEATURE-1] 权限错误
        _msg("ERROR", f"[配置] 保存失败：{type(e).__name__}: {e}")
    except Exception as e:
        _msg("ERROR", f"[配置] 保存异常：{type(e).__name__}: {e}")


def get_config(key, default=None):                          # [FEATURE-1]
    """线程安全读取配置项。"""
    with _config_lock:
        return _config_cache.get(key, default)


def set_config(key, value):                                 # [FEATURE-1]
    """线程安全写入配置项（仅内存，不回写文件；需调用 save_config 持久化）。"""
    with _config_lock:
        _config_cache[key] = value


# =====================================================================
# [FEATURE-8] 定时计划辅助函数
#   · _validate_time_str: 校验 "HH:MM" 格式，非法返回 default
#   · _is_in_schedule_window: 判断当前时间是否在计划启用时段内
#   · _schedule_paused: 标记当前是否因非计划时段而暂停（worker 读取）
# =====================================================================
def _validate_time_str(s, default):                          # [FEATURE-8]
    """校验时间字符串格式为 HH:MM，合法返回原值，非法返回 default。"""
    try:
        text = str(s).strip()
        parts = text.split(":")
        if len(parts) != 2:
            return default
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"                        # [FEATURE-8] 规范化输出
        return default
    except (TypeError, ValueError):
        return default


def _is_in_schedule_window():                                # [FEATURE-8]
    """
    判断当前时间是否在计划启用时段内。
    · 计划未启用 → 返回 True（不阻塞监控）
    · start == end → 视为全天启用，返回 True
    · 跨日情况（如 22:00 → 06:00）也正确处理
    """
    try:
        if not get_config("schedule_enabled", False):        # [FEATURE-8] 计划未启用，放行
            return True
        start_str = get_config("schedule_start", "09:00")    # [FEATURE-8]
        end_str = get_config("schedule_end", "18:00")        # [FEATURE-8]
        start_h, start_m = [int(x) for x in start_str.split(":")]
        end_h, end_m = [int(x) for x in end_str.split(":")]
        # 转换为当天分钟数便于比较
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        now = datetime.datetime.now()                        # [FEATURE-8] 当前本地时间
        now_min = now.hour * 60 + now.minute
        if start_min == end_min:                             # 全天
            return True
        if start_min < end_min:                              # 同日时段
            return start_min <= now_min < end_min
        else:                                                # 跨日时段（如 22:00 → 06:00）
            return now_min >= start_min or now_min < end_min
    except Exception as e:
        _msg("ERROR", f"[定时计划] 时间判断异常: {type(e).__name__}: {e}")
        return True  # 异常时放行，避免误锁


# [FEATURE-8] 定时计划暂停标志（worker 读取，定时检查线程写入）
_schedule_paused = False                                     # [FEATURE-8]
_schedule_lock = threading.Lock()                            # [FEATURE-8] 独立锁，避免依赖 _state_lock 顺序


def is_schedule_paused():                                    # [FEATURE-8]
    """返回当前是否因非计划时段而暂停监控。"""
    with _schedule_lock:
        return _schedule_paused


def set_schedule_paused(v):                                  # [FEATURE-8]
    global _schedule_paused
    with _schedule_lock:
        old = _schedule_paused
        _schedule_paused = v
    if old != v:                                             # [FEATURE-8] 状态变化记日志
        _msg("INFO", f"[定时计划] 暂停状态：{old} → {v}")


def get_whitelist():                                        # [FEATURE-2]
    """获取白名单进程名列表（小写）。"""
    with _config_lock:
        return list(_config_cache.get("whitelist", []))


def add_to_whitelist(proc_name):                            # [FEATURE-2]
    """添加进程名到白名单（去重，忽略大小写）。返回是否新增。"""
    name = str(proc_name).strip().lower()
    if not name:
        return False
    with _config_lock:
        wl = _config_cache.get("whitelist", [])
        if name in wl:
            return False
        wl.append(name)
        _config_cache["whitelist"] = wl
    save_config()                                           # [FEATURE-2] 立即回写
    _msg("INFO", f"[白名单] 已添加：{name}")
    return True


def remove_from_whitelist(proc_name):                      # [FEATURE-2]
    """从白名单移除进程名。返回是否移除。"""
    name = str(proc_name).strip().lower()
    with _config_lock:
        wl = _config_cache.get("whitelist", [])
        if name not in wl:
            return False
        wl.remove(name)
        _config_cache["whitelist"] = wl
    save_config()                                           # [FEATURE-2] 立即回写
    _msg("INFO", f"[白名单] 已移除：{name}")
    return True


# [FEATURE-2] 前台窗口进程名获取（ctypes + GetForegroundWindow + GetWindowThreadProcessId）
def get_foreground_process_name():                          # [FEATURE-2]
    """获取当前前台窗口的进程名（小写，失败返回空字符串）。"""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_uint()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # 用 PowerShell/tasklist 替代 psutil，避免额外依赖
        # 这里用 ctypes 调 QueryFullProcessImageName 获取可执行文件路径
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(1024)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(h)
        if not ok:
            return ""
        # 取文件名并转小写
        return os.path.basename(buf.value).lower()
    except Exception as e:
        _msg("ERROR", f"[白名单] 获取前台进程名失败：{type(e).__name__}: {e}")
        return ""


# [FEATURE-3] 热键切换请求标志（回调置位，worker 消费）
_hotkey_toggle_requested = False                            # [FEATURE-3]


def request_hotkey_toggle():                                # [FEATURE-3]
    global _hotkey_toggle_requested
    with _state_lock:
        _hotkey_toggle_requested = True


def consume_hotkey_toggle():                                # [FEATURE-3]
    global _hotkey_toggle_requested
    with _state_lock:
        if _hotkey_toggle_requested:
            _hotkey_toggle_requested = False
            return True
        return False


# [FEATURE-2] 白名单活跃标志（回调/worker 共同维护）
_whitelist_active = False                                   # [FEATURE-2]


def is_whitelist_active():                                  # [FEATURE-2]
    with _state_lock:
        return _whitelist_active


def set_whitelist_active(v):                                # [FEATURE-2]
    global _whitelist_active
    with _state_lock:
        _whitelist_active = v


# Windows API 常量
SW_HIDE = 0
SW_SHOW = 5

# ---------------------------------------------------------------------
# Windows API 函数声明（明确参数与返回类型，避免 64 位下指针被截断）
# ---------------------------------------------------------------------
user32 = ctypes.windll.user32

user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]

user32.FindWindowExW.restype = ctypes.c_void_p
user32.FindWindowExW.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_wchar_p, ctypes.c_wchar_p,
]

user32.ShowWindow.restype = ctypes.c_bool
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]

user32.SendMessageTimeoutW.restype = ctypes.c_void_p
user32.SendMessageTimeoutW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p),
]

# [FEATURE-2] 前台窗口相关 API
user32.GetForegroundWindow.restype = ctypes.c_void_p          # [FEATURE-2]
user32.GetForegroundWindow.argtypes = []
user32.GetWindowThreadProcessId.restype = ctypes.c_uint       # [FEATURE-2]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_uint)]

# WindowsError 在 Python3 中等价于 OSError（Windows 平台仍可作别名引用）
WindowsError = OSError  # [HANDLE] 兼容引用，便于显式捕获


# =====================================================================
# [STATE] 枚举状态机
#   ACTIVE : 监控中，桌面图标可见，用户近期有操作
#   IDLE   : 空闲计时中，桌面图标仍可见，用户已停止操作但未达阈值
#   HIDDEN : 空闲达阈值，桌面图标已隐藏，等待鼠标左键单击唤醒
# =====================================================================
class State(Enum):                                         # [STATE]
    ACTIVE = "ACTIVE"                                      # [STATE]
    IDLE = "IDLE"                                          # [STATE]
    HIDDEN = "HIDDEN"                                      # [STATE]


# =====================================================================
# 全局共享状态（多线程读写，需加锁）
# =====================================================================
_state_lock = threading.Lock()
_last_activity_time = time.time()   # 最近一次鼠标 / 键盘活动时间
_idle_seconds = IDLE_SECONDS        # 当前空闲阈值（可被 GUI 修改）
_running = True                     # 程序是否继续运行

_state = State.ACTIVE                # [STATE] 核心状态变量

# 控制标志位
flag_enabled = True                  # 总控开关（False 时暂停整个循环）
flag_handle_lost = False             # 桌面句柄是否丢失（用于 GUI 提示）
flag_show_panel_requested = False    # 请求显示控制面板（来自托盘）
flag_quit_requested = False          # 请求退出（来自托盘）
flag_retry_requested = False         # 请求重试获取句柄（来自 GUI 按钮）
_show_requested = False              # 请求恢复显示桌面图标（来自单击 / 暂停切换）

_tray_icon = None                    # 托盘图标引用
_mouse_listener = None               # [LISTENER] 监听器引用
_kb_listener = None                  # [LISTENER] 监听器引用
_panel_ref = None                    # [EXIT-FIX] ControlPanel 实例引用，供退出时获取 root


# ---------------------------------------------------------------------
# 基础读写
# ---------------------------------------------------------------------
def update_activity():
    global _last_activity_time
    with _state_lock:
        _last_activity_time = time.time()


def get_idle_duration():
    with _state_lock:
        return time.time() - _last_activity_time


def reset_activity_time():                                  # [STATE]
    """重置活动时间戳（启用监控 / 单击唤醒后调用，闭合循环的关键）。"""
    global _last_activity_time
    with _state_lock:
        _last_activity_time = time.time()


def get_idle_seconds():
    with _state_lock:
        return _idle_seconds


def set_idle_seconds(v):
    global _idle_seconds
    with _state_lock:
        _idle_seconds = v


def is_running():
    with _state_lock:
        return _running


def stop_running():
    global _running
    with _state_lock:
        _running = False


def get_state():                                            # [STATE]
    with _state_lock:
        return _state


def set_state(new_state):                                   # [STATE]
    """
    直接写入状态（供 hide_desktop / show_desktop / on_click 等权威函数使用）。
    ============================================================
    【关键】本函数是状态机唯一入口，"强制复位状态以确保循环继续"。
    on_mouse_click 在唤醒时调用 set_state(ACTIVE) 立即跳出 HIDDEN，
    即使后续 worker 的 ShowWindow 失败，状态也已恢复，UI 轮询能立即看到
    ACTIVE 并刷新标签，杜绝"点击后界面卡在 已隐藏"的问题。
    UI 同步依赖 control_worker / update_status 的周期轮询（250ms + 1s 兜底），
    无需在此处直接操作 tkinter（避免跨线程 UI 调用）。
    ============================================================
    """
    global _state
    with _state_lock:
        old = _state
        _state = new_state
    if old != new_state:
        _msg("INFO", f"[状态] {old.name} → {new_state.name}")  # [STATE] 转换日志


def is_enabled():
    with _state_lock:
        return flag_enabled


def set_enabled(v):
    global flag_enabled
    with _state_lock:
        flag_enabled = v


def is_handle_lost():
    with _state_lock:
        return flag_handle_lost


def set_handle_lost(v):
    global flag_handle_lost
    with _state_lock:
        flag_handle_lost = v


def request_show():
    global _show_requested
    with _state_lock:
        _show_requested = True


def consume_show_request():
    global _show_requested
    with _state_lock:
        if _show_requested:
            _show_requested = False
            return True
        return False


def request_show_panel():
    global flag_show_panel_requested
    with _state_lock:
        flag_show_panel_requested = True


def consume_show_panel_request():
    global flag_show_panel_requested
    with _state_lock:
        if flag_show_panel_requested:
            flag_show_panel_requested = False
            return True
        return False


def request_quit():
    global flag_quit_requested
    with _state_lock:
        flag_quit_requested = True


def consume_quit_request():
    global flag_quit_requested
    with _state_lock:
        if flag_quit_requested:
            flag_quit_requested = False
            return True
        return False


def request_retry():
    global flag_retry_requested
    with _state_lock:
        flag_retry_requested = True


def consume_retry_request():
    global flag_retry_requested
    with _state_lock:
        if flag_retry_requested:
            flag_retry_requested = False
            return True
        return False


# =====================================================================
# [HANDLE] 动态句柄获取（核心修复 ①：即时获取 + 重试机制）
#   废除任何全局缓存的 hwnd。每次需要操作桌面时都重新 FindWindow，
#   以应对 explorer.exe 重启 / 桌面视图刷新导致旧句柄失效。
# =====================================================================
def _search_defview_once():
    """单次查找 SHELLDLL_DefView，含 Progman 与顶层 WorkerW 两路 + 0x052C 兜底。"""
    # 路径 A：Progman 直接包含 SHELLDLL_DefView
    progman = user32.FindWindowW("Progman", "Program Manager")
    if progman:
        dv = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if dv:
            return dv

    # 路径 B：遍历所有【顶层】WorkerW（Win10/11 下常见结构）
    worker = user32.FindWindowExW(None, None, "WorkerW", None)
    while worker:
        dv = user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None)
        if dv:
            return dv
        worker = user32.FindWindowExW(None, worker, "WorkerW", None)

    # 兜底：向 Progman 发送 0x052C 强制重建 WorkerW 桌面层结构后重查一次
    if progman:
        try:
            result = ctypes.c_void_p()
            user32.SendMessageTimeoutW(progman, 0x052C, None, None,
                                       0x0002, 1000, ctypes.byref(result))
            time.sleep(0.15)
        except Exception:
            pass
        # 重建后再走一遍两路查找
        dv = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if dv:
            return dv
        worker = user32.FindWindowExW(None, None, "WorkerW", None)
        while worker:
            dv = user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None)
            if dv:
                return dv
            worker = user32.FindWindowExW(None, worker, "WorkerW", None)
    return None


def get_desktop_handle(retries=HANDLE_RETRIES):             # [HANDLE] 核心修复 ①
    """
    即时获取桌面 SysListView32 句柄（不缓存）。
    每次调用都重新执行 FindWindow("Progman","Program Manager")
    → FindWindowEx(...,"SHELLDLL_DefView") → FindWindowEx(...,"SysListView32")。
    失败时等待 0.2 秒后重试，最多重试 retries 次。全部失败返回 None。
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            def_view = _search_defview_once()
            if def_view:
                listview = user32.FindWindowExW(def_view, None, "SysListView32", None)
                if listview:
                    set_handle_lost(False)                  # 成功，清除丢失标志
                    return listview
            last_err = "未定位到 SHELLDLL_DefView / SysListView32"
        except (WindowsError, AttributeError) as e:        # [HANDLE] 显式捕获
            last_err = f"{type(e).__name__}: {e}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        if attempt < retries:
            time.sleep(HANDLE_RETRY_INTERVAL)              # [HANDLE] 0.2s 后重试

    # 全部重试失败
    set_handle_lost(True)
    _msg("ERROR", f"[HANDLE] 获取桌面句柄失败（重试 {retries} 次）：{last_err}")
    return None


# =====================================================================
# [FEATURE-9] 多显示器支持
#   · get_all_desktop_handles(): 遍历所有 Progman / 顶层 WorkerW 下的
#     SHELLDLL_DefView → SysListView32，返回所有桌面图标列表视图句柄。
#   · 实测 Windows 通常只存在一个 SysListView32（主显示器），扩展显示器
#     的图标也由该控件统一管理；但本函数仍做完整遍历，以兼容多桌面视图。
#   · 隐藏/显示时遍历所有句柄操作，单个失败不阻断其他句柄。
# =====================================================================
def _search_all_defviews():                                  # [FEATURE-9]
    """
    查找所有 SHELLDLL_DefView 句柄（去重）。
    遍历 Progman 子窗口 + 所有顶层 WorkerW 子窗口。
    """
    found = []                                               # [FEATURE-9] 收集所有 DefView
    seen = set()                                             # [FEATURE-9] 去重集合

    # 路径 A：Progman 下的 SHELLDLL_DefView
    progman = user32.FindWindowW("Progman", "Program Manager")
    if progman:
        dv = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if dv and dv not in seen:
            seen.add(dv)
            found.append(dv)

    # 路径 B：所有顶层 WorkerW 下的 SHELLDLL_DefView
    worker = user32.FindWindowExW(None, None, "WorkerW", None)
    while worker:
        dv = user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None)
        if dv and dv not in seen:                           # [FEATURE-9] 去重
            seen.add(dv)
            found.append(dv)
        worker = user32.FindWindowExW(None, worker, "WorkerW", None)

    return found, progman                                    # [FEATURE-9] 返回 progman 用于兜底


def get_all_desktop_handles(retries=HANDLE_RETRIES):         # [FEATURE-9]
    """
    获取所有桌面 SysListView32 句柄列表（多显示器支持）。
    每次重新遍历，不缓存。失败时重试。
    :return: 非空 list[句柄]；全部失败返回空 list。
    """
    for attempt in range(1, retries + 1):
        try:
            defviews, progman = _search_all_defviews()      # [FEATURE-9]
            # 兜底：若两路都没找到，向 Progman 发 0x052C 重建后再查一次
            if not defviews and progman:
                try:
                    result = ctypes.c_void_p()
                    user32.SendMessageTimeoutW(progman, 0x052C, None, None,
                                               0x0002, 1000, ctypes.byref(result))
                    time.sleep(0.15)                        # [FEATURE-9] 等待桌面层重建
                except Exception:
                    pass
                defviews, progman = _search_all_defviews()  # [FEATURE-9] 重建后重查

            handles = []                                    # [FEATURE-9] 收集所有 SysListView32
            for dv in defviews:
                lv = user32.FindWindowExW(dv, None, "SysListView32", None)
                if lv:
                    handles.append(lv)
            if handles:
                set_handle_lost(False)                      # [FEATURE-9] 至少一个成功
                # 记录检测到的句柄数（用于多显示器调试）
                if len(handles) > 1:
                    _msg("INFO", f"[多显示器] 检测到 {len(handles)} 个桌面图标视图句柄")  # [FEATURE-9]
                return handles
        except (WindowsError, AttributeError) as e:
            _msg("ERROR", f"[多显示器] 第 {attempt} 次遍历异常: {type(e).__name__}: {e}")
        except Exception as e:
            _msg("ERROR", f"[多显示器] 第 {attempt} 次遍历异常: {type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(HANDLE_RETRY_INTERVAL)               # [FEATURE-9] 重试间隔

    set_handle_lost(True)
    _msg("ERROR", f"[多显示器] 遍历所有句柄失败（重试 {retries} 次）")  # [FEATURE-9]
    return []


# =====================================================================
# [STATE] 隐藏 / 显示桌面（核心修复 ②③：动态获取 + 强制复位防卡死）
#   注意：以下函数只能在 control_worker 工作线程中调用（含 Win32 阻塞调用），
#         严禁在 pynput 回调或 tkinter 主线程中调用，否则 GUI 会卡死。
# =====================================================================
def hide_desktop():                                         # [STATE] 核心修复 ③
    """
    ACTIVE → HIDDEN。
    · [FEATURE-9] 调用 get_all_desktop_handles() 获取所有显示器的桌面句柄。
    · 遍历所有句柄执行 ShowWindow(SW_HIDE)，至少一个成功即视为隐藏成功。
    · 全部失败：保持 ACTIVE，等待下一周期重试。
    """
    handles = get_all_desktop_handles()                     # [FEATURE-9] 多显示器遍历
    if not handles:
        _msg("ERROR", "hide_desktop：句柄获取失败，保持 ACTIVE，等待下周期重试")
        return False
    success_count = 0                                       # [FEATURE-9] 成功计数
    for hwnd in handles:                                    # [FEATURE-9] 遍历所有句柄
        try:
            ok = user32.ShowWindow(hwnd, SW_HIDE)
            if ok:
                success_count += 1
        except (WindowsError, AttributeError) as e:         # [STATE] 单个句柄失败不阻断
            _msg("ERROR", f"hide_desktop ShowWindow 异常（句柄 {hwnd}）：{type(e).__name__}: {e}")
        except Exception as e:
            _msg("ERROR", f"hide_desktop ShowWindow 异常（句柄 {hwnd}）：{type(e).__name__}: {e}")

    if success_count == 0:                                  # [FEATURE-9] 全部失败
        _msg("ERROR", f"hide_desktop：{len(handles)} 个句柄全部 ShowWindow 失败，保持 ACTIVE")
        return False

    # 至少一个成功：切换到 HIDDEN
    if success_count < len(handles):                        # [FEATURE-9] 部分成功记警告
        _msg("WARNING", f"hide_desktop：{success_count}/{len(handles)} 个句柄隐藏成功")
    set_state(State.HIDDEN)                                 # [STATE] ACTIVE → HIDDEN
    return True


def show_desktop():                                         # [STATE] 核心修复 ②
    """
    HIDDEN → ACTIVE。
    · [FEATURE-9] 调用 get_all_desktop_handles() 获取所有显示器句柄。
    · 遍历所有句柄执行 ShowWindow(SW_SHOW)，至少一个成功即视为显示成功。
    · 若全部失败：【绝不能保持 HIDDEN！】强制将状态设为 ACTIVE，
      并打印警告。此处强制复位状态是为了防止卡死——用户已点击桌面意图恢复，
      即使 API 调用失败，状态机也必须回到 ACTIVE，这样计时器才能重新启动，
      下一次空闲周期会用新句柄再次尝试隐藏桌面。
    """
    handles = get_all_desktop_handles()                     # [FEATURE-9] 多显示器遍历
    success_count = 0                                       # [FEATURE-9] 成功计数
    if handles:
        for hwnd in handles:                                # [FEATURE-9] 遍历所有句柄
            try:
                ok = user32.ShowWindow(hwnd, SW_SHOW)
                if ok:
                    success_count += 1
            except (WindowsError, AttributeError) as e:     # [STATE] 单个失败不阻断
                _msg("ERROR", f"show_desktop ShowWindow 异常（句柄 {hwnd}）：{type(e).__name__}: {e}")
            except Exception as e:
                _msg("ERROR", f"show_desktop ShowWindow 异常（句柄 {hwnd}）：{type(e).__name__}: {e}")

    if success_count > 0:                                   # [FEATURE-9] 至少一个成功
        if success_count < len(handles):
            _msg("WARNING", f"show_desktop：{success_count}/{len(handles)} 个句柄显示成功")
        set_state(State.ACTIVE)                             # [STATE] HIDDEN → ACTIVE
        return True

    # ============================================================
    # 【关键】此处强制复位状态是为了防止卡死。
    # 即使显示 API 失败，也必须回到 ACTIVE，否则状态机永久停在 HIDDEN，
    # 计时器无法重启，监控流程彻底中断。下一次空闲周期会用新句柄重试隐藏。
    # ============================================================
    _msg("WARNING", "显示桌面失败，但已强制重置状态以恢复监控")  # [STATE] 强制复位
    set_state(State.ACTIVE)                                 # [STATE] 强制 HIDDEN → ACTIVE
    return False


# =====================================================================
# [EXIT-FIX] 安全退出流程
#   · 退出前若桌面处于 HIDDEN 态，必须尝试恢复显示，避免图标被"遗忘"在隐藏状态。
#   · 恢复失败也记录日志后继续退出，绝不让程序卡死无法关闭。
#   · 供托盘退出 / main() finally / Ctrl+C 统一调用，保证一致性。
# =====================================================================
def ensure_desktop_visible_on_exit():                        # [EXIT-FIX]
    """
    退出前确保桌面图标恢复显示。
    1) 若当前状态为 HIDDEN，调用 show_desktop() 恢复。
    2) 短延时 0.2s 等待 ShowWindow 生效，并重试一次（句柄可能刚失效）。
    3) 无论恢复是否成功，均不抛异常，保证后续退出流程不被阻断。
    """
    try:
        if get_state() == State.HIDDEN:                      # [EXIT-FIX] 仅隐藏态才恢复
            _msg("INFO", "[退出] 检测到桌面处于隐藏状态，尝试恢复…")
            try:
                show_desktop()                               # [EXIT-FIX] 含动态句柄获取 + 强制复位
            except Exception as e:
                _msg("ERROR", f"[退出] 恢复显示异常: {type(e).__name__}: {e}")
            # 短延时等待 ShowWindow 生效，再确认一次状态
            time.sleep(0.2)                                  # [EXIT-FIX] 等待 GUI 刷新
            if get_state() == State.HIDDEN:                  # 仍隐藏则再试一次
                _msg("WARNING", "[退出] 首次恢复未生效，重试一次…")
                try:
                    show_desktop()
                except Exception as e:
                    _msg("ERROR", f"[退出] 重试恢复异常: {type(e).__name__}: {e}")
    except Exception as e:
        _msg("ERROR", f"[退出] ensure_desktop_visible_on_exit 异常: {type(e).__name__}: {e}")


def safe_shutdown(icon=None, root=None):                     # [EXIT-FIX]
    """
    统一安全退出入口：恢复桌面 → 停监听器 → 停托盘 → 注销热键 → 退出主循环。
    供 on_tray_quit / main() finally / Ctrl+C 调用，保证退出行为一致。
    """
    stop_running()                                           # [EXIT-FIX] 通知所有工作线程停止
    ensure_desktop_visible_on_exit()                         # [EXIT-FIX] 关键：先恢复桌面图标
    # [FEATURE-3] 注销全局热键（keyboard 库）
    if _KB_LIB_OK:                                           # [FEATURE-3]
        try:
            kb_lib.unhook_all()                              # [FEATURE-3] 清除所有热键注册
        except Exception as e:
            _msg("ERROR", f"[退出] 注销热键异常: {type(e).__name__}: {e}")
    # 停止 pynput 监听器
    for l in (_mouse_listener, _kb_listener):
        if l is not None:
            try:
                l.stop()
            except Exception as e:
                _msg("ERROR", f"[退出] 停止监听器异常: {type(e).__name__}: {e}")
    # 停止托盘图标
    if icon is not None:
        try:
            icon.stop()
        except Exception as e:
            _msg("ERROR", f"[退出] 停止托盘异常: {type(e).__name__}: {e}")
    # 退出 tkinter 主循环
    if root is not None:
        try:
            root.after(0, root.quit)                         # [EXIT-FIX] 主线程安全退出
        except Exception as e:
            _msg("ERROR", f"[退出] root.quit 异常: {type(e).__name__}: {e}")
    _msg("INFO", "[退出] 安全退出流程完成。")                 # [EXIT-FIX]


# =====================================================================
# [STARTUP] 开机自启注册表管理
#   注册表路径：HKCU\Software\Microsoft\Windows\CurrentVersion\Run
#   HKCU 写入通常无需管理员权限；失败多为权限问题或键被组策略禁用。
#   命令字符串需兼容三种启动形态：.py / .pyw / 打包后的 .exe。
# =====================================================================
APP_NAME = "DesktopTool"                                    # [STARTUP] 注册表唯一标识
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run" # [STARTUP] 注册表键路径


def _is_frozen():                                           # [STARTUP]
    """是否为 PyInstaller 打包后的 exe（frozen）。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


# [PACK] 资源路径解析：兼容开发态与 PyInstaller 打包态
#   开发态：返回脚本同目录的相对路径
#   打包态：返回 sys._MEIPASS 临时解压目录中的路径（--add-data 拷入的资源在此）
def resource_path(relative_path: str) -> str:               # [PACK]
    """
    获取资源文件的绝对路径，兼容 PyInstaller 打包后的运行环境。
    :param relative_path: 相对路径，如 "icon.png" 或 "assets/icon.png"
    :return: 资源文件的绝对路径（可能位于 sys._MEIPASS 临时目录）
    """
    try:
        # PyInstaller 打包后：_MEIPASS 指向解压临时目录
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return os.path.join(base, relative_path)
    except Exception:
        pass
    # 开发态：以脚本所在目录为基准
    try:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
    except Exception:
        return relative_path


def _is_in_venv():                                          # [STARTUP]
    """检测当前 sys.executable 是否位于虚拟环境目录。"""
    exe = sys.executable
    # 虚拟环境常见标志：pyvenvcfg.cfg 同级，或路径含 \venv\ \.venv\ \envs\
    pyvenv_cfg = os.path.join(os.path.dirname(exe), "pyvenvcfg.cfg")
    if os.path.exists(pyvenv_cfg):
        return True
    low = exe.lower()
    for mark in (r"\venv\\", r"\.venv\\", r"\envs\\", r"\virtualenvs\\"):
        if mark in low:
            return True
    return False


def _build_startup_command():                               # [STARTUP]
    """
    构造写入注册表的启动命令字符串。
    返回 (command_str, error_msg)；error_msg 非空表示无法构造（如 venv 场景）。
    """
    try:
        if _is_frozen():
            # 打包为 exe：直接用 sys.executable 作为主程序路径
            exe_path = os.path.abspath(sys.executable)
            return f'"{exe_path}"', None

        # 脚本模式：用解释器 + 脚本路径
        # 检测虚拟环境：避免把 venv 的 python.exe 写入注册表（venv 移动/删除会失效）
        if _is_in_venv():
            return ("", "检测到当前运行在虚拟环境中，已拒绝将 venv 的 python.exe "
                        "写入开机自启。请先打包为 exe（pyinstaller -F -w main.py），"
                        "或使用系统 Python 运行。")

        # 系统 Python：优先用 pythonw.exe（无控制台），找不到则回退 python.exe
        exe_dir = os.path.dirname(sys.executable)
        exe_name = os.path.basename(sys.executable).lower()
        if exe_name == "python.exe":
            pythonw = os.path.join(exe_dir, "pythonw.exe")
            interpreter = pythonw if os.path.exists(pythonw) else sys.executable
        else:
            interpreter = sys.executable

        # 脚本路径：取 main.py 绝对路径（与本文件同目录）
        script_path = os.path.abspath(__file__)
        # 双引号包裹，防止路径含空格被注册表解析截断
        return f'"{interpreter}" "{script_path}"', None
    except Exception as e:
        return ("", f"构造启动命令失败：{type(e).__name__}: {e}")


def is_startup_enabled():                                   # [STARTUP]
    """检查当前程序是否已添加到开机自启。返回 bool。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:                               # 键不存在 = 未启用
        return False
    except OSError as e:                                    # 权限/访问失败
        _msg("ERROR", f"[STARTUP] is_startup_enabled 读取失败: {type(e).__name__}: {e}")
        return False
    except Exception as e:
        _msg("ERROR", f"[STARTUP] is_startup_enabled 异常: {type(e).__name__}: {e}")
        return False


def set_startup_enabled(enabled: bool):                     # [STARTUP]
    """
    启用或禁用开机自启。
    :param enabled: True=写入注册表；False=删除注册表项
    :return: (success: bool, error_msg: str) error_msg 为空表示成功
    """
    try:
        if enabled:
            cmd, err = _build_startup_command()
            if err:
                _msg("WARNING", f"[STARTUP] 拒绝启用开机自启：{err}")
                return False, err
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            _msg("INFO", f"[STARTUP] 已启用开机自启：{cmd}")
            return True, ""
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, APP_NAME)
                _msg("INFO", "[STARTUP] 已禁用开机自启")
            except FileNotFoundError:
                # 本来就没启用，视为成功
                _msg("INFO", "[STARTUP] 禁用开机自启：原本未启用，无需删除")
            return True, ""
    except PermissionError as e:                            # 权限不足
        msg = f"权限不足，无法修改注册表：{e}。请以管理员身份运行。"
        _msg("ERROR", f"[STARTUP] {msg}")
        return False, msg
    except OSError as e:
        msg = f"注册表操作失败：{type(e).__name__}: {e}。请以管理员身份运行。"
        _msg("ERROR", f"[STARTUP] {msg}")
        return False, msg
    except Exception as e:
        msg = f"未知异常：{type(e).__name__}: {e}"
        _msg("ERROR", f"[STARTUP] {msg}")
        return False, msg


# =====================================================================
# [UPDATE] 自动检查更新
#   - 启动后延迟 2s 自动检查（静默，无更新不弹窗）
#   - 托盘菜单 "检查更新" 手动触发（有结果则弹窗）
#   - 使用 urllib（内置库，无需额外依赖），5s 超时
#   - 版本比较用 tuple 拆分数字，避免 distutils 在 3.12+ 弃用问题
# =====================================================================
def _parse_version(v: str):                                 # [UPDATE]
    """将 '1.2.3' 解析为 (1, 2, 3) 元组，便于比较大小。非数字部分当作 0。"""
    parts = []
    for p in str(v).strip().split("."):
        try:
            parts.append(int(p))
        except ValueError:
            # 形如 '1.2.0-beta' → 取前导数字，取不到当 0
            num = ""
            for ch in p:
                if ch.isdigit():
                    num += ch
                else:
                    break
            parts.append(int(num) if num else 0)
    return tuple(parts)


def check_for_updates(manual: bool = False):                # [UPDATE]
    """
    检查是否有新版本。
    :param manual: True=用户手动触发（无更新/失败也弹窗）；False=启动自动检查（静默）
    """
    try:
        url = f"{UPDATE_API_URL}?current_version={VERSION}"  # [UPDATE] 附带当前版本便于服务端统计
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": f"DesktopHider/{VERSION}"})
        with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:  # [UPDATE] 5s 超时
            data = json.loads(resp.read().decode("utf-8"))

        latest = data.get("latest_version")
        if not latest:
            _msg("WARNING", f"[UPDATE] 响应缺少 latest_version 字段：{data}")
            if manual:
                messagebox.showwarning("检查更新", "服务器返回数据格式异常，请稍后重试。")
            return

        _msg("INFO", f"[UPDATE] 当前 {VERSION}，服务器最新 {latest}")

        if _parse_version(latest) > _parse_version(VERSION):  # [UPDATE] 有新版本
            notes = data.get("release_notes", "")
            download_url = data.get("download_url", "")
            force = bool(data.get("force_update", False))

            # 弹窗询问是否前往下载
            msg = (f"发现新版本 {latest}！\n\n"
                   f"当前版本：{VERSION}\n\n"
                   f"更新内容：\n{notes}\n\n"
                   f"是否立即前往下载？")
            # 强制更新时用 showwarning 强调，否则用 askyesno 询问
            if force:
                messagebox.showwarning("发现新版本（建议立即更新）", msg)
                if download_url:
                    webbrowser.open(download_url)            # [UPDATE] 打开下载页
            else:
                if messagebox.askyesno("发现新版本", msg):
                    if download_url:
                        webbrowser.open(download_url)        # [UPDATE] 打开下载页
                    else:
                        messagebox.showwarning("提示", "下载地址未提供，请访问官网获取。")
        else:
            # 已是最新版
            if manual:
                messagebox.showinfo("检查更新", f"当前已是最新版本（{VERSION}）。")
    except urllib.error.URLError as e:                       # [UPDATE] 网络错误
        _msg("WARNING", f"[UPDATE] 网络请求失败: {type(e).__name__}: {e}")
        if manual:
            messagebox.showerror("检查更新",
                                 f"检查更新失败，请检查网络后重试。\n错误信息：{e}")
    except Exception as e:                                   # [UPDATE] 其他异常
        _msg("WARNING", f"[UPDATE] 检查异常: {type(e).__name__}: {e}")
        if manual:
            messagebox.showerror("检查更新",
                                 f"检查更新失败。\n错误信息：{type(e).__name__}: {e}")


# =====================================================================
# [LISTENER] pynput 全局监听回调
#   严禁直接调用 hide_desktop / show_desktop（含 Win32 阻塞调用）；
#   只置标志位，由 control_worker 消费。回调外层 try-except 防止监听器死亡。
# =====================================================================
def _check_whitelist_active():                              # [FEATURE-2]
    """
    检查当前前台窗口是否在白名单中。
    · 白名单为空 → 直接返回 False（不阻塞空闲检测）。
    · 前台进程在白名单 → 设置 _whitelist_active=True，重置计时器，返回 True。
    · 不在白名单 → 设置 _whitelist_active=False，返回 False。
    在 pynput 回调中调用（用户活动时），避免单独轮询线程。
    """
    try:
        wl = get_whitelist()                                # [FEATURE-2] 读最新白名单
        if not wl:                                          # 空白名单直接放行
            if is_whitelist_active():
                set_whitelist_active(False)
            return False
        proc = get_foreground_process_name()                # [FEATURE-2] 当前前台进程（小写）
        if proc and proc in wl:
            if not is_whitelist_active():
                _msg("INFO", f"[白名单] 程序活跃，暂停监控：{proc}")
            set_whitelist_active(True)                      # [FEATURE-2] 标记暂停
            reset_activity_time()                           # [FEATURE-2] 重置计时器，避免触发隐藏
            return True
        # 不在白名单：清除暂停标志
        if is_whitelist_active():
            _msg("INFO", "[白名单] 前台已切换为非白名单程序，恢复监控")
            set_whitelist_active(False)
        return False
    except Exception as e:
        _msg("ERROR", f"[白名单] 检测异常: {type(e).__name__}: {e}")
        return False


def on_mouse_move(x, y):                                   # [LISTENER]
    try:
        update_activity()
        # [FEATURE-2] 白名单检测：前台在白名单则重置计时器并暂停隐藏
        if _check_whitelist_active():                       # [FEATURE-2]
            # 白名单活跃时不切换状态，保持当前显示状态
            if get_state() == State.IDLE:
                set_state(State.ACTIVE)                     # [STATE] IDLE → ACTIVE
            return
        # IDLE 计时态下有移动 → 回到 ACTIVE（无 Win32 副作用，安全）
        if get_state() == State.IDLE:
            set_state(State.ACTIVE)                         # [STATE] IDLE → ACTIVE
    except (WindowsError, AttributeError) as e:             # [LISTENER] 防崩溃
        _msg("ERROR", f"[回调异常] on_mouse_move: {type(e).__name__}: {e}")
    except Exception as e:
        _msg("ERROR", f"[回调异常] on_mouse_move: {type(e).__name__}: {e}")


def on_mouse_click(x, y, button, pressed):                 # [LISTENER]
    try:
        update_activity()
        # 仅响应鼠标左键 "抬起"（一次完整单击按下+抬起的结束）
        if not (button == mouse.Button.left and not pressed):
            return

        # [调试] 验证 on_click 是否被触发；若日志无此行说明监听器已崩溃
        _msg("DEBUG", "[调试] 检测到鼠标点击")               # [LISTENER] 调试输出

        # [STATE] 防抖：只有处于 HIDDEN 状态时才处理，避免多次重复触发
        if get_state() != State.HIDDEN:                     # [STATE] 防重复触发
            return

        _msg("DEBUG", "[调试] 单击唤醒触发")                 # [STATE] 调试输出

        # ================================================================
        # 【关键】此处强制复位状态以确保循环继续。
        # 旧代码仅置标志位等 worker 处理，但 worker 调度有延迟，期间 UI 仍显示
        # "空闲中(已隐藏)"，造成"点击后界面不刷新"的观感。
        # 现改为：立即在回调中把状态切回 ACTIVE（无 Win32 调用，线程安全），
        #         然后再请求 worker 执行实际的 ShowWindow 显示。
        # 这样 UI 的下一轮 250ms 轮询就能立刻看到 ACTIVE，界面同步刷新。
        # ================================================================
        set_state(State.ACTIVE)                             # [STATE] 强制复位 HIDDEN → ACTIVE
        reset_activity_time()                               # [STATE] 重置计时器，闭合循环
        request_show()                                      # 请求 worker 执行 ShowWindow（动态句柄）
        # worker 中 show_desktop 内部亦含"失败也强制 ACTIVE"的兜底逻辑
    except (WindowsError, AttributeError) as e:             # [LISTENER] 防崩溃
        _msg("ERROR", f"[回调异常] on_mouse_click: {type(e).__name__}: {e}")
    except Exception as e:
        _msg("ERROR", f"[回调异常] on_mouse_click: {type(e).__name__}: {e}")


def on_key_press(key):                                     # [LISTENER]
    try:
        update_activity()
        # [FEATURE-2] 白名单检测：键盘活动同样触发检查
        if _check_whitelist_active():                       # [FEATURE-2]
            if get_state() == State.IDLE:
                set_state(State.ACTIVE)                     # [STATE] IDLE → ACTIVE
            return
        if get_state() == State.IDLE:
            set_state(State.ACTIVE)                         # [STATE] IDLE → ACTIVE
    except (WindowsError, AttributeError) as e:             # [LISTENER] 防崩溃
        _msg("ERROR", f"[回调异常] on_key_press: {type(e).__name__}: {e}")
    except Exception as e:
        _msg("ERROR", f"[回调异常] on_key_press: {type(e).__name__}: {e}")


# =====================================================================
# [LISTENER] 监听器监管线程：检测监听器死亡并自动重启
# =====================================================================
def _make_mouse_listener():                                 # [LISTENER]
    return mouse.Listener(on_move=on_mouse_move, on_click=on_mouse_click)


def _make_kb_listener():                                    # [LISTENER]
    return pynput_kb.Listener(on_press=on_key_press)


_g_lock = threading.Lock()


def _set_global_mouse_listener(l):                          # [LISTENER]
    global _mouse_listener
    with _g_lock:
        _mouse_listener = l


def _set_global_kb_listener(l):                             # [LISTENER]
    global _kb_listener
    with _g_lock:
        _kb_listener = l


def _listener_supervisor(factory, name):                    # [LISTENER]
    """
    持续监控某个监听器，若已停止（running=False 或线程死亡）则重新创建并启动。
    """
    listener_holder = {"ref": None}
    while is_running():
        try:
            cur = listener_holder["ref"]
            need_start = (cur is None
                          or not getattr(cur, "running", False)
                          or not cur.is_alive())
            if need_start:
                try:
                    if cur is not None:
                        cur.stop()
                except Exception:
                    pass
                new_listener = factory()
                new_listener.start()
                listener_holder["ref"] = new_listener
                if name == "mouse":
                    _set_global_mouse_listener(new_listener)
                else:
                    _set_global_kb_listener(new_listener)
                _msg("INFO", f"[监听] {name} 监听器已（重新）启动")
        except Exception as e:
            _msg("ERROR", f"[监听] {name} 监管异常: {type(e).__name__}: {e}")
        time.sleep(1.0)


# =====================================================================
# 系统托盘
#   [FEATURE-4] 动态托盘图标：根据状态显示三种颜色变体
#     · active  (绿色) - 监控中
#     · hidden  (红色) - 已隐藏
#     · paused  (灰色) - 暂停 / 白名单激活
#   基础图标加载自外部文件（icon.png / app.ico），状态色通过叠加圆点实现；
#   若无外部图标则用 Pillow 程序化绘制带状态圆点的桌面样式图标。
# =====================================================================
# [FEATURE-4] 状态→颜色映射
_TRAY_COLORS = {                                             # [FEATURE-4]
    "active": (40, 200, 80, 255),    # 绿色：监控中
    "hidden": (220, 60, 60, 255),    # 红色：已隐藏
    "paused": (160, 160, 160, 255),  # 灰色：暂停
}


def _draw_status_dot(img, color):                            # [FEATURE-4]
    """在图标右下角叠加状态圆点（直径 18px）。img: PIL RGBA Image。"""
    try:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        # 右下角圆点，外圈深色描边增强对比
        cx, cy = img.size[0] - 14, img.size[1] - 14
        r = 9
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                   fill=color, outline=(0, 0, 0, 200), width=2)
        return Image.alpha_composite(img, overlay)
    except Exception as e:
        _msg("WARNING", f"[托盘] 绘制状态点失败: {type(e).__name__}: {e}")
        return img


def create_tray_icon_image(state="active"):                  # [FEATURE-4] 增加 state 参数
    """
    生成托盘图标（带状态色变体）。
    :param state: "active"(绿) / "hidden"(红) / "paused"(灰)
    优先加载外部图标文件（icon.png → app.ico，通过 resource_path 兼容打包态），
    叠加状态圆点；若都不存在或加载失败，回退到 Pillow 程序化绘制的默认图标。
    """
    base = None
    # [PACK] 依次尝试 icon.png / app.ico（开发态在脚本目录，打包态在 _MEIPASS）
    for icon_name in ("icon.png", "app.ico"):               # [PACK]
        icon_file = resource_path(icon_name)                # [PACK]
        try:
            if os.path.exists(icon_file):
                base = Image.open(icon_file).convert("RGBA")
                # 统一缩放到 64x64，避免不同尺寸导致托盘显示异常
                base = base.resize((64, 64), Image.LANCZOS)
                _msg("INFO", f"[托盘] 已加载外部图标：{icon_file}")
                break
        except Exception as e:
            _msg("WARNING", f"[托盘] 加载 {icon_name} 失败，回退默认图标：{type(e).__name__}: {e}")

    if base is None:
        # 回退：Pillow 程序化绘制默认桌面样式图标
        base = Image.new("RGBA", (64, 64), color=(30, 30, 30, 255))
        draw = ImageDraw.Draw(base)
        draw.rectangle([10, 14, 54, 44], outline=(220, 220, 220), width=2)
        draw.rectangle([24, 44, 40, 50], fill=(220, 220, 220))
        draw.rectangle([18, 50, 46, 53], fill=(220, 220, 220))
        draw.line([16, 22, 48, 22], fill=(120, 200, 255), width=2)
        draw.line([16, 30, 40, 30], fill=(120, 200, 255), width=2)
        draw.line([16, 38, 44, 38], fill=(120, 200, 255), width=2)

    # [FEATURE-4] 叠加状态色圆点
    color = _TRAY_COLORS.get(state, _TRAY_COLORS["active"])  # [FEATURE-4] 默认绿
    return _draw_status_dot(base, color)


def update_tray_icon(state):                                  # [FEATURE-4]
    """
    切换托盘图标到指定状态色。
    :param state: "active" / "hidden" / "paused"
    在 control_worker 中调用（非 GUI 线程），pystray 的 icon.icon 可跨线程设置。
    """
    global _tray_icon
    if _tray_icon is None:
        return
    try:
        new_img = create_tray_icon_image(state)              # [FEATURE-4] 生成新状态图标
        _tray_icon.icon = new_img                            # [FEATURE-4] 切换图标
        # 同步更新托盘提示文字
        tip_map = {
            "active": "桌面图标隐藏工具 - 监控中",
            "hidden": "桌面图标隐藏工具 - 已隐藏",
            "paused": "桌面图标隐藏工具 - 已暂停",
        }
        _tray_icon.title = tip_map.get(state, "桌面图标隐藏工具")  # [FEATURE-4]
    except Exception as e:
        _msg("ERROR", f"[托盘] 更新图标异常: {type(e).__name__}: {e}")


def on_tray_show_panel(icon, item):
    request_show_panel()


def on_tray_check_update(icon, item):                       # [UPDATE]
    """托盘菜单 '检查更新' 回调：在后台线程执行，避免阻塞托盘。"""
    def _run():
        check_for_updates(manual=True)                      # [UPDATE] 手动触发，有结果必弹窗
    threading.Thread(target=_run, daemon=True).start()      # [UPDATE] 后台线程，5s 超时不卡托盘


def on_tray_quit(icon, item):                                # [EXIT-FIX]
    """托盘 '退出' 菜单回调：统一走 safe_shutdown 安全退出。"""
    request_quit()
    # 找到主窗口对象用于退出 mainloop（ControlPanel 实例存于 _panel_ref）
    root = _panel_ref.root if _panel_ref is not None else None  # [EXIT-FIX]
    safe_shutdown(icon=icon, root=root)                      # [EXIT-FIX] 恢复桌面 + 停监听器/托盘 + 退出


def start_tray():
    global _tray_icon
    try:
        image = create_tray_icon_image("active")            # [FEATURE-4] 初始绿色（监控中）
        menu = pystray.Menu(
            pystray.MenuItem("显示控制面板", on_tray_show_panel),
            pystray.MenuItem("检查更新", on_tray_check_update),  # [UPDATE] 手动检查
            pystray.MenuItem("退出", on_tray_quit),
        )
        _tray_icon = pystray.Icon("desktop_hider", image, "桌面图标隐藏工具 - 监控中", menu)
        _tray_icon.run_detached()
    except Exception as e:
        _msg("ERROR", f"[错误] 托盘初始化失败: {type(e).__name__}: {e}")


# =====================================================================
# [FEATURE-3] 全局热键注册（keyboard 库）
#   · 默认热键：Ctrl + Win + H（可在 config.json 的 hotkey 字段配置）
#   · 注册失败（被占用/无权限/库缺失）仅记日志并弹框提示，不阻断其他功能
#   · keyboard 库自带后台监听线程，无需手动管理；回调仅置标志位
# =====================================================================
def setup_global_hotkey(panel=None):                         # [FEATURE-3]
    """
    注册全局热键。在 main() 中创建 ControlPanel 后调用。
    :param panel: ControlPanel 实例，用于失败时弹框提示（可 None）
    """
    if not _KB_LIB_OK:                                       # [FEATURE-3] keyboard 库未安装/导入失败
        _msg("WARNING", "[热键] keyboard 库不可用，热键功能已禁用。"
                        "请 pip install keyboard 后重启。")
        if panel is not None:
            try:
                panel.root.after(0, lambda: messagebox.showwarning(
                    "热键功能不可用",
                    "未安装 keyboard 库，快捷键唤醒功能已禁用。\n"
                    "其他功能不受影响。\n"
                    "如需启用，请运行：pip install keyboard",
                    parent=panel.root))
            except Exception:
                pass
        return

    hotkey = get_config("hotkey", "ctrl+win+h") or "ctrl+win+h"  # [FEATURE-3] 从配置读取
    try:
        # [FEATURE-3] 注册全局热键；回调仅置标志位，由 worker 消费
        kb_lib.add_hotkey(hotkey, request_hotkey_toggle,
                          suppress=False, trigger_on_release=False)
        _msg("INFO", f"[热键] 全局热键已注册：{hotkey}")
        if panel is not None:
            # [FEATURE-3] 在 GUI 标签显示当前热键（主线程操作 UI）
            display = hotkey.upper().replace("+", " + ")
            try:
                panel.root.after(0, lambda: panel.lbl_hotkey.config(
                    text=f"快捷键：{display}（切换显示/隐藏）"))
            except Exception:
                pass
    except (ValueError, OSError) as e:                       # [FEATURE-3] 热键被占用/无效
        _msg("ERROR", f"[热键] 注册失败（可能被占用）：{type(e).__name__}: {e}")
        if panel is not None:
            try:
                panel.root.after(0, lambda: panel.lbl_hotkey.config(
                    text=f"快捷键：{hotkey} 注册失败（被占用）",
                    foreground="red"))
            except Exception:
                pass
            try:
                panel.root.after(0, lambda: messagebox.showwarning(
                    "热键注册失败",
                    f"全局热键 {hotkey} 注册失败，可能被其他程序占用。\n"
                    f"错误：{type(e).__name__}: {e}\n"
                    f"其他功能不受影响。",
                    parent=panel.root))
            except Exception:
                pass
    except Exception as e:                                    # [FEATURE-3] 其他异常
        _msg("ERROR", f"[热键] 注册异常：{type(e).__name__}: {e}")


# =====================================================================
# [FEATURE-8] 定时计划检查线程
#   · 每分钟检查一次当前时间是否在计划启用时段内
#   · 状态变化时更新 _schedule_paused 标志（worker 读取）
#   · 进入非计划时段时：请求显示桌面（若已隐藏），并设置暂停标志
#   · 进入计划时段时：清除暂停标志，恢复正常监控
# =====================================================================
def schedule_checker():                                       # [FEATURE-8]
    """定时计划检查线程主体，每 60s 检查一次。"""
    last_paused = None                                        # [FEATURE-8] 上次状态，用于检测变化
    while is_running():
        try:
            if not get_config("schedule_enabled", False):    # [FEATURE-8] 计划未启用
                if is_schedule_paused():                     # 之前被暂停过，现在取消
                    set_schedule_paused(False)
                last_paused = False
            else:
                in_window = _is_in_schedule_window()         # [FEATURE-8] 当前是否在计划时段
                should_pause = not in_window                  # 不在计划时段 → 暂停
                if should_pause != last_paused:              # [FEATURE-8] 状态变化才记日志
                    if should_pause:
                        _msg("INFO", f"[定时计划] 非计划时段，暂停监控 "
                                      f"({get_config('schedule_start', '?')}-"
                                      f"{get_config('schedule_end', '?')})")
                        # 若当前已隐藏，请求恢复显示
                        if get_state() == State.HIDDEN:
                            request_show()                    # [FEATURE-8] worker 消费执行 show_desktop
                    else:
                        _msg("INFO", f"[定时计划] 进入计划时段，恢复监控")
                    set_schedule_paused(should_pause)         # [FEATURE-8] 更新标志
                    last_paused = should_pause
        except Exception as e:
            _msg("ERROR", f"[定时计划] 检查异常: {type(e).__name__}: {e}")
        # 每 60s 检查一次
        for _ in range(60):                                   # [FEATURE-8] 分段睡眠，便于快速响应退出
            if not is_running():
                break
            time.sleep(1)


# =====================================================================
# [STATE] 控制工作线程
#   所有 Win32 调用（get_desktop_handle / ShowWindow）集中于此线程，
#   避免阻塞 GUI 主线程。负责驱动 ACTIVE → IDLE → HIDDEN → ACTIVE 闭环。
#   show_desktop / hide_desktop 调用点用 try-except 兜底，防止 worker 崩溃。
# =====================================================================
def control_worker():                                       # [STATE]
    # [FEATURE-5] 健康检查：记录进入 HIDDEN 的时间，超时未恢复则强制复位
    _hidden_since = None                                    # [FEATURE-5]
    # [FEATURE-5] 上次健康检查时间，避免每轮都做重试句柄
    _last_health_check = 0.0                                # [FEATURE-5]
    # [FEATURE-4] 记录上次托盘图标状态，避免每轮重复设置图标
    _last_tray_state = None                                 # [FEATURE-4]

    while is_running():
        time.sleep(0.2)
        try:
            # ============================================================
            # [FEATURE-3] 热键切换请求消费（最高优先级，不依赖其他条件）
            #   · 当前为 HIDDEN → 切到 ACTIVE（执行 show_desktop）
            #   · 当前为 ACTIVE/IDLE → 切到 HIDDEN（执行 hide_desktop）
            #   · 重置计时器，确保后续按空闲逻辑继续运行
            # ============================================================
            if consume_hotkey_toggle():                     # [FEATURE-3]
                cur = get_state()
                _msg("INFO", f"[热键] 收到切换请求，当前状态：{cur.name}")
                if cur == State.HIDDEN:
                    # 隐藏 → 显示
                    try:
                        show_desktop()                      # [FEATURE-3] HIDDEN → ACTIVE
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[热键] show_desktop 异常: {type(e).__name__}: {e}")
                        set_state(State.ACTIVE)             # 异常也强制复位
                elif cur in (State.ACTIVE, State.IDLE):
                    # 显示 → 隐藏
                    try:
                        hide_desktop()                      # [FEATURE-3] → HIDDEN
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[热键] hide_desktop 异常: {type(e).__name__}: {e}")
                reset_activity_time()                       # [FEATURE-3] 重置计时器
                continue

            # 1) 优先处理 "重试获取句柄" 请求（GUI 按钮触发）
            if consume_retry_request():
                # 仅触发一次 get_desktop_handle 以刷新 flag_handle_lost
                get_desktop_handle()
                continue

            # ============================================================
            # [FEATURE-5] 健康检查（每 5s 执行一次，避免高频开销）
            #   · 若当前为 HIDDEN 但桌面句柄已丢失（explorer 重启等），
            #     强制恢复到 ACTIVE，避免永久卡在 HIDDEN。
            #   · 若 HIDDEN 状态持续超过 30 分钟（异常长），强制恢复。
            # ============================================================
            now = time.time()
            cur_state = get_state()
            if cur_state == State.HIDDEN:                   # [FEATURE-5]
                if _hidden_since is None:
                    _hidden_since = now
                # 每 5s 做一次句柄健康检查
                if now - _last_health_check > 5.0:          # [FEATURE-5]
                    _last_health_check = now
                    hwnd = get_desktop_handle()             # [FEATURE-5] 仅查询，不修改
                    if not hwnd:
                        _msg("WARNING", "[健康检查] HIDDEN 态下句柄丢失，强制恢复 ACTIVE")  # [FEATURE-5]
                        set_state(State.ACTIVE)             # [FEATURE-5] 强制复位
                        _hidden_since = None
                    # 异常长 HIDDEN（>30 分钟，可能用户离开且唤醒机制失效）
                    elif now - _hidden_since > 1800:
                        _msg("WARNING", "[健康检查] HIDDEN 持续超过 30 分钟，强制恢复 ACTIVE")  # [FEATURE-5]
                        try:
                            show_desktop()                  # [FEATURE-5] 尝试恢复显示
                        except Exception as e:
                            _msg("ERROR", f"[健康检查] 恢复异常: {type(e).__name__}: {e}")
                            set_state(State.ACTIVE)         # [FEATURE-5] 强制复位
                        _hidden_since = None
            else:
                _hidden_since = None                        # [FEATURE-5] 非 HIDDEN 清除计时

            # 2) 总控关闭（暂停）：仅处理 "恢复显示" 请求，跳过空闲隐藏逻辑
            if not is_enabled():
                if consume_show_request() and get_state() == State.HIDDEN:
                    try:
                        show_desktop()                      # [STATE] 内部含强制复位
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[worker] show_desktop 异常: {type(e).__name__}: {e}")
                        # 即使异常也强制复位，防止卡死
                        set_state(State.ACTIVE)
                    reset_activity_time()                   # [STATE] 重置计时
                continue

            # [FEATURE-8] 定时计划暂停：非计划时段跳过隐藏逻辑，仅处理显示请求
            if is_schedule_paused():                         # [FEATURE-8]
                if consume_show_request() and get_state() == State.HIDDEN:
                    try:
                        show_desktop()                      # [FEATURE-8] 恢复显示
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[worker] show_desktop 异常: {type(e).__name__}: {e}")
                        set_state(State.ACTIVE)
                    reset_activity_time()
                continue

            # 3) HIDDEN 态：等待单击唤醒
            #    注意：on_mouse_click 回调已【立即】将状态切到 ACTIVE 并置 show 请求，
            #    因此 worker 进入此分支时状态通常已是 ACTIVE；此处仅作兜底：
            #    若状态仍为 HIDDEN 且有 show 请求（如暂停态切换触发），才执行显示。
            if get_state() == State.HIDDEN:
                if consume_show_request():
                    try:
                        show_desktop()                      # [STATE] HIDDEN → ACTIVE（含强制复位）
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[worker] show_desktop 异常: {type(e).__name__}: {e}")
                        set_state(State.ACTIVE)             # 异常也强制复位防卡死
                    # 无论成功失败，show_desktop 已保证状态回到 ACTIVE
                    reset_activity_time()                   # [STATE] 重置计时器，闭合循环
                continue

            # 3.5) 状态已是 ACTIVE 但有 show 请求（on_click 已先复位的情况）：
            #      消费请求并执行 ShowWindow 显示，但不改状态（避免覆盖回调已设的 ACTIVE）
            if get_state() == State.ACTIVE and consume_show_request():
                try:
                    # 直接调用 ShowWindow 显示（动态句柄），失败不回退状态
                    hwnd = get_desktop_handle()
                    if hwnd:
                        try:
                            user32.ShowWindow(hwnd, SW_SHOW)
                        except (WindowsError, AttributeError, Exception) as e:
                            _msg("ERROR", f"[worker] 兜底 ShowWindow(SHOW) 异常: {type(e).__name__}: {e}")
                except (WindowsError, AttributeError, Exception) as e:
                    _msg("ERROR", f"[worker] 兜底 get_desktop_handle 异常: {type(e).__name__}: {e}")
                reset_activity_time()
                continue

            # 4) ACTIVE / IDLE 态：处理显示请求（来自暂停切换等）
            if consume_show_request():
                if get_state() == State.HIDDEN:
                    try:
                        show_desktop()
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[worker] show_desktop 异常: {type(e).__name__}: {e}")
                        set_state(State.ACTIVE)
                reset_activity_time()
                continue

            # ============================================================
            # [FEATURE-2] 白名单活跃检查：若前台进程在白名单，跳过隐藏逻辑
            #   · 回调已重置计时器，此处仅判断标志位即可
            #   · 若处于 HIDDEN 态但白名单变为活跃（用户切到白名单程序），
            #     主动恢复显示，避免用户被困在隐藏状态
            # ============================================================
            if is_whitelist_active():                       # [FEATURE-2]
                if get_state() == State.HIDDEN:             # [FEATURE-2] 隐藏态下白名单激活 → 恢复
                    try:
                        show_desktop()                      # [FEATURE-2] 恢复显示
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[白名单] 恢复显示异常: {type(e).__name__}: {e}")
                        set_state(State.ACTIVE)
                    reset_activity_time()
                # 白名单活跃时跳过空闲隐藏逻辑
                continue

            # 5) 空闲计时驱动状态转换
            idle_dur = get_idle_duration()
            threshold = get_idle_seconds()

            if idle_dur >= threshold:
                # 达到阈值 → 尝试隐藏（仅 ACTIVE / IDLE 可进入 HIDDEN）
                if get_state() in (State.ACTIVE, State.IDLE):
                    try:
                        hide_desktop()                      # [STATE] → HIDDEN（失败则保持原态）
                    except (WindowsError, AttributeError, Exception) as e:
                        _msg("ERROR", f"[worker] hide_desktop 异常: {type(e).__name__}: {e}")
                        # 隐藏失败保持 ACTIVE，等待下一周期重试
            else:
                # 未达阈值：ACTIVE → IDLE（无 Win32 副作用）
                if get_state() == State.ACTIVE and idle_dur > 0.3:
                    set_state(State.IDLE)

            # ============================================================
            # [FEATURE-4] 动态托盘图标更新：根据当前状态切换图标颜色
            #   · ACTIVE → 绿色（监控中）
            #   · IDLE   → 绿色（仍监控中，未隐藏）
            #   · HIDDEN → 红色（已隐藏）
            #   · 暂停/白名单活跃 → 灰色（暂停监控）
            # 仅在状态变化时更新图标，避免每轮重复设置造成开销
            # ============================================================
            if _tray_icon is not None:                      # [FEATURE-4]
                if not is_enabled() or is_whitelist_active() or is_schedule_paused():  # [FEATURE-8]
                    new_tray = "paused"                     # [FEATURE-4] 灰色（暂停）
                elif get_state() == State.HIDDEN:
                    new_tray = "hidden"                     # [FEATURE-4] 红色（已隐藏）
                else:
                    new_tray = "active"                     # [FEATURE-4] 绿色（监控中）
                if new_tray != _last_tray_state:            # [FEATURE-4] 仅变化时更新
                    _last_tray_state = new_tray
                    try:
                        update_tray_icon(new_tray)          # [FEATURE-4] 切换图标
                    except Exception as e:
                        _msg("ERROR", f"[托盘] 更新图标异常: {type(e).__name__}: {e}")
        except Exception as e:
            _msg("ERROR", f"[工作线程异常] {type(e).__name__}: {e}")


# =====================================================================
# [GUI] 可视化控制面板
# =====================================================================
class ControlPanel:
    def __init__(self, root):
        self.root = root
        root.title("桌面图标隐藏 - 控制面板")
        # [FEATURE-8] 高度增加以容纳定时计划区 + 日志/备份按钮行
        root.geometry("380x780")                            # [FEATURE-2] 580 → 780
        root.resizable(False, False)
        root.attributes('-topmost', True)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # [FEATURE-3] 当前快捷键文本（由 setup_global_hotkey 填充）
        self._hotkey_text = ""                              # [FEATURE-3]
        # [FEATURE-6] 日志查看器窗口引用（None 表示未打开）
        self._log_window = None                             # [FEATURE-6]

        # ---- 状态看板 ----
        self.lbl_status = tk.Label(root, text="🟢 监控中",
                                   font=("Microsoft YaHei", 15, "bold"),
                                   fg="green")
        self.lbl_status.pack(pady=(18, 4))

        self.lbl_idle = ttk.Label(root, text="累计空闲：0.0 秒",
                                  font=("Microsoft YaHei", 10))
        self.lbl_idle.pack(pady=(0, 8))

        # ---- 错误提示（默认隐藏）----
        self.lbl_error = tk.Label(root, text="❌ 桌面句柄丢失",
                                  fg="red",
                                  font=("Microsoft YaHei", 10, "bold"))
        self.btn_retry = ttk.Button(root, text="重试获取",
                                    command=self.on_retry)

        # ---- 启用开关 ----
        frm_enable = ttk.LabelFrame(root, text="总控")
        frm_enable.pack(fill='x', padx=20, pady=4)
        self.var_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_enable, text="启用监控",
                        variable=self.var_enabled,
                        command=self.on_toggle_enable).pack(anchor='w', padx=8, pady=4)

        # ---- [STARTUP] 开机自启开关 ----
        # 复选框初始状态读取注册表，确保与系统实际状态一致
        startup_init = False
        try:
            startup_init = is_startup_enabled()
        except Exception as e:
            _msg("ERROR", f"[STARTUP] 读取初始状态失败: {type(e).__name__}: {e}")
        self.var_startup = tk.BooleanVar(value=startup_init)        # [STARTUP]
        cb_startup = ttk.Checkbutton(                               # [STARTUP]
            frm_enable, text="开机自动启动",
            variable=self.var_startup,
            command=self.on_toggle_startup)
        cb_startup.pack(anchor='w', padx=8, pady=(0, 4))
        # 悬停提示：在底部状态栏显示说明文字
        self._tip_text = "开启后，每次登录系统将自动启动本工具"     # [STARTUP]
        cb_startup.bind("<Enter>",
                        lambda e: self.lbl_tip.config(text=self._tip_text, foreground="#0066cc"))
        cb_startup.bind("<Leave>",
                        lambda e: self.lbl_tip.config(text=self._DEFAULT_TIP, foreground="gray"))

        # ---- [GUI-BUTTON] 检查更新按钮 ----
        # 放在 "开机自动启动" 复选框下方，单独一行右对齐
        # 点击后在后台线程执行 check_for_updates(manual=True)，
        # 避免 5s 网络 I/O 阻塞 GUI 主循环导致界面卡死。
        self.btn_check_update = ttk.Button(                            # [GUI-BUTTON]
            frm_enable, text="检查更新", width=10,
            command=self.on_check_update)
        self.btn_check_update.pack(anchor='e', padx=8, pady=(2, 6))
        # 悬停提示
        self._update_tip = "立即检查是否有新版本可用"                 # [GUI-BUTTON]
        self.btn_check_update.bind("<Enter>",
                                   lambda e: self.lbl_tip.config(text=self._update_tip, foreground="#0066cc"))
        self.btn_check_update.bind("<Leave>",
                                   lambda e: self.lbl_tip.config(text=self._DEFAULT_TIP, foreground="gray"))

        # ---- 空闲阈值滑动条 ----
        frm_slider = ttk.LabelFrame(root, text="空闲阈值")
        frm_slider.pack(fill='x', padx=20, pady=4)
        self.var_idle = tk.IntVar(value=get_idle_seconds())
        self.scale_idle = ttk.Scale(frm_slider, from_=MIN_IDLE, to=MAX_IDLE,
                                    orient='horizontal', variable=self.var_idle,
                                    command=self.on_idle_change)
        self.scale_idle.pack(fill='x', padx=8, pady=(4, 0))
        self.lbl_idle_val = ttk.Label(frm_slider,
                                      text=f"{get_idle_seconds()} 秒")
        self.lbl_idle_val.pack(anchor='e', padx=8)

        # ---- [FEATURE-2] 白名单管理区 ----
        # 当前台进程在白名单时，暂停空闲隐藏（重置计时器，不进入 HIDDEN）
        frm_wl = ttk.LabelFrame(root, text="白名单（前台进程暂停隐藏）")  # [FEATURE-2]
        frm_wl.pack(fill='x', padx=20, pady=4)

        # 列表框 + 纵向滚动条（高度 5 行，足以显示常用条目）
        wl_list_frame = ttk.Frame(frm_wl)                     # [FEATURE-2]
        wl_list_frame.pack(fill='x', padx=8, pady=(4, 0))
        self.wl_scroll = ttk.Scrollbar(wl_list_frame, orient='vertical')
        self.wl_scroll.pack(side='right', fill='y')
        self.wl_listbox = tk.Listbox(                         # [FEATURE-2] 白名单条目列表
            wl_list_frame, height=5,
            yscrollcommand=self.wl_scroll.set,
            font=("Consolas", 9))
        self.wl_listbox.pack(side='left', fill='x', expand=True)
        self.wl_scroll.config(command=self.wl_listbox.yview)
        # 启动时从配置加载已存在的白名单条目
        for name in get_whitelist():                          # [FEATURE-2]
            self.wl_listbox.insert('end', name)

        # 输入框 + 添加按钮
        wl_input_frame = ttk.Frame(frm_wl)                    # [FEATURE-2]
        wl_input_frame.pack(fill='x', padx=8, pady=(2, 0))
        self.var_wl_input = tk.StringVar()                    # [FEATURE-2]
        self.entry_wl = ttk.Entry(wl_input_frame,
                                  textvariable=self.var_wl_input)
        self.entry_wl.pack(side='left', fill='x', expand=True)
        self.btn_wl_add = ttk.Button(wl_input_frame, text="添加",
                                     width=6, command=self.on_wl_add)  # [FEATURE-2]
        self.btn_wl_add.pack(side='left', padx=(4, 0))

        # 移除选中按钮
        wl_btn_frame = ttk.Frame(frm_wl)                      # [FEATURE-2]
        wl_btn_frame.pack(fill='x', padx=8, pady=(2, 4))
        self.btn_wl_remove = ttk.Button(wl_btn_frame, text="移除选中",
                                        command=self.on_wl_remove)  # [FEATURE-2]
        self.btn_wl_remove.pack(side='left')

        # ---- [FEATURE-8] 定时计划区 ----
        # 启用后在指定时间段内执行监控，其余时间自动暂停
        frm_sched = ttk.LabelFrame(root, text="定时计划（时间段内启用监控）")  # [FEATURE-8]
        frm_sched.pack(fill='x', padx=20, pady=4)
        self.var_sched_enabled = tk.BooleanVar(               # [FEATURE-8] 计划开关
            value=bool(get_config("schedule_enabled", False)))
        ttk.Checkbutton(frm_sched, text="启用定时计划",
                        variable=self.var_sched_enabled,
                        command=self.on_sched_toggle).pack(anchor='w', padx=8, pady=(4, 2))
        # 开始时间 / 结束时间 Spinbox（HH:MM）
        sched_time_frame = ttk.Frame(frm_sched)               # [FEATURE-8]
        sched_time_frame.pack(fill='x', padx=8, pady=(0, 4))
        ttk.Label(sched_time_frame, text="开始：").pack(side='left')
        sched_start = get_config("schedule_start", "09:00")   # [FEATURE-8]
        sh, sm = sched_start.split(":")
        self.var_sched_start_h = tk.IntVar(value=int(sh))     # [FEATURE-8]
        self.var_sched_start_m = tk.IntVar(value=int(sm))     # [FEATURE-8]
        ttk.Spinbox(sched_time_frame, from_=0, to=23, width=4,
                    textvariable=self.var_sched_start_h,
                    command=self.on_sched_time_change).pack(side='left')
        ttk.Label(sched_time_frame, text=":").pack(side='left')
        ttk.Spinbox(sched_time_frame, from_=0, to=59, width=4,
                    textvariable=self.var_sched_start_m,
                    command=self.on_sched_time_change).pack(side='left')
        ttk.Label(sched_time_frame, text="  结束：").pack(side='left')
        sched_end = get_config("schedule_end", "18:00")       # [FEATURE-8]
        eh, em = sched_end.split(":")
        self.var_sched_end_h = tk.IntVar(value=int(eh))       # [FEATURE-8]
        self.var_sched_end_m = tk.IntVar(value=int(em))       # [FEATURE-8]
        ttk.Spinbox(sched_time_frame, from_=0, to=23, width=4,
                    textvariable=self.var_sched_end_h,
                    command=self.on_sched_time_change).pack(side='left')
        ttk.Label(sched_time_frame, text=":").pack(side='left')
        ttk.Spinbox(sched_time_frame, from_=0, to=59, width=4,
                    textvariable=self.var_sched_end_m,
                    command=self.on_sched_time_change).pack(side='left')

        # ---- [FEATURE-6][FEATURE-7] 工具按钮行（日志/备份/恢复）----
        frm_tools = ttk.LabelFrame(root, text="工具")         # [FEATURE-6][FEATURE-7]
        frm_tools.pack(fill='x', padx=20, pady=4)
        tools_row1 = ttk.Frame(frm_tools)                     # [FEATURE-7] 备份/恢复行
        tools_row1.pack(fill='x', padx=8, pady=(4, 2))
        self.btn_backup = ttk.Button(tools_row1, text="备份配置",  # [FEATURE-7]
                                     width=10, command=self.on_backup_config)
        self.btn_backup.pack(side='left', padx=(0, 4))
        self.btn_restore = ttk.Button(tools_row1, text="恢复配置",  # [FEATURE-7]
                                      width=10, command=self.on_restore_config)
        self.btn_restore.pack(side='left')
        tools_row2 = ttk.Frame(frm_tools)                     # [FEATURE-6] 日志按钮行
        tools_row2.pack(fill='x', padx=8, pady=(0, 4))
        self.btn_view_log = ttk.Button(tools_row2, text="查看日志",  # [FEATURE-6]
                                       width=10, command=self.on_view_log)
        self.btn_view_log.pack(side='left')

        # ---- [FEATURE-3] 快捷键显示标签 ----
        # 显示当前全局热键（由 setup_global_hotkey 在 main() 中回填）
        self.lbl_hotkey = ttk.Label(                         # [FEATURE-3]
            root, text="快捷键：Ctrl+Win+H（切换显示/隐藏）",
            foreground="#0066cc",
            font=("Microsoft YaHei", 9))
        self.lbl_hotkey.pack(side='bottom', pady=(0, 2))

        # ---- 底部提示（兼作 [STARTUP] tooltip 状态栏）----
        self._DEFAULT_TIP = "关闭本窗口仅隐藏面板；右键托盘图标可退出"  # [STARTUP]
        self.lbl_tip = ttk.Label(root, text=self._DEFAULT_TIP, foreground="gray")  # [STARTUP]
        self.lbl_tip.pack(side='bottom', pady=8)

    def on_close(self):
        self.root.withdraw()

    def on_toggle_enable(self):
        enabled = self.var_enabled.get()
        set_enabled(enabled)
        if not enabled:
            request_show()  # 暂停时请求工作线程恢复显示
        reset_activity_time()
        _msg("INFO", f"[总控] 监控已{'启用' if enabled else '暂停'}")

    def on_toggle_startup(self):                            # [STARTUP]
        """开机自启复选框勾选/取消回调。失败时弹错误框并回滚复选框状态。"""
        wanted = self.var_startup.get()
        ok, err = set_startup_enabled(wanted)
        if not ok:
            # 操作失败：回滚复选框到实际状态，并用消息框提示用户
            actual = is_startup_enabled()
            self.var_startup.set(actual)
            _msg("WARNING", f"[STARTUP] 开机自启设置失败，已回滚：{err}")
            try:
                messagebox.showerror(
                    "开机自启设置失败",
                    f"{err}\n\n请以管理员身份运行本程序后重试。",
                    parent=self.root)
            except Exception as e:
                _msg("ERROR", f"[STARTUP] 弹出错误框失败: {type(e).__name__}: {e}")
        else:
            # 成功：同步复选框到实际注册表状态（避免状态不一致）
            self.var_startup.set(is_startup_enabled())
            _msg("INFO", f"[STARTUP] 开机自启已{'启用' if wanted else '禁用'}")

    def on_check_update(self):                                # [GUI-BUTTON]
        """
        "检查更新" 按钮回调。
        在后台线程执行 check_for_updates(manual=True)，避免 5s 网络 I/O 阻塞 GUI。
        重复点击期间禁用按钮，防止并发请求。
        """
        if not getattr(self, "_checking", False):            # [GUI-BUTTON] 防重复点击
            self._checking = True
            self.btn_check_update.config(state='disabled', text="检查中…")  # [GUI-BUTTON]

            def _run():                                       # [GUI-BUTTON] 后台线程
                try:
                    check_for_updates(manual=True)            # [GUI-BUTTON] 复用更新检查函数
                except Exception as e:
                    _msg("ERROR", f"[GUI-BUTTON] 检查更新异常: {type(e).__name__}: {e}")
                    try:
                        messagebox.showerror("检查更新",
                                             f"检查更新失败。\n错误信息：{type(e).__name__}: {e}",
                                             parent=self.root)
                    except Exception:
                        pass
                finally:
                    # 恢复按钮状态（必须在主线程操作 UI）
                    try:
                        self.root.after(0, self._reset_update_button)  # [GUI-BUTTON]
                    except Exception:
                        pass

            threading.Thread(target=_run, daemon=True).start()  # [GUI-BUTTON]

    def _reset_update_button(self):                           # [GUI-BUTTON]
        """恢复检查更新按钮为可点击状态（在主线程调用）。"""
        try:
            self._checking = False
            self.btn_check_update.config(state='normal', text="检查更新")
        except Exception:
            pass

    def on_idle_change(self, val):                            # [FEATURE-1]
        """滑动条值变化回调：实时生效 + 持久化到 config.json。"""
        v = int(float(val))
        # 范围钳制（防御滑动条返回越界值）
        if v < MIN_IDLE:
            v = MIN_IDLE
        if v > MAX_IDLE:
            v = MAX_IDLE
        set_idle_seconds(v)                                   # [FEATURE-1] 实时生效（worker 下一周期读取新阈值）
        set_config("idle_seconds", v)                         # [FEATURE-1] 同步到配置缓存
        save_config()                                         # [FEATURE-1] 立即回写文件，避免退出丢失
        self.lbl_idle_val.config(text=f"{v} 秒")

    # ---- [FEATURE-2] 白名单按钮回调（主线程中执行，安全操作 UI）----
    def on_wl_add(self):                                      # [FEATURE-2]
        """添加按钮：从输入框取值，加入白名单并刷新列表。"""
        name = self.var_wl_input.get().strip()
        if not name:
            return
        if add_to_whitelist(name):                            # [FEATURE-2] 已含去重 + 持久化
            self._refresh_wl_listbox()                        # [FEATURE-2] 刷新列表显示
            self.var_wl_input.set("")                         # 清空输入框
            _msg("INFO", f"[白名单] GUI 添加：{name}")

    def on_wl_remove(self):                                   # [FEATURE-2]
        """移除选中按钮：删除列表框中当前选中的条目。"""
        sel = self.wl_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        name = self.wl_listbox.get(idx)
        if remove_from_whitelist(name):                       # [FEATURE-2] 已含持久化
            self._refresh_wl_listbox()                        # [FEATURE-2] 刷新列表显示
            _msg("INFO", f"[白名单] GUI 移除：{name}")

    def _refresh_wl_listbox(self):                            # [FEATURE-2]
        """从配置缓存重新填充白名单列表框（保证 UI 与数据一致）。"""
        try:
            self.wl_listbox.delete(0, 'end')
            for name in get_whitelist():                      # [FEATURE-2] 读取最新白名单
                self.wl_listbox.insert('end', name)
        except Exception as e:
            _msg("ERROR", f"[白名单] 刷新列表框失败: {type(e).__name__}: {e}")

    # ---- [FEATURE-8] 定时计划回调 ----
    def on_sched_toggle(self):                                # [FEATURE-8]
        """启用/禁用定时计划复选框回调。"""
        enabled = self.var_sched_enabled.get()
        set_config("schedule_enabled", enabled)               # [FEATURE-8] 更新配置缓存
        save_config()                                         # [FEATURE-8] 持久化
        if not enabled:                                       # 禁用时清除暂停标志
            set_schedule_paused(False)
        _msg("INFO", f"[定时计划] 已{'启用' if enabled else '禁用'}")

    def on_sched_time_change(self):                           # [FEATURE-8]
        """开始/结束时间 Spinbox 值变化回调：组装 HH:MM 并保存。"""
        try:
            sh = int(self.var_sched_start_h.get())
            sm = int(self.var_sched_start_m.get())
            eh = int(self.var_sched_end_h.get())
            em = int(self.var_sched_end_m.get())
            start_str = f"{sh:02d}:{sm:02d}"                  # [FEATURE-8] 规范化
            end_str = f"{eh:02d}:{em:02d}"
            set_config("schedule_start", start_str)           # [FEATURE-8] 更新配置
            set_config("schedule_end", end_str)
            save_config()                                     # [FEATURE-8] 持久化
            _msg("INFO", f"[定时计划] 时间更新：{start_str} - {end_str}")
        except (TypeError, ValueError) as e:
            _msg("ERROR", f"[定时计划] 时间格式异常: {type(e).__name__}: {e}")

    # ---- [FEATURE-7] 配置备份与恢复回调 ----
    def on_backup_config(self):                               # [FEATURE-7]
        """备份配置：弹出保存对话框，将当前配置导出为 JSON 文件。"""
        try:
            default_name = f"config_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"  # [FEATURE-7]
            filepath = filedialog.asksaveasfilename(           # [FEATURE-7] 保存对话框
                title="备份配置",
                defaultextension=".json",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
                initialfile=default_name,
                parent=self.root)
            if not filepath:                                  # 用户取消
                return
            # 读取当前配置并写入用户指定文件
            with _config_lock:
                data = dict(_config_cache)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _msg("INFO", f"[配置备份] 已导出到：{filepath}")
            messagebox.showinfo("备份成功",
                                f"配置已备份到：\n{filepath}",
                                parent=self.root)
        except OSError as e:
            _msg("ERROR", f"[配置备份] 写入失败: {type(e).__name__}: {e}")
            messagebox.showerror("备份失败",
                                 f"写入文件失败：{type(e).__name__}: {e}",
                                 parent=self.root)
        except Exception as e:
            _msg("ERROR", f"[配置备份] 异常: {type(e).__name__}: {e}")
            messagebox.showerror("备份失败",
                                 f"备份异常：{type(e).__name__}: {e}",
                                 parent=self.root)

    def on_restore_config(self):                              # [FEATURE-7]
        """恢复配置：弹出文件选择对话框，导入 JSON 并应用到当前设置。"""
        try:
            filepath = filedialog.askopenfilename(             # [FEATURE-7] 打开对话框
                title="恢复配置",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
                parent=self.root)
            if not filepath:                                  # 用户取消
                return
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)                           # [FEATURE-7] 解析 JSON
            # 完整性校验：必须包含至少 idle_seconds 字段
            if not isinstance(data, dict):
                messagebox.showerror("恢复失败",
                                     "文件格式错误：不是有效的 JSON 对象。",
                                     parent=self.root)
                return
            if "idle_seconds" not in data:
                messagebox.showerror("恢复失败",
                                     "配置文件缺少必要字段 idle_seconds，可能不是有效的配置备份。",
                                     parent=self.root)
                return
            # 合并默认值（缺字段补默认）
            merged = dict(_DEFAULT_CONFIG)
            merged.update(data)
            # 校验并规范化各字段
            try:
                v = int(merged.get("idle_seconds", IDLE_SECONDS))
                merged["idle_seconds"] = max(MIN_IDLE, min(MAX_IDLE, v))
            except (TypeError, ValueError):
                merged["idle_seconds"] = IDLE_SECONDS
            if not isinstance(merged.get("whitelist"), list):
                merged["whitelist"] = []
            merged["whitelist"] = [str(x).lower() for x in merged["whitelist"]]
            merged["schedule_enabled"] = bool(merged.get("schedule_enabled", False))  # [FEATURE-8]
            merged["schedule_start"] = _validate_time_str(
                merged.get("schedule_start", "09:00"), "09:00")
            merged["schedule_end"] = _validate_time_str(
                merged.get("schedule_end", "18:00"), "18:00")
            # 应用到内存配置缓存并回写 config.json
            with _config_lock:
                _config_cache = merged
            save_config()                                     # [FEATURE-7] 立即回写
            # 同步应用到运行时状态
            set_idle_seconds(merged["idle_seconds"])          # [FEATURE-7] 更新空闲阈值
            # 刷新所有 GUI 控件
            self._apply_config_to_gui(merged)                 # [FEATURE-7] 统一刷新
            _msg("INFO", f"[配置恢复] 已从 {filepath} 恢复配置")
            messagebox.showinfo("恢复成功",
                                f"配置已恢复并应用：\n{filepath}",
                                parent=self.root)
        except json.JSONDecodeError as e:
            _msg("ERROR", f"[配置恢复] JSON 解析失败: {type(e).__name__}: {e}")
            messagebox.showerror("恢复失败",
                                 f"JSON 解析失败：{e}",
                                 parent=self.root)
        except OSError as e:
            _msg("ERROR", f"[配置恢复] 读取失败: {type(e).__name__}: {e}")
            messagebox.showerror("恢复失败",
                                 f"读取文件失败：{type(e).__name__}: {e}",
                                 parent=self.root)
        except Exception as e:
            _msg("ERROR", f"[配置恢复] 异常: {type(e).__name__}: {e}")
            messagebox.showerror("恢复失败",
                                 f"恢复异常：{type(e).__name__}: {e}",
                                 parent=self.root)

    def _apply_config_to_gui(self, cfg):                      # [FEATURE-7]
        """将配置 dict 同步刷新到所有 GUI 控件（恢复配置后调用）。"""
        try:
            # 滑动条
            idle_val = int(cfg.get("idle_seconds", IDLE_SECONDS))
            self.var_idle.set(idle_val)
            self.lbl_idle_val.config(text=f"{idle_val} 秒")
            # 白名单列表框
            self._refresh_wl_listbox()
            # 定时计划
            self.var_sched_enabled.set(bool(cfg.get("schedule_enabled", False)))
            start_str = cfg.get("schedule_start", "09:00")    # [FEATURE-8]
            end_str = cfg.get("schedule_end", "18:00")
            sh, sm = start_str.split(":")
            eh, em = end_str.split(":")
            self.var_sched_start_h.set(int(sh))
            self.var_sched_start_m.set(int(sm))
            self.var_sched_end_h.set(int(eh))
            self.var_sched_end_m.set(int(em))
            # 若计划未启用，清除暂停标志
            if not cfg.get("schedule_enabled", False):
                set_schedule_paused(False)
            _msg("INFO", "[配置恢复] GUI 控件已刷新")
        except Exception as e:
            _msg("ERROR", f"[配置恢复] GUI 刷新异常: {type(e).__name__}: {e}")

    # ---- [FEATURE-6] 日志查看器回调 ----
    def on_view_log(self):                                    # [FEATURE-6]
        """打开日志查看器窗口（Toplevel + ScrolledText）。"""
        if self._log_window is not None:                      # [FEATURE-6] 已打开则聚焦
            try:
                if self._log_window.winfo_exists():
                    self._log_window.lift()
                    self._log_window.focus_force()
                    return
            except Exception:
                pass
            self._log_window = None

        win = tk.Toplevel(self.root)                          # [FEATURE-6] 独立窗口
        win.title("运行日志查看器")
        win.geometry("700x500")
        win.resizable(True, True)
        self._log_window = win

        # 工具栏：刷新 + 复制 + 清空显示
        toolbar = ttk.Frame(win)                              # [FEATURE-6]
        toolbar.pack(fill='x', padx=8, pady=(8, 4))
        self.btn_log_refresh = ttk.Button(toolbar, text="刷新",
                                          command=self._refresh_log_text)  # [FEATURE-6]
        self.btn_log_refresh.pack(side='left', padx=(0, 4))
        self.btn_log_copy = ttk.Button(toolbar, text="复制",
                                       command=self._copy_log_text)  # [FEATURE-6]
        self.btn_log_copy.pack(side='left', padx=(0, 4))
        ttk.Label(toolbar, text="（每秒自动刷新）",
                  foreground="gray").pack(side='left')
        # 关闭窗口时清理引用
        win.protocol("WM_DELETE_WINDOW", self._on_log_window_close)

        # 多行文本框（只读 + 自动滚动）
        self.txt_log = scrolledtext.ScrolledText(             # [FEATURE-6] 滚动文本框
            win, wrap='word', state='normal',
            font=("Consolas", 9))
        self.txt_log.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        # 首次填充：从环形缓冲区加载历史日志
        self._refresh_log_text()                              # [FEATURE-6] 初始加载
        # 启动定时刷新（每 1s 从队列拉取新日志）
        self._poll_log_queue()                                # [FEATURE-6] 启动轮询

    def _on_log_window_close(self):                           # [FEATURE-6]
        """日志窗口关闭回调：清理引用。"""
        try:
            if self._log_window is not None:
                self._log_window.destroy()
        except Exception:
            pass
        self._log_window = None                               # [FEATURE-6] 清除引用

    def _refresh_log_text(self):                              # [FEATURE-6]
        """从环形缓冲区重新加载全部日志到文本框（手动刷新按钮调用）。"""
        try:
            if self._log_window is None or not self._log_window.winfo_exists():
                return
            self.txt_log.config(state='normal')
            self.txt_log.delete('1.0', 'end')
            # 从环形缓冲区读取所有历史日志
            for line in list(_LOG_RING_BUFFER):               # [FEATURE-6] 拷贝快照
                self.txt_log.insert('end', line + "\n")
            # 自动滚动到最底部
            self.txt_log.see('end')
            self.txt_log.config(state='disabled')
        except Exception as e:
            _msg("ERROR", f"[日志查看器] 刷新异常: {type(e).__name__}: {e}")

    def _poll_log_queue(self):                                # [FEATURE-6]
        """定时（1s）从日志队列拉取新日志追加到文本框。"""
        try:
            if self._log_window is None or not self._log_window.winfo_exists():
                return  # 窗口已关闭，停止轮询
            # 从队列取出所有待处理日志（非阻塞）
            new_lines = []
            while True:
                try:
                    line = _LOG_QUEUE.get_nowait()            # [FEATURE-6] 非阻塞拉取
                    new_lines.append(line)
                except Empty:
                    break
            if new_lines:
                self.txt_log.config(state='normal')
                for line in new_lines:
                    self.txt_log.insert('end', line + "\n")   # [FEATURE-6] 追加新日志
                # 限制文本框行数（超过 1000 行删除最旧部分，与环形缓冲区对齐）
                line_count = int(self.txt_log.index('end-1c').split('.')[0])
                if line_count > 1000:
                    self.txt_log.delete('1.0', f'{line_count - 1000}.0')
                self.txt_log.see('end')                       # [FEATURE-6] 自动滚动到最新
                self.txt_log.config(state='disabled')
            # 1s 后继续轮询
            self._log_window.after(1000, self._poll_log_queue)  # [FEATURE-6] 定时调度
        except Exception as e:
            _msg("ERROR", f"[日志查看器] 轮询异常: {type(e).__name__}: {e}")

    def _copy_log_text(self):                                 # [FEATURE-6]
        """复制当前日志文本框内容到系统剪贴板。"""
        try:
            if self._log_window is None or not self._log_window.winfo_exists():
                return
            content = self.txt_log.get('1.0', 'end-1c')       # [FEATURE-6] 获取全部文本
            self.root.clipboard_clear()                       # [FEATURE-6] 清空剪贴板
            self.root.clipboard_append(content)               # [FEATURE-6] 写入剪贴板
            _msg("INFO", "[日志查看器] 日志已复制到剪贴板")
        except Exception as e:
            _msg("ERROR", f"[日志查看器] 复制异常: {type(e).__name__}: {e}")

    def on_retry(self):
        request_retry()  # 仅置标志位，由工作线程执行 get_desktop_handle

    def show_error(self):
        if not self.lbl_error.winfo_ismapped():
            self.lbl_error.pack(pady=(0, 2))
            self.btn_retry.pack(pady=(0, 6))

    def hide_error(self):
        if self.lbl_error.winfo_ismapped():
            self.lbl_error.pack_forget()
            self.btn_retry.pack_forget()

    # ---- 周期性刷新（只读状态 + 更新标签，严禁调用 Win32）----
    # 250ms 主轮询 + 1s 兜底同步：即使某次轮询漏掉状态变化，1s 兜底也会强制对齐
    def update_status(self):
        if consume_show_panel_request():
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

        if consume_quit_request():
            self.root.destroy()
            return

        enabled = is_enabled()
        handle_lost = is_handle_lost()
        state = get_state()
        idle_dur = get_idle_duration()
        wl_active = is_whitelist_active()                     # [FEATURE-2] 白名单活跃标志
        sched_paused = is_schedule_paused()                    # [FEATURE-8] 定时计划暂停标志

        # [UI-SYNC] 检测 UI 显示态与实际状态是否一致，不一致则打印警告便于调试
        last_shown = getattr(self, "_last_shown_state", None)
        if last_shown is not None and last_shown != state:
            _msg("INFO", f"[UI-SYNC] UI 滞后纠正：{last_shown.name} → {state.name}")
        self._last_shown_state = state

        if handle_lost:
            self.show_error()
        else:
            self.hide_error()

        if not enabled:
            self.lbl_status.config(text="⏸️ 已暂停", fg="#888888")
            self.lbl_idle.config(text="累计空闲：— 暂停 —")
        elif handle_lost:
            self.lbl_status.config(text="❌ 句柄丢失", fg="red")
            self.lbl_idle.config(text="将自动重试获取…")
        elif sched_paused:                                     # [FEATURE-8] 定时计划暂停
            self.lbl_status.config(text="🕤 非计划时段", fg="#666699")
            self.lbl_idle.config(text="定时计划暂停监控")
        elif wl_active:                                       # [FEATURE-2] 白名单活跃：暂停监控
            self.lbl_status.config(text="🚫 白名单暂停", fg="#9966cc")
            self.lbl_idle.config(text="前台程序在白名单中，已暂停隐藏")
        elif state == State.HIDDEN:
            self.lbl_status.config(text="⏳ 空闲中 (已隐藏)", fg="#cc8800")
            self.lbl_idle.config(text="等待单击唤醒…")
        elif state == State.IDLE:
            self.lbl_status.config(text="🟡 空闲计时中", fg="#b8860b")
            self.lbl_idle.config(text=f"累计空闲：{idle_dur:.1f} / {get_idle_seconds()} 秒")
        else:
            self.lbl_status.config(text="🟢 监控中", fg="green")
            self.lbl_idle.config(text=f"累计空闲：{idle_dur:.1f} / {get_idle_seconds()} 秒")

        self.root.after(250, self.update_status)

    # ---- [UI-SYNC] 1s 兜底刷新：强制根据当前 state 重设标签，防止 UI 与状态机失步 ----
    def update_status_fallback(self):                        # [UI-SYNC]
        try:
            if not self.root.winfo_exists():
                return
            state = get_state()
            # 仅做"标签对齐"，不消费任何请求标志位
            if state == State.HIDDEN and "已隐藏" not in self.lbl_status.cget("text"):
                _msg("WARNING", "[UI-SYNC] 兜底：标签与 HIDDEN 状态不一致，已强制对齐")
                self.lbl_status.config(text="⏳ 空闲中 (已隐藏)", fg="#cc8800")
                self.lbl_idle.config(text="等待单击唤醒…")
            elif state == State.ACTIVE and "监控中" not in self.lbl_status.cget("text") \
                    and "暂停" not in self.lbl_status.cget("text") \
                    and "句柄" not in self.lbl_status.cget("text"):
                _msg("WARNING", "[UI-SYNC] 兜底：标签与 ACTIVE 状态不一致，已强制对齐")
                self.lbl_status.config(text="🟢 监控中", fg="green")
                self.lbl_idle.config(text=f"累计空闲：{get_idle_duration():.1f} / {get_idle_seconds()} 秒")
        except Exception as e:
            _msg("ERROR", f"[UI-SYNC] 兜底刷新异常: {type(e).__name__}: {e}")
        self.root.after(1000, self.update_status_fallback)   # 每 1s 兜底一次


# =====================================================================
# 主程序
# =====================================================================
def main():
    global _panel_ref
    # [FEATURE-1] 启动时加载 config.json，应用 idle_seconds / whitelist / hotkey
    try:
        cfg = load_config()                                  # [FEATURE-1] 加载配置（失败用默认值）
        loaded_idle = int(cfg.get("idle_seconds", IDLE_SECONDS))
        if MIN_IDLE <= loaded_idle <= MAX_IDLE:
            set_idle_seconds(loaded_idle)                    # [FEATURE-1] 应用持久化的空闲阈值
            _msg("INFO", f"[启动] 已加载空闲阈值：{loaded_idle} 秒")
    except Exception as e:
        _msg("WARNING", f"[启动] 加载配置异常，使用默认值：{type(e).__name__}: {e}")

    root = tk.Tk()
    panel = ControlPanel(root)
    _panel_ref = panel                                       # [EXIT-FIX] 供 on_tray_quit 获取 root

    # [FEATURE-3] 注册全局热键（keyboard 库可用时）
    setup_global_hotkey(panel)                               # [FEATURE-3]

    start_tray()

    # [LISTENER] 监听器监管线程（自动重启死亡监听器）
    threading.Thread(target=_listener_supervisor,
                     args=(_make_mouse_listener, "mouse"),
                     daemon=True).start()
    threading.Thread(target=_listener_supervisor,
                     args=(_make_kb_listener, "keyboard"),
                     daemon=True).start()

    # 控制工作线程（所有 Win32 调用集中于此）
    threading.Thread(target=control_worker, daemon=True).start()

    # [FEATURE-8] 定时计划检查线程（每分钟检查时段）
    threading.Thread(target=schedule_checker, daemon=True).start()  # [FEATURE-8]

    _msg("INFO", f"[启动] 空闲 {get_idle_seconds()} 秒后将隐藏桌面图标；"
                  f"右键托盘 \"显示控制面板\" 可打开设置。")

    root.after(250, panel.update_status)
    root.after(1000, panel.update_status_fallback)          # [UI-SYNC] 1s 兜底刷新
    root.after(UPDATE_CHECK_DELAY,                          # [UPDATE] 启动后延迟 2s 静默检查
               lambda: threading.Thread(target=lambda: check_for_updates(manual=False),
                                        daemon=True).start())

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        # [EXIT-FIX] 统一走 safe_shutdown：恢复桌面 + 停监听器/托盘
        # 即使 on_tray_quit 已调用过 safe_shutdown，这里再次调用也是幂等的（stop_running / show_desktop 可重入）
        safe_shutdown(icon=_tray_icon, root=root)            # [EXIT-FIX]
        _msg("INFO", "[退出] 程序已结束。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # pythonw 下无控制台，用 Win32 消息框提示致命错误；同时写日志
        err_text = f"程序异常终止：\n{type(e).__name__}: {e}\n\n日志文件：{_LOG_PATH}"
        _msg("CRITICAL", f"[致命错误] 程序异常终止: {type(e).__name__}: {e}")
        _fatal_msgbox("桌面图标隐藏工具 - 致命错误", err_text)

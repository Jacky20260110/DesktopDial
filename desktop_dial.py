#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  桌面圆盘启动器 / Desktop Dial Launcher
#  Copyright (C) 2026 Jacky
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#  本程序是自由软件：您可以依据自由软件基金会发布的 GNU 通用公共许可证
#  第 3 版或（由您选择）任何更新版本的条款，重新分发和/或修改本程序。
#  本程序不含任何担保，包括但不限于适销性与特定用途适用性的默示担保。
#  许可证全文见项目根目录的 LICENSE 文件；个人与企业均可免费使用，
#  修改与再分发须同样遵循 GPLv3（含提供完整对应源码的义务）。
# =============================================================================
"""
桌面圆盘启动器 (Desktop Dial Launcher)
=====================================

交互模型
--------
* 空闲：不创建任何常驻窗口，只保留一个 WH_MOUSE_LL / WH_KEYBOARD_LL 全局钩子线程。
  用户看到的只有桌面壁纸本身。
* 唤起：鼠标在桌面（Shell 窗口 Progman / WorkerW）上按下左键并拖动超过阈值，
  才在按下位置弹出环形扇区菜单。单纯单击不会唤起。
* 选择：按住不放，朝某个扇区方向移动即高亮该扇区，松开左键执行动作。
* 取消：鼠标回到圆盘死区 / 甩出外圈安全区 / 按 Esc / 右键，均不执行任何动作。
* 收起：松开左键后先缓动收缩再隐藏，回到完全隐身状态。
* 热键：任意窗口下按全局热键（默认 Ctrl+Alt+Space）在光标处唤出，
  移动选中、单击确认、数字键 1..N 直接启动、Esc / 点空白取消。

依赖：Python 3.8+ / PyQt5（Windows 平台），全局钩子通过 ctypes 调用 user32，
音效由 numpy 合成后经 QtMultimedia(QSoundEffect) 播放（缺失时回退 winsound）。

作者说明：本文件为最终交付版本，配套文件：
    desktop_dial.pyw   免控制台启动
    desktop_dial_config.json  配置文件
    sounds/            程序合成的音效套件（三套主题）
    requirements.txt / README.md
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import ctypes.wintypes as wt
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import (
    QPoint, QPointF, QRect, QRectF, QSize, QTimer, Qt, QObject, pyqtSignal, pyqtSlot,
    QFileInfo, QPropertyAnimation, QEasingCurve, pyqtProperty, QUrl, QByteArray,
)
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QCursor, QRadialGradient, QDragEnterEvent,
    QDropEvent, QKeySequence,
)
from PyQt5.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QAction, QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFileIconProvider, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QSizePolicy, QSlider, QSpinBox, QDoubleSpinBox, QSplitter, QStackedWidget, QStyle,
    QSystemTrayIcon, QVBoxLayout, QWidget, QColorDialog, QScrollArea, QShortcut,
)

try:  # 音效播放首选 QtMultimedia（支持音量、可并发、低延迟）
    from PyQt5.QtMultimedia import QSoundEffect
    _HAS_QSOUND = True
except Exception:  # pragma: no cover - 极少见（未装 PyQt5-multimedia）
    QSoundEffect = None  # type: ignore
    _HAS_QSOUND = False

try:
    import winsound  # 回退播放方案
    _HAS_WINSOUND = True
except Exception:  # pragma: no cover - 非 Windows
    winsound = None  # type: ignore
    _HAS_WINSOUND = False

try:
    import winreg  # 读取系统深浅色主题
    _HAS_WINREG = True
except Exception:  # pragma: no cover - 非 Windows
    winreg = None  # type: ignore
    _HAS_WINREG = False

APP_NAME = "DesktopDial"
APP_TITLE = "桌面圆盘启动器"


# ---------------------------------------------------------------- 运行期路径
# 打包（PyInstaller）后 sys._MEIPASS 是只读资源根目录，程序目录也可能是
# Program Files 等不可写位置，因此资源与用户数据必须分开定位：
#   资源（sounds/、ico 等）-> resource_dir()
#   用户数据（配置、日志、音效缓存）-> data_dir()
def is_frozen() -> bool:
    """打包器（PyInstaller 等）冻结运行时为 True。"""
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> str:
    """exe（冻结）或脚本（源码）所在目录。"""
    if is_frozen():
        try:
            exe = os.path.abspath(sys.executable)
            if exe and os.path.isdir(os.path.dirname(exe)):
                return os.path.dirname(exe)
        except (OSError, ValueError):
            pass
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir() -> str:
    """只读资源根目录：冻结时 sys._MEIPASS，源码运行时为脚本目录。"""
    if is_frozen():
        base = ""
        try:
            base = getattr(sys, "_MEIPASS", "") or ""
        except (AttributeError, ValueError):
            base = ""
        if base and os.path.isdir(base):
            return base
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts: str) -> str:
    """拼接只读资源路径，例如 resource_path("sounds")。"""
    return os.path.join(resource_dir(), *parts)


def _dir_writable(path: str) -> bool:
    """真实写一个临时文件来判断目录是否可写（临时文件由 NamedTemporaryFile 自行清理）。"""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write_probe_", suffix=".tmp") as fh:
            fh.write(b"ok")
            fh.flush()
        return True
    except OSError:
        return False


_DATA_DIR_CACHE = ""


def data_dir() -> str:
    """可写数据目录：优先 exe/脚本同目录，不可写则 %APPDATA%\\DesktopDial\\。"""
    global _DATA_DIR_CACHE
    if _DATA_DIR_CACHE:
        return _DATA_DIR_CACHE
    cand = _exe_dir()
    target = cand if _dir_writable(cand) else ""
    if not target:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        alt = os.path.join(base, APP_NAME)
        target = alt if _dir_writable(alt) else cand
    _DATA_DIR_CACHE = target
    return target


def sound_cache_dir() -> str:
    """音效音量缩放缓存目录（必须可写，绝不能落在 sys._MEIPASS）。"""
    d = os.path.join(data_dir(), "sound_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d

# ---------------------------------------------------------------- Win32 常量
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B
VK_TAB = 0x09
VK_OEM_4 = 0xDB      # [ 切换上一环
VK_OEM_6 = 0xDD      # ] 切换下一环
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

DESKTOP_CLASSES = {"Progman", "WorkerW"}
# 桌面图标列表窗口：铺满整个屏幕，**必须**用 LVM_HITTEST 区分「空白」与「图标」
# 早期版本直接按类名判否，导致真实桌面任何位置都被判为"非空白"，圆盘永远不弹（已修复）
DESKTOP_ICON_CLASSES = {"SysListView32", "SysTreeView32"}

# ListView 消息
LVM_FIRST = 0x1000
LVM_HITTEST = LVM_FIRST + 18
LVHT_NOWHERE = 0x00000001

SMTO_ABORTIFHUNG = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04

log = logging.getLogger(APP_NAME)
# 置 DIAL_TRACE=1 可把每一次按下/拖动判定写入日志，便于排查"为什么不弹"
TRACE = os.environ.get("DIAL_TRACE", "") in ("1", "true", "yes", "on")


# ================================================================ Win32 绑定
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.PostThreadMessageW.argtypes = [wt.DWORD, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetCurrentThreadId.restype = wt.DWORD

# 全局热键
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetProcessDPIAware.restype = wt.BOOL

# 跨进程 LVM_HITTEST 所需绑定
user32.SendMessageTimeoutW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM,
                                       ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = LRESULT
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                    wt.DWORD, wt.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
kernel32.VirtualFreeEx.restype = wt.BOOL
kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = wt.BOOL
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wt.BOOL


class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG),
                ("flags", ctypes.c_uint), ("iItem", ctypes.c_int),
                ("iSubItem", ctypes.c_int), ("iGroup", ctypes.c_int)]


def _class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(hwnd, buf, 256) <= 0:
        return ""
    return buf.value


def is_desktop_point(x: int, y: int) -> bool:
    """判断物理坐标 (x, y) 是否落在桌面（壁纸区域）。

    通过 WindowFromPoint 拿到该点最上层窗口，再沿途向上回溯父窗口，
    只要祖链上出现 Progman / WorkerW 即认为是桌面。
    其他程序窗口（哪怕全屏铺满）都会返回自己的类名，从而不会被误判。
    """
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return False
    node = hwnd
    for _ in range(10):
        if not node:
            break
        cn = _class_name(node)
        if cn in DESKTOP_CLASSES:
            return True
        node = user32.GetParent(node)
    root = user32.GetAncestor(hwnd, GA_ROOT)
    return _class_name(root) in DESKTOP_CLASSES


def _listview_hit_item(lv_hwnd: int, x: int, y: int) -> Optional[int]:
    """跨进程对桌面图标列表做 LVM_HITTEST。

    返回命中的项索引（>=0 表示点到了某个图标），-1 表示空白处；
    无法判定（打不开进程、内存分配失败、消息超时等）返回 None。
    注意：必须在钩子线程内快速返回，故使用 SendMessageTimeoutW 限时。
    """
    if not lv_hwnd:
        return None
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(lv_hwnd), ctypes.byref(pid))
    if not pid.value:
        return None
    hproc = kernel32.OpenProcess(
        PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION,
        False, pid.value)
    if not hproc:
        log.debug("LVM_HITTEST: OpenProcess(%d) 失败 err=%d", pid.value, ctypes.get_last_error())
        return None
    size = ctypes.sizeof(LVHITTESTINFO)
    remote = kernel32.VirtualAllocEx(hproc, None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not remote:
        kernel32.CloseHandle(hproc)
        return None
    try:
        pt = POINT(int(x), int(y))
        user32.ScreenToClient(ctypes.c_void_p(lv_hwnd), ctypes.byref(pt))
        info = LVHITTESTINFO(pt.x, pt.y, 0, -1, -1, -1)
        written = ctypes.c_size_t(0)
        if not kernel32.WriteProcessMemory(hproc, ctypes.c_void_p(remote), ctypes.byref(info),
                                           size, ctypes.byref(written)):
            return None
        result = ctypes.c_size_t(0)
        ok = user32.SendMessageTimeoutW(ctypes.c_void_p(lv_hwnd), LVM_HITTEST, 0,
                                        ctypes.cast(remote, ctypes.c_void_p).value or 0,
                                        SMTO_ABORTIFHUNG, 60, ctypes.byref(result))
        if not ok:
            log.debug("LVM_HITTEST: SendMessageTimeout 超时/失败")
            return None
        read = ctypes.c_size_t(0)
        if not kernel32.ReadProcessMemory(hproc, ctypes.c_void_p(remote), ctypes.byref(info),
                                          size, ctypes.byref(read)):
            return None
        return int(info.iItem)
    finally:
        kernel32.VirtualFreeEx(hproc, ctypes.c_void_p(remote), 0, MEM_RELEASE)
        kernel32.CloseHandle(hproc)


def _ancestor_chain(hwnd: int, limit: int = 10) -> List[int]:
    """从 hwnd 向上回溯父窗口祖链（含自身）。"""
    chain: List[int] = []
    node = hwnd
    for _ in range(limit):
        if not node:
            break
        chain.append(int(node))
        node = user32.GetParent(ctypes.c_void_p(node))
    return chain


def is_blank_desktop_point(x: int, y: int) -> bool:
    """桌面「空白」区域判定：必须落在桌面（壁纸）上，且没有压到桌面图标。

    关键点：真实桌面上 WindowFromPoint 命中的通常不是 Progman/WorkerW，而是
    铺满全屏的图标层 SysListView32。因此这里不能按类名一票否决，而要：
      1) 沿祖链确认属于 Progman/WorkerW（是桌面）；
      2) 若祖链里出现图标列表窗口，则对它做 LVM_HITTEST：
         - 命中图标（iItem >= 0）-> 判否，把拖拽还给系统图标操作；
         - 空白（iItem == -1）  -> 判是；
         - 无法判定           -> 放行（否则在无法跨进程时圆盘会彻底失效）。
    """
    if not is_desktop_point(x, y):
        return False
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    for node in _ancestor_chain(hwnd):
        cn = _class_name(node)
        if cn in DESKTOP_ICON_CLASSES:
            item = _listview_hit_item(node, int(x), int(y))
            if item is None:
                log.debug("桌面图标命中测试不可用，按空白放行 (%d,%d)", x, y)
                return True
            return item < 0
    # 没有图标层参与（隐藏桌面图标 / 壁纸 WorkerW 等）即为空白
    return True


# ================================================================ 默认配置
DEFAULT_ITEMS: List[Dict[str, Any]] = [
    {"label": "记事本", "type": "app", "target": r"C:\Windows\System32\notepad.exe", "args": ""},
    {"label": "计算器", "type": "app", "target": r"C:\Windows\System32\calc.exe", "args": ""},
    {"label": "任务管理器", "type": "app", "target": r"C:\Windows\System32\taskmgr.exe", "args": ""},
    {"label": "我的电脑", "type": "app", "target": r"C:\Windows\explorer.exe", "args": "shell:MyComputerFolder"},
    {"label": "下载", "type": "folder", "target": r"%USERPROFILE%\Downloads"},
    {"label": "文档", "type": "folder", "target": r"%USERPROFILE%\Documents"},
    {"label": "桌面", "type": "folder", "target": r"%USERPROFILE%\Desktop"},
    {"label": "必应搜索", "type": "url", "target": "https://www.bing.com"},
]

CONFIG_VERSION = 2

DEFAULT_RING2_ITEMS: List[Dict[str, Any]] = [
    {"label": "图片", "type": "folder", "target": r"%USERPROFILE%\Pictures"},
    {"label": "音乐", "type": "folder", "target": r"%USERPROFILE%\Music"},
    {"label": "下载", "type": "folder", "target": r"%USERPROFILE%\Downloads"},
    {"label": "文档", "type": "folder", "target": r"%USERPROFILE%\Documents"},
    {"label": "必应", "type": "url", "target": "https://www.bing.com"},
    {"label": "GitHub", "type": "url", "target": "https://github.com"},
    {"label": "设置", "type": "app", "target": r"C:\Windows\System32\control.exe", "args": ""},
    {"label": "回收站", "type": "app", "target": r"C:\Windows\explorer.exe", "args": "shell:RecycleBinFolder"},
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": CONFIG_VERSION,  # 配置结构版本（1=旧的扁平 items；2=多环 rings）
    "sectors": 8,             # 扇区数量（4 / 6 / 8 / 12 视觉效果最佳）
    "radius": 168,            # 外半径（逻辑像素）
    "inner_radius": 52,       # 内半径（中心圆盘，死区）
    "icon_size": 32,          # 图标边长
    "font_size": 10,          # 标签字号
    "drag_threshold": 12,     # 唤起阈值：按下后拖动超过多少像素才弹出
    "cancel_margin": 70,      # 取消余量：超出 radius + cancel_margin 视为取消
    "dead_zone_ratio": 0.5,   # 死区半径 = inner_radius * 该值
    # 注意：低层钩子吞掉 WM_MOUSEMOVE 会让系统根本不移动光标（指针被冻结在锚点），
    # 用户无法把指针"甩"向扇区，方向判定永远停在死区、松手也取不到真实坐标。
    # 因此该项默认关闭；即使配置写成 true 也会在启动时被忽略并写日志。
    "swallow_move_events": False,  # [已废弃] 圆盘激活时吞鼠标移动（会冻结光标，勿开启）
    # 深浅色：follow_system=true 时按系统主题取调色板；
    # colors 里写 "custom": true 可强制保留自定义配色（不被系统主题覆盖）。
    "theme": {
        "follow_system": True,
        "mode": "auto",        # auto | dark | light
    },
    "colors": {
        "base": "#1B1F2A",
        "base_alpha": 205,
        "empty_alpha": 60,
        "accent": "#4FC3F7",
        "accent_alpha": 225,
        "text": "#EAF1F8",
        "warn": "#FF8A65",
    },
    # 音效套件（wav 由程序合成，见 sounds/ 目录）
    "sound": {
        "enabled": True,
        "theme": "glass",        # glass | mechanical | minimal
        "volume": 0.55,          # 0.0 ~ 1.0
        "mute": False,
        "backend": "auto",       # auto | async | qsound（async=winsound 非阻塞，默认）
        "hover_pitch": True,     # 悬停音高随扇区序号递变
        "hover_semitone": 1.0,   # 每个扇区升多少半音
    },
    # 全局热键唤起
    "hotkey": {
        "enabled": True,
        "sequence": "Ctrl+Alt+Space",   # 例：Ctrl+Alt+Space / Ctrl+Shift+F12
    },
    # 动画
    "animation": {
        "enabled": True,
        "expand_ms": 170,
        "collapse_ms": 130,
        "hover_ms": 110,
        "sector_gain": 1.10,     # 选中扇区放大倍数（仅径向放大，不改角度映射）
    },
    # 外观（磨砂玻璃质感相关，全部可热更新）
    "appearance": {
        "frosted": True,        # 磨砂玻璃质感（半透明 + 内高光 + 柔和边缘）
        "glass_alpha": 0.80,    # 玻璃底色不透明度（0~1，按 base 色推导）
        "gap_px": 3.2,          # 扇区之间的间隙（像素，形成圆角缝隙）
        "sector_radius": 9.0,   # 扇区圆角（像素）
        "hub_name": True,       # 中心显示当前环名
        "glow": True,           # 选中扇区外发光
        "icon_shadow": True,    # 图标底部投影（提升质感）
    },
    # 多环
    "ring_gap": 6,              # 多环同时显示时的径向间隙（预留，当前版本单环展示）
    "switch_wheel": True,       # 滚轮切环
    "switch_keys": True,        # [ ] / Tab 切环
    "rings": [
        {"id": "ring1", "name": "圆环 1", "accent": "", "icon_size": 0,
         "items": DEFAULT_ITEMS},
        {"id": "ring2", "name": "工具盘", "accent": "", "icon_size": 0,
         "items": DEFAULT_RING2_ITEMS},
    ],
    "current_ring": 0,
    # 设置窗界面状态（由 SettingsDialog 维护，与功能配置互不影响）：
    #   geometry  = base64(Qt QWidget.saveGeometry())，恢复时做屏幕边界校验
    #   maximized = 上次关闭时是否最大化
    #   nav_width = 左侧导航栏宽度；splitter = 启动器页三栏宽度
    "ui": {
        "geometry": "",
        "maximized": False,
        "nav_width": 168,
        "splitter": [],
    },
    # 兼容旧接口：始终等于当前环的槽位列表（load_config/切换环时同步维护）
    "items": DEFAULT_ITEMS,
}


# ---------------------------------------------------------------- 配置结构与迁移
ITEM_TYPES = ("app", "file", "folder", "url", "empty")
TYPE_LABELS = {"app": "程序", "file": "文件", "folder": "文件夹", "url": "网址",
               "empty": "空槽位"}
_TYPE_ALIAS = {
    "exe": "app", "program": "app", "application": "app", "uwp": "app",
    "lnk": "file", "shortcut": "file", "doc": "file",
    "dir": "folder", "directory": "folder",
    "web": "url", "website": "url", "link": "url", "http": "url",
    "": "empty", "blank": "empty", "none": "empty",
}
MAX_RINGS = 8


def normalize_type(value: Any) -> str:
    t = str(value or "").strip().lower()
    t = _TYPE_ALIAS.get(t, t)
    return t if t in ITEM_TYPES else "app"


def normalize_item(raw: Any, slot: int = 0) -> Dict[str, Any]:
    """把任意来源的槽位数据收敛成统一结构，抗坏数据（None / 字符串 / 缺字段）。"""
    if not isinstance(raw, dict):
        return {"label": "", "type": "empty", "target": "", "args": "",
                "icon": "", "hotkey": ""}
    kind = normalize_type(raw.get("type"))
    label = str(raw.get("label", "") or "")[:60]
    target = str(raw.get("target", "") or "").strip()
    if kind == "empty" and target:
        kind = "app"
    if kind != "empty" and not target and not label:
        kind = "empty"
    return {
        "label": label,
        "type": kind,
        "target": target,
        "args": str(raw.get("args", "") or ""),
        "icon": str(raw.get("icon", "") or ""),
        "hotkey": str(raw.get("hotkey", "") or ""),
        "slot": slot,
    }


def new_ring(index: int, name: str = "") -> Dict[str, Any]:
    return {
        "id": "ring%d" % (index + 1),
        "name": name or ("圆环 %d" % (index + 1)),
        "accent": "",
        "icon_size": 0,
        "items": [normalize_item({}, i) for i in range(8)],
    }


def normalize_ring(raw: Any, index: int, sectors: int) -> Dict[str, Any]:
    """单个环的容错收敛：名字/配色/图标尺寸/槽位数量与类型全部规范化。"""
    ring = new_ring(index)
    if isinstance(raw, dict):
        ring["id"] = str(raw.get("id", "") or ring["id"])
        ring["name"] = str(raw.get("name", "") or "").strip()[:40] or ring["name"]
        ring["accent"] = str(raw.get("accent", "") or "")
        try:
            ring["icon_size"] = max(0, min(96, int(raw.get("icon_size", 0) or 0)))
        except (TypeError, ValueError):
            ring["icon_size"] = 0
        items = raw.get("items")
    else:
        items = None
    src_items = items if isinstance(items, list) else []
    out = [normalize_item(it, i) for i, it in enumerate(src_items[:sectors])]
    if len(out) < sectors:
        out += [normalize_item({}, i) for i in range(len(out), sectors)]
    ring["items"] = out
    return ring


def sync_items(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """把 cfg["items"] 同步成当前环的槽位（保持旧接口/旧脚本可用）。"""
    ring = active_ring(cfg)
    cfg["items"] = list(ring.get("items") or [])
    return cfg


def ring_list(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    rings = cfg.get("rings")
    return rings if isinstance(rings, list) else []


def ring_index(cfg: Dict[str, Any]) -> int:
    rings = ring_list(cfg)
    if not rings:
        return 0
    try:
        idx = int(cfg.get("current_ring", 0))
    except (TypeError, ValueError):
        idx = 0
    return max(0, min(len(rings) - 1, idx))


def active_ring(cfg: Dict[str, Any]) -> Dict[str, Any]:
    rings = ring_list(cfg)
    if not rings:
        cfg["rings"] = rings = [normalize_ring(DEFAULT_CONFIG["rings"][0], 0, 8)]
        cfg["current_ring"] = 0
    return rings[ring_index(cfg)]


def active_items(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(active_ring(cfg).get("items") or [])


def set_active_ring(cfg: Dict[str, Any], index: int) -> Dict[str, Any]:
    rings = ring_list(cfg)
    if not rings:
        return cfg
    cfg["current_ring"] = max(0, min(len(rings) - 1, int(index)))
    return sync_items(cfg)


def normalize_config(raw: Any) -> Tuple[Dict[str, Any], List[str], bool]:
    """把任意版本的配置收敛为当前版本，返回 (配置, 迁移说明, 是否需要回写)。"""
    notes: List[str] = []
    migrated = False
    if not isinstance(raw, dict):
        if raw is not None:
            notes.append("配置根节点不是对象，已忽略")
        raw = {}

    # 非 rings / items 的键先按默认值深合并（坏值通过下面逐项校验兜住）
    base = {k: v for k, v in raw.items() if k not in ("rings", "items")}
    cfg = deep_merge(DEFAULT_CONFIG, base)
    cfg.pop("items", None)

    try:
        sectors = int(cfg.get("sectors", 8))
    except (TypeError, ValueError):
        sectors = 8
    sectors = max(3, min(16, sectors))
    cfg["sectors"] = sectors

    rings_raw = raw.get("rings")
    legacy_items = raw.get("items")
    if isinstance(rings_raw, list) and rings_raw:
        rings = [normalize_ring(r, i, sectors) for i, r in enumerate(rings_raw)]
    elif isinstance(legacy_items, list) and legacy_items:
        # 旧版结构：8 条扁平槽位 -> 迁移为「圆环 1」，一个都不能丢
        rings = [normalize_ring({"name": "圆环 1", "items": legacy_items}, 0, sectors)]
        migrated = True
        notes.append("旧版扁平 items 已迁移为「圆环 1」（%d 项）" % len(legacy_items))
    else:
        rings = [normalize_ring(r, i, sectors) for i, r in enumerate(DEFAULT_CONFIG["rings"])]

    if len(rings) > MAX_RINGS:
        rings = rings[:MAX_RINGS]
        notes.append("圆环数量超过上限 %d，已截断" % MAX_RINGS)
    cfg["rings"] = rings

    try:
        cur = int(raw.get("current_ring", cfg.get("current_ring", 0)))
    except (TypeError, ValueError):
        cur = 0
    cfg["current_ring"] = max(0, min(len(rings) - 1, cur))

    try:
        ver = int(raw.get("version", 1))
    except (TypeError, ValueError):
        ver = 1
    if ver < CONFIG_VERSION or not isinstance(rings_raw, list):
        migrated = True
    cfg["version"] = CONFIG_VERSION
    sync_items(cfg)
    return cfg, notes, migrated


def save_config(path: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """原子写入配置（先写临时文件再替换），并保留一份 .bak。"""
    try:
        out = {k: v for k, v in cfg.items() if k != "items"}
        out["version"] = CONFIG_VERSION
        text = json.dumps(out, ensure_ascii=False, indent=2)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        if os.path.isfile(path):
            try:
                shutil.copyfile(path, path + ".bak")
            except OSError:
                pass
        os.replace(tmp, path)
        log.info("配置已保存 %s（%d 个环）", path, len(ring_list(cfg)))
        return True, "已保存"
    except (OSError, TypeError, ValueError) as exc:
        log.error("保存配置失败 %s: %r", path, exc)
        return False, str(exc)


# ---------------------------------------------------------------- 深浅色调色板
PALETTE_DARK: Dict[str, Any] = {
    "base": "#1B1F2A", "base_alpha": 205, "empty_alpha": 60,
    "accent": "#4FC3F7", "accent_alpha": 225, "text": "#EAF1F8", "warn": "#FF8A65",
}
PALETTE_LIGHT: Dict[str, Any] = {
    "base": "#F7F9FC", "base_alpha": 236, "empty_alpha": 74,
    "accent": "#0B84C4", "accent_alpha": 214, "text": "#1B2430", "warn": "#D9542B",
}

APPS_USE_LIGHT_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def system_theme_mode() -> str:
    """读注册表判断系统是深色还是浅色；读不到时返回 'dark'（与本程序默认一致）。"""
    if not _HAS_WINREG:
        return "dark"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPS_USE_LIGHT_KEY) as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return "light" if int(val) else "dark"
    except OSError:
        return "dark"


def resolve_colors(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """按配置 + 系统主题算出最终调色板，返回 (colors, 说明)。"""
    colors = dict(cfg.get("colors") or {})
    theme = cfg.get("theme") or {}
    mode = str(theme.get("mode", "auto")).lower()
    follow = bool(theme.get("follow_system", True))
    custom = bool(colors.get("custom", False))
    if mode == "auto":
        sys_mode = system_theme_mode()
    else:
        sys_mode = "light" if mode == "light" else "dark"
    if not follow or custom:
        return colors, "自定义配色（不跟随系统）"
    pal = dict(PALETTE_LIGHT if sys_mode == "light" else PALETTE_DARK)
    pal["custom"] = False
    return pal, "跟随系统主题：%s" % ("浅色" if sys_mode == "light" else "深色")


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config_path_default() -> str:
    """配置文件默认位置：可写目录下的 desktop_dial_config.json。

    冻结/源码运行都优先放在 exe（或脚本）同目录；不可写时退到
    %APPDATA%\\DesktopDial\\（绝不写 sys._MEIPASS）。旧版配置在程序同目录，
    首次迁移时拷贝一份过去并保留 desktop_dial_config.json.bak。
    """
    target = os.path.join(data_dir(), "desktop_dial_config.json")
    legacy = os.path.join(_exe_dir(), "desktop_dial_config.json")
    try:
        same = os.path.abspath(target) == os.path.abspath(legacy)
        if not same and os.path.isfile(legacy) and not os.path.isfile(target):
            shutil.copyfile(legacy, target)
            try:
                shutil.copyfile(legacy, target + ".bak")
            except OSError:
                pass
            log.info("已迁移旧配置 %s -> %s（原文件保留，另存 .bak）", legacy, target)
    except OSError as exc:
        log.warning("迁移旧配置失败：%r", exc)
    return target


def load_config(path: str) -> Tuple[Dict[str, Any], str]:
    """读取配置并按当前版本做迁移与容错；坏配置绝不抛异常导致启动崩溃。"""
    if not os.path.isfile(path):
        cfg, _notes, _mig = normalize_config({})
        ok, msg = save_config(path, cfg)
        return cfg, ("已生成默认配置（2 个圆环）" if ok else "写入默认配置失败（%s），使用内置默认值" % msg)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        log.error("读取配置失败 %s: %s", path, exc)
        cfg, _notes, _mig = normalize_config({})
        return cfg, "配置解析失败，已回退到内置默认值"
    try:
        cfg, notes, migrated = normalize_config(raw)
    except Exception as exc:  # 任何结构异常都回退，保证程序能起来
        log.exception("配置结构异常：%r", exc)
        cfg, _notes, _mig = normalize_config({})
        return cfg, "配置结构异常，已回退到内置默认值"
    status = "已读取配置"
    if notes:
        status += "（" + "；".join(notes) + "）"
    if migrated:
        ok, _msg = save_config(path, cfg)
        status += ("；已按 v%d 结构回写" % CONFIG_VERSION) if ok else "；结构回写失败"
    return cfg, status



# ================================================================ 全局热键
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB10C  # 进程内唯一即可

_MOD_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
    "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN,
}

_VK_NAMES: Dict[str, int] = {
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}


def parse_hotkey(seq: str) -> Tuple[int, int, str]:
    """把 'Ctrl+Alt+Space' 解析成 (modifiers, vk, 错误说明)。"""
    parts = [p.strip() for p in str(seq or "").replace("＋", "+").split("+") if p.strip()]
    if not parts:
        return 0, 0, "热键为空"
    mods = 0
    key = ""
    for p in parts:
        low = p.lower()
        if low in _MOD_NAMES:
            mods |= _MOD_NAMES[low]
        elif re.fullmatch(r"[a-z]", low):
            key = low.upper()
        elif re.fullmatch(r"[0-9]", low):
            key = low
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low):
            key = low.upper()
        elif low in _VK_NAMES:
            key = low
        else:
            return 0, 0, "无法识别的按键：%s" % p
    if not key:
        return 0, 0, "缺少主键（如 Space / F12）"
    if key in _VK_NAMES:
        vk = _VK_NAMES[key]
    elif re.fullmatch(r"[A-Z0-9]", key):
        vk = ord(key)
    else:  # F1..F24
        vk = 0x70 + int(key[1:]) - 1
    if mods == 0 and vk < 0x70:
        return 0, 0, "全局热键至少需要一个修饰键（Ctrl/Alt/Shift/Win）"
    return mods, vk, ""


class HotkeyBridge(QObject):
    """热键线程 -> 主线程。"""
    pressed = pyqtSignal()
    status = pyqtSignal(bool, str)


class HotkeyThread(threading.Thread):
    """RegisterHotKey + 独立消息循环，热键触发后通过信号回到主线程。"""

    def __init__(self, bridge: HotkeyBridge, sequence: str) -> None:
        super().__init__(name="dial-hotkey", daemon=True)
        self.bridge = bridge
        self.sequence = sequence
        self.thread_id = 0
        self.registered = False
        self.error = ""
        self.ready = threading.Event()

    def run(self) -> None:
        self.thread_id = int(kernel32.GetCurrentThreadId())
        mods, vk, err = parse_hotkey(self.sequence)
        if err:
            self.error = err
            self.ready.set()
            self.bridge.status.emit(False, "热键配置无效：%s" % err)
            log.error("热键解析失败 %r -> %s", self.sequence, err)
            return
        ok = user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk)
        self.registered = bool(ok)
        if not ok:
            code = ctypes.get_last_error()
            self.error = "RegisterHotKey 失败 err=%s（可能被其他程序占用）" % code
            log.error("热键注册失败 sequence=%s mods=0x%X vk=0x%X err=%s",
                      self.sequence, mods, vk, code)
        else:
            log.info("热键注册成功 sequence=%s mods=0x%X vk=0x%X tid=%s",
                     self.sequence, mods, vk, self.thread_id)
        self.ready.set()
        self.bridge.status.emit(self.registered, self.error)
        if not self.registered:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                log.info("热键触发 WM_HOTKEY id=%s", msg.wParam)
                self.bridge.pressed.emit()
        user32.UnregisterHotKey(None, HOTKEY_ID)
        log.info("热键已注销")

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


# ================================================================ 音效套件
SOUND_EVENTS = ("show", "hover", "select", "cancel", "hide")


class SoundKit(QObject):
    """音效套件播放器：3 套主题 × 5 类事件，hover 音高随扇区序号递变。

    wav 全部由 temp/gen_sounds.py 用 numpy 合成（非下载素材）。

    播放后端（sound.backend，默认 "auto"）：
      * "async"（Windows 默认）：winsound.PlaySound(SND_FILENAME|SND_ASYNC)。
        winmm 自己起线程读盘解码，调用方只做一次 P/Invoke——实测中位 0.12ms、
        P95 0.17ms，绝不阻塞 UI 线程。音量通过「启动时预生成按音量缩放好的
        wav 缓存文件」实现（等价于音量设置，保留 sound.volume 语义）。
      * "qsound"：QSoundEffect。可运行时直接改音量，但首次 setSource 要解码，
        实测单文件同步阻塞 220ms+（切 source 更慢），会直接卡住 UI 线程，
        因此只作为可选后端保留，不再是默认值。
    """

    def __init__(self, base_dir: str, cfg: Dict[str, Any]) -> None:
        super().__init__()
        self.base_dir = base_dir
        self._effects: Dict[str, Any] = {}          # qsound 后端专用
        self._scaled: Dict[str, str] = {}           # 源 wav -> 已缩放缓存 wav
        self._cache_sig: Tuple[Any, ...] = ()
        self._cache_dir = sound_cache_dir()
        self.warm_ms = 0.0
        self.play_count = 0
        self.history: List[Dict[str, Any]] = []
        self.backend = "none"
        self._inited = False
        self.set_config(cfg)
        self._inited = True
        self._select_backend()
        self._refresh_scaled_cache()

    # -------------------------------------------------- 配置
    def set_config(self, cfg: Dict[str, Any]) -> None:
        pack = cfg.get("sound") or {}
        self.enabled = bool(pack.get("enabled", True))
        theme = str(pack.get("theme", "glass")).lower()
        self.theme = theme if os.path.isdir(os.path.join(self.base_dir, theme)) else "glass"
        try:
            self.volume = max(0.0, min(1.0, float(pack.get("volume", 0.55))))
        except (TypeError, ValueError):
            self.volume = 0.55
        self.mute = bool(pack.get("mute", False))
        self.backend_pref = str(pack.get("backend", "auto")).lower()
        self.hover_pitch = bool(pack.get("hover_pitch", True))
        try:
            self.hover_semitone = float(pack.get("hover_semitone", 1.0))
        except (TypeError, ValueError):
            self.hover_semitone = 1.0
        self._apply_volume()
        if getattr(self, "_inited", False):
            self._select_backend()
            self._refresh_scaled_cache()

    # -------------------------------------------------- 后端与音量缓存
    def _select_backend(self) -> None:
        want = self.backend_pref if self.backend_pref in ("auto", "async", "qsound") else "auto"
        if want == "auto":
            want = "async" if _HAS_WINSOUND else ("qsound" if _HAS_QSOUND else "none")
        if want == "async" and not _HAS_WINSOUND:
            want = "qsound" if _HAS_QSOUND else "none"
        if want == "qsound" and not _HAS_QSOUND:
            want = "async" if _HAS_WINSOUND else "none"
        if want != self.backend:
            log.info("SOUND backend -> %s（配置=%s）", want, self.backend_pref)
        self.backend = want

    def _scale_pcm(self, frames: bytes, volume: float) -> bytes:
        """按音量缩放 16bit PCM（音效素材均为 16bit 合成音）。"""
        if volume >= 0.995:
            return frames
        try:
            import audioop  # 3.11 可用；3.13 起被移除，走下面的兜底
            return audioop.mul(frames, 2, max(0.0, volume))
        except Exception:
            pass
        import array
        try:
            a = array.array("h")
            a.frombytes(frames)
            k = max(0.0, volume)
            for i, v in enumerate(a):
                a[i] = int(max(-32768, min(32767, v * k)))
            return a.tobytes()
        except Exception:  # pragma: no cover
            return frames

    def _refresh_scaled_cache(self) -> None:
        """为当前主题 + 当前音量预生成缩放后的 wav 缓存。

        只在启动 / 换主题 / 改音量时跑一次（实测 20 个文件合计 < 30ms），
        目的是把「音量」从播放热路径移出去：播放退化为一次 P/Invoke，
        不再有读盘 + 采样缩放的成本。
        """
        if self.backend != "async":
            self._scaled = {}
            self.warm_ms = 0.0
            return
        vol = self.effective_volume()
        sig = (self.theme, round(vol, 3))
        if sig == self._cache_sig and self._scaled:
            return
        t0 = time.perf_counter()
        mapping: Dict[str, str] = {}
        folder = os.path.join(self._cache_dir, "%s_v%03d" % (self.theme, int(round(vol * 100))))
        src_dir = os.path.join(self.base_dir, self.theme)
        try:
            os.makedirs(folder, exist_ok=True)
            names = [n for n in os.listdir(src_dir) if n.lower().endswith(".wav")]
        except OSError:
            log.warning("SOUND 缓存目录不可用：%s", folder)
            return
        import io
        import wave
        for name in names:
            src = os.path.join(src_dir, name)
            dst = os.path.join(folder, name)
            try:
                if vol >= 0.995:
                    mapping[src] = src
                    continue
                if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
                    mapping[src] = dst
                    continue
                with wave.open(src, "rb") as w:
                    params = w.getparams()
                    frames = w.readframes(w.getnframes())
                if params.sampwidth != 2:
                    mapping[src] = src
                    continue
                buf = io.BytesIO()
                with wave.open(buf, "wb") as o:
                    o.setparams(params)
                    o.writeframes(self._scale_pcm(frames, vol))
                with open(dst, "wb") as f:
                    f.write(buf.getvalue())
                mapping[src] = dst
            except Exception:
                log.debug("SOUND 缩放失败 %s", src, exc_info=True)
                mapping[src] = src
        self._scaled = mapping
        self._cache_sig = sig
        self.warm_ms = (time.perf_counter() - t0) * 1000.0
        log.info("SOUND warm theme=%s vol=%.2f files=%d 耗时=%.1fms cache=%s",
                 self.theme, vol, len(mapping), self.warm_ms, folder)

    def _apply_volume(self) -> None:
        for eff in self._effects.values():
            try:
                eff.setVolume(self.effective_volume())
            except Exception:
                pass

    def effective_volume(self) -> float:
        return 0.0 if (self.mute or not self.enabled) else self.volume

    def themes(self) -> List[str]:
        try:
            return sorted(d for d in os.listdir(self.base_dir)
                          if os.path.isdir(os.path.join(self.base_dir, d)))
        except OSError:
            return []

    # -------------------------------------------------- 文件
    def hover_level(self, index: int) -> int:
        """扇区序号 -> hover 采样档位（0..15，按半音递变）。"""
        if not self.hover_pitch or index is None or index < 0:
            return 0
        step = max(0.0, self.hover_semitone)
        return int(round(min(15, max(0, index * step))))

    def _wav(self, event: str, index: Optional[int] = None) -> str:
        if event == "hover":
            return os.path.join(self.base_dir, self.theme, "hover_%02d.wav" % self.hover_level(index))
        return os.path.join(self.base_dir, self.theme, "%s.wav" % event)

    def _effect(self, path: str):
        eff = self._effects.get(path)
        if eff is not None:
            return eff
        if not _HAS_QSOUND:
            return None
        eff = QSoundEffect(self)
        eff.setSource(QUrl.fromLocalFile(path))
        eff.setVolume(self.effective_volume())
        eff.setLoopCount(1)
        self._effects[path] = eff
        return eff

    # -------------------------------------------------- 播放
    def _target_path(self, path: str) -> str:
        """源 wav -> 实际播放路径（async 后端下优先用预缩放缓存）。"""
        if self.backend != "async":
            return path
        return self._scaled.get(path, path)

    def play(self, event: str, index: Optional[int] = None) -> bool:
        if not self.enabled or self.mute:
            log.debug("SOUND skip event=%s（静音或已关闭）", event)
            return False
        path = self._wav(event, index)
        if not os.path.isfile(path):
            log.warning("SOUND missing file=%s", path)
            return False
        t0 = time.perf_counter()
        played = False
        st = "-"
        if self.backend == "async":
            try:
                winsound.PlaySound(self._target_path(path),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC |
                                   winsound.SND_NODEFAULT)
                played = True
                st = "async"
            except Exception as exc:  # pragma: no cover
                log.warning("winsound 播放失败 %r", exc)
        elif self.backend == "qsound":
            try:
                eff = self._effect(path)
                if eff is not None:
                    eff.setVolume(self.effective_volume())
                    eff.play()
                    played = True
                    st = "qsound:%s" % eff.status()
            except Exception as exc:  # pragma: no cover
                log.warning("QSoundEffect 播放失败 %r", exc)
        cost_ms = (time.perf_counter() - t0) * 1000.0
        self.play_count += 1
        rec = {"event": event, "index": index, "file": os.path.basename(path),
               "theme": self.theme, "volume": round(self.effective_volume(), 2),
               "backend": st, "ok": played, "ms": round(cost_ms, 3),
               "t": time.strftime("%H:%M:%S")}
        self.history.append(rec)
        if len(self.history) > 200:
            del self.history[:100]
        # 播放本身必须极快：超过 20ms 说明后端又在同步解码，需要人工介入排查
        if cost_ms > 20.0:
            log.warning("SOUND 播放偏慢 event=%s backend=%s 耗时=%.1fms", event, st, cost_ms)
        log.info("SOUND play event=%s theme=%s file=%s vol=%.2f backend=%s ok=%s %.2fms",
                 event, self.theme, rec["file"], rec["volume"], st, played, cost_ms)
        return played

    # -------------------------------------------------- 状态
    def status(self) -> Dict[str, Any]:
        p = self._wav("show")
        return {
            "enabled": self.enabled, "theme": self.theme, "volume": round(self.volume, 2),
            "mute": self.mute, "backend": self.backend, "backend_pref": self.backend_pref,
            "themes": self.themes(), "warm_ms": round(self.warm_ms, 1),
            "show_wav": os.path.basename(p), "show_exists": os.path.isfile(p),
            "base_dir": self.base_dir, "play_count": self.play_count,
        }


# ================================================================ 快捷方式解析
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str = "{00000000-0000-0000-0000-000000000000}") -> None:
        super().__init__()
        t = text.strip().strip("{}")
        a, b, c, d, e = t.split("-")
        self.Data1 = int(a, 16)
        self.Data2 = int(b, 16)
        self.Data3 = int(c, 16)
        tail = bytes.fromhex(d + e)
        for i, v in enumerate(tail):
            self.Data4[i] = v


_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"
_STGM_READ = 0
_CLSCTX_INPROC_SERVER = 1

ole32 = None
LnK_READY = False
if sys.platform == "win32":
    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_ulong,
                                           ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = ctypes.c_long
        LnK_READY = True
    except Exception:  # pragma: no cover
        LnK_READY = False


def _com_call(ptr: int, index: int, restype, argtypes, *args):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return fn(ptr, *args)


def _com_release(ptr: int) -> None:
    try:
        _com_call(ptr, 2, ctypes.c_ulong, (), )  # IUnknown::Release
    except Exception:
        pass


def resolve_lnk(path: str) -> Optional[Dict[str, str]]:
    """.lnk 解析：返回 {target, args, workdir}；失败返回 None。"""
    if not LnK_READY or not ole32:
        return None
    ppf = ctypes.c_void_p()
    plink = ctypes.c_void_p()
    try:
        ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED，重复初始化无害
        clsid = _GUID(_CLSID_SHELLLINK)
        iid = _GUID(_IID_ISHELLLINKW)
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER,
                                    ctypes.byref(iid), ctypes.byref(plink))
        if hr != 0 or not plink.value:
            log.warning("resolve_lnk CoCreateInstance hr=0x%08X", hr & 0xFFFFFFFF)
            return None
        ppf = ctypes.c_void_p()
        iid_pf = _GUID(_IID_IPERSISTFILE)
        hr = _com_call(plink.value, 0, ctypes.c_long,
                       [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
                       ctypes.byref(iid_pf), ctypes.byref(ppf))
        if hr != 0 or not ppf.value:
            return None
        hr = _com_call(ppf.value, 5, ctypes.c_long, [ctypes.c_wchar_p, ctypes.c_ulong],
                       os.path.abspath(path), _STGM_READ)
        if hr != 0:
            log.warning("resolve_lnk Load hr=0x%08X file=%s", hr & 0xFFFFFFFF, path)
            return None
        buf = ctypes.create_unicode_buffer(1024)
        _com_call(plink.value, 3, ctypes.c_long,
                  [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong],
                  buf, 1024, None, 0)  # IShellLinkW::GetPath
        args_buf = ctypes.create_unicode_buffer(1024)
        _com_call(plink.value, 10, ctypes.c_long, [ctypes.c_wchar_p, ctypes.c_int],
                  args_buf, 1024)  # IShellLinkW::GetArguments
        wd_buf = ctypes.create_unicode_buffer(1024)
        _com_call(plink.value, 8, ctypes.c_long, [ctypes.c_wchar_p, ctypes.c_int],
                  wd_buf, 1024)  # IShellLinkW::GetWorkingDirectory
        return {"target": buf.value, "args": args_buf.value, "workdir": wd_buf.value}
    except Exception as exc:  # pragma: no cover
        log.warning("resolve_lnk 异常 %r", exc)
        return None
    finally:
        if ppf.value:
            _com_release(ppf.value)
        if plink.value:
            _com_release(plink.value)


def resolve_url_shortcut(path: str) -> Optional[str]:
    """解析 .url 网络快捷方式里的 URL= 。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().lower().startswith("url="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def list_uwp_apps(limit: int = 200) -> List[Dict[str, str]]:
    """枚举已安装 UWP 应用的 AUMID（供配置参考）。"""
    out: List[Dict[str, str]] = []
    try:
        ps = ("Get-StartApps | Select-Object -First %d | ForEach-Object { "
              "\"$($_.Name)`t$($_.AppID)\" }" % limit)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=25,
                           creationflags=0x08000000)
        for line in (r.stdout or "").splitlines():
            if "\t" in line:
                name, aumid = line.split("\t", 1)
                out.append({"name": name.strip(), "aumid": aumid.strip()})
    except Exception as exc:  # pragma: no cover
        log.warning("枚举 UWP 失败 %r", exc)
    return out


def launch_shell_target(shell_target: str) -> bool:
    """通过 explorer.exe 打开 shell: / shell:AppsFolder\\<AUMID> 目标。"""
    try:
        subprocess.Popen(["explorer.exe", shell_target],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
        log.info("已通过 explorer 打开: %s", shell_target)
        return True
    except Exception as exc:
        log.error("explorer 打开失败 %s: %r", shell_target, exc)
        return False


# ================================================================ 系统图标（Win32）
# 说明：QFileIconProvider 对 exe 往往只给 16/32px 的小图，直接放大到 40px 会糊。
# 这里补一条 SHGetFileInfoW(SHGFI_SYSICONINDEX) + SHGetImageList(SHIL_EXTRALARGE)
# 的路径，可从系统镜像列表直接取 48×48（jumbo 256 视系统版本而定），再降采样。
SHGFI_ICON = 0x00000100
SHGFI_LARGEICON = 0x00000000
SHGFI_SMALLICON = 0x00000001
SHGFI_SYSICONINDEX = 0x00004000
SHGFI_USEFILEATTRIBUTES = 0x00000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
SHIL_LARGE = 0x00
SHIL_SMALL = 0x01
SHIL_EXTRALARGE = 0x02
SHIL_SYSSMALL = 0x03
SHIL_JUMBO = 0x04
ILD_TRANSPARENT = 0x00000001

_HAS_SHELL = True
try:
    # 注意：这里的图标提取用到独立的 DLL 实例（ic_*），
    # 严禁重新赋值模块级 user32/gdi32/shell32——那会丢掉第 160~200 行配置好的
    # argtypes，导致 SetWindowsHookExW 在 64 位下抛 OverflowError、GetDC 句柄被截断。
    ic_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ic_user32 = ctypes.WinDLL("user32", use_last_error=True)
    ic_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    IID_IImageList = _GUID("{46EB5926-582E-4017-9FDF-E8998DAA0950}")

    ic_shell32.SHGetFileInfoW.restype = ctypes.c_void_p
    ic_shell32.SHGetImageList.restype = ctypes.c_long
    ic_gdi32.GetObjectW.restype = ctypes.c_int
    ic_gdi32.GetDIBits.restype = ctypes.c_int
    ic_gdi32.DeleteObject.restype = wt.BOOL
    ic_user32.GetDC.argtypes = [wt.HWND]
    ic_user32.GetDC.restype = wt.HDC
    ic_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    ic_user32.ReleaseDC.restype = ctypes.c_int
except Exception:  # pragma: no cover - 非 Windows
    ic_shell32 = ic_user32 = ic_gdi32 = None  # type: ignore
    IID_IImageList = None  # type: ignore
    _HAS_SHELL = False

if _HAS_SHELL:
    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [
            ("hIcon", ctypes.c_void_p),
            ("iIcon", ctypes.c_int),
            ("dwAttributes", ctypes.c_uint32),
            ("szDisplayName", ctypes.c_wchar * 260),
            ("szTypeName", ctypes.c_wchar * 80),
        ]


def _shell_icon_index(path: str, is_dir: bool = False) -> int:
    """取得文件在系统图标列表中的索引；失败返回 -1。"""
    if not _HAS_SHELL:
        return -1
    try:
        info = SHFILEINFOW()
        flags = SHGFI_SYSICONINDEX | SHGFI_LARGEICON
        if is_dir or not os.path.exists(path):
            flags |= SHGFI_USEFILEATTRIBUTES
            attr = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_NORMAL
        else:
            attr = 0
        res = ic_shell32.SHGetFileInfoW(ctypes.c_wchar_p(path), ctypes.c_uint32(attr),
                                     ctypes.byref(info), ctypes.sizeof(info),
                                     ctypes.c_uint32(flags))
        if not res:
            return -1
        return int(info.iIcon)
    except Exception:
        log.debug("SHGetFileInfoW failed for %s", path, exc_info=True)
        return -1


def _image_list(which: int):
    """取系统图标镜像列表（IImageList*），失败返回 None。"""
    if not _HAS_SHELL:
        return None
    try:
        ppv = ctypes.c_void_p()
        hr = ic_shell32.SHGetImageList(ctypes.c_int(which),
                                    ctypes.byref(IID_IImageList),
                                    ctypes.byref(ppv))
        if hr != 0 or not ppv.value:
            return None
        return ppv
    except Exception:
        return None


def _release_com(ppv) -> None:
    if not ppv:
        return
    try:
        _com_release(ppv)
    except Exception:
        pass


def _qimage_from_hbitmap(hbmp: int) -> Optional[QImage]:
    """HBITMAP(32bpp 预乘) -> QImage(ARGB32)。"""
    if not _HAS_SHELL or not hbmp:
        return None

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
            ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
            ("bmBits", ctypes.c_void_p),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bm = BITMAP()
    if not ic_gdi32.GetObjectW(ctypes.c_void_p(hbmp), ctypes.sizeof(bm), ctypes.byref(bm)):
        return None
    w, h = int(bm.bmWidth), int(bm.bmHeight)
    if w <= 0 or h <= 0 or w > 512 or h > 512:
        return None
    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = w
    bih.biHeight = -h          # 负数 = top-down，与 QImage 行序一致
    bih.biPlanes = 1
    bih.biBitCount = 32
    bih.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = ic_user32.GetDC(None)
    try:
        got = ic_gdi32.GetDIBits(ctypes.c_void_p(hdc), ctypes.c_void_p(hbmp), ctypes.c_uint(0),
                              ctypes.c_uint(h), buf, ctypes.byref(bih), ctypes.c_uint(0))
    finally:
        ic_user32.ReleaseDC(None, hdc)
    if not got:
        return None
    img = QImage(bytes(buf.raw), w, h, w * 4, QImage.Format_ARGB32)
    return img.copy()


def _icon_from_imagelist(path: str, which: int, is_dir: bool = False) -> Optional[QPixmap]:
    """从系统镜像列表按图标索引取一张位图。"""
    idx = _shell_icon_index(path, is_dir)
    if idx < 0:
        return None
    ppv = _image_list(which)
    if not ppv:
        return None
    hbmp = ctypes.c_void_p()
    try:
        hres = _com_call(ppv, 10, ctypes.c_long, [ctypes.c_int, ctypes.c_void_p],
                         ctypes.c_int(idx), ctypes.byref(hbmp))
        if hres != 0 or not hbmp.value:
            return None
        img = _qimage_from_hbitmap(hbmp.value)
        if img is None or img.isNull():
            return None
        return QPixmap.fromImage(img)
    except Exception:
        return None
    finally:
        if hbmp.value:
            try:
                ic_gdi32.DeleteObject(ctypes.c_void_p(hbmp.value))
            except Exception:
                pass
        _release_com(ppv)


def extract_icon_pixmap(path: str, size: int, is_dir: bool = False) -> Optional[QPixmap]:
    """从 exe / lnk / 文件夹取系统图标（优先大图，逐级降级）。"""
    if not path or not _HAS_SHELL:
        return None
    target = os.path.expandvars(os.path.expanduser(path))
    real = target
    if real.lower().endswith(".lnk"):
        # .lnk 的图标在快捷方式里，SHGetFileInfo 会自动解析；保留原路径即可
        pass
    for which in (SHIL_JUMBO, SHIL_EXTRALARGE, SHIL_LARGE):
        pm = _icon_from_imagelist(real, which, is_dir)
        if pm is not None and not pm.isNull():
            if pm.width() > size * 2:
                pm = pm.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return pm
    return None


# ================================================================ 图标
class IconCache:
    """从 exe / lnk / 文件夹取系统图标，带缓存与降级绘制。

    取图优先级：显式自定义图标 -> Win32 镜像列表大图 -> QFileIconProvider
    -> 类型标准图标 -> 名称首字标识。缓存键包含目标路径与尺寸，配置热更新后
    clear() 一次即可全部失效。
    """

    IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".ico", ".webp", ".svg")

    def __init__(self) -> None:
        self._provider = QFileIconProvider()
        self._cache: Dict[Tuple[str, int], QPixmap] = {}
        self._source: Dict[Tuple[str, int], str] = {}

    # ---------------------------------------------------------- 降级绘制
    def _fallback(self, size: int, letter: str) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(int(size * 0.52))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(235, 242, 250))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, letter or "?")
        p.end()
        return pm

    def _letter_tile(self, letter: str, size: int, kind: str = "app") -> QPixmap:
        """无图标时的首字标识：圆角渐变色块 + 首字（颜色按类型区分）。"""
        ch = (letter or "?").strip()[:1].upper() or "?"
        palette = {
            "app": ("#4F8CFF", "#2B5FD9"),
            "file": ("#8C9BB0", "#4A5B74"),
            "folder": ("#FFC24F", "#E39A18"),
            "url": ("#4FD1C5", "#1C9E93"),
        }
        c1, c2 = palette.get(kind, palette["app"])
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing, True)
        grad = QLinearGradient(0, 0, 0, size)
        grad.setColorAt(0.0, QColor(c1))
        grad.setColorAt(1.0, QColor(c2))
        rad = size * 0.26
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(0, 0, size, size), rad, rad)
        gloss = QLinearGradient(0, 0, 0, size * 0.55)
        gloss.setColorAt(0.0, QColor(255, 255, 255, 78))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(gloss))
        p.drawRoundedRect(QRectF(0, 0, size, size), rad, rad)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(int(size * 0.52))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 240))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, ch)
        p.end()
        return pm

    def _standard(self, size: int, sp: Any, letter: str) -> QPixmap:
        try:
            icon: QIcon = QApplication.style().standardIcon(sp)
            pm = icon.pixmap(size, size)
            if not pm.isNull():
                return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:  # pragma: no cover - 极少数样式缺失
            pass
        return self._fallback(size, letter)

    # ---------------------------------------------------------- 取图
    def _fit(self, pm: Optional[QPixmap], size: int) -> Optional[QPixmap]:
        if pm is None or pm.isNull():
            return None
        if pm.width() == size and pm.height() == size:
            return pm
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _custom_image(self, path: str, size: int) -> Optional[QPixmap]:
        if not path.lower().endswith(self.IMAGE_EXT) or not os.path.isfile(path):
            return None
        tmp = QPixmap(path)
        return self._fit(tmp, size)

    def get(self, item: Dict[str, Any], size: int, ring_key: str = "") -> QPixmap:
        target = str(item.get("target", ""))
        label = str(item.get("label", "") or item.get("name", "") or "?")
        kind = str(item.get("type", "app")).lower()
        icon_path = str(item.get("icon", "") or "")
        size = max(8, int(size))
        key = ("%s|%s|%s|%s|%s" % (ring_key, kind, icon_path, target, label), size)
        if key in self._cache:
            return self._cache[key]

        pm: Optional[QPixmap] = None
        src = ""
        cand = os.path.expandvars(os.path.expanduser(icon_path)) if icon_path else ""
        tgt = os.path.expandvars(os.path.expanduser(target)) if target else ""

        # 1) 用户指定的自定义图标文件（图片直接读，exe/ico/lnk 走系统图标）
        if cand:
            pm = self._custom_image(cand, size)
            if pm is not None:
                src = "自定义图片"
            elif os.path.exists(cand):
                pm = self._fit(extract_icon_pixmap(cand, size, os.path.isdir(cand)), size)
                if pm is not None:
                    src = "自定义图标+Win32"

        # 2) 程序 / 快捷方式 / 文件 / 文件夹：优先 Win32 大图镜像列表
        if pm is None and tgt and kind in ("app", "lnk", "file", "folder", "exe", "uwp"):
            is_dir = os.path.isdir(tgt)
            pm = self._fit(extract_icon_pixmap(tgt, size, is_dir), size)
            if pm is not None:
                src = "Win32镜像列表"

        # 3) QFileIconProvider 兜底（含 .lnk 解析、UWP 等）
        if pm is None and tgt and os.path.exists(tgt):
            try:
                ic = self._provider.icon(QFileInfo(tgt))
                if ic is not None and not ic.isNull():
                    pm = self._fit(ic.pixmap(size, size), size)
                    if pm is not None:
                        src = "QFileIconProvider"
            except Exception:
                pm = None

        # 4) 首字标识 / 类型标准图标
        if pm is None:
            if kind == "url":
                pm = self._standard(size, QStyle.SP_DriveNetIcon, label[:1])
                src = "标准类型图标"
            elif kind == "folder":
                pm = self._standard(size, QStyle.SP_DirIcon, label[:1])
                src = "标准类型图标"
            elif not tgt:
                # 未设置路径的槽位：用「首字色块」占位，比通用文件图标更易辨识
                pm = self._letter_tile(label[:1], size, kind)
                src = "首字标识"
            else:
                pm = self._standard(size, QStyle.SP_FileIcon, label[:1])
                src = "标准类型图标"

        self._cache[key] = pm
        self._source[key] = src
        return pm

    def source_of(self, item: Dict[str, Any], size: int, ring_key: str = "") -> str:
        """返回上一次取图的实际来源（供自检输出，验证真实图标链路生效）。"""
        key = ("%s|%s|%s|%s|%s" % (ring_key, str(item.get("type", "app")).lower(),
                                   str(item.get("icon", "") or ""),
                                   str(item.get("target", "")),
                                   str(item.get("label", "") or "?")), max(8, int(size)))
        return self._source.get(key, "未取图")

    def clear(self) -> None:
        self._cache.clear()
        self._source.clear()


# ================================================================ 钩子线程
class HookBridge(QObject):
    """跨线程信号：钩子线程 -> 主线程 UI。"""

    triggered = pyqtSignal(int, int)      # 物理坐标：唤起
    moved = pyqtSignal(int, int)          # 物理坐标：拖动中
    released = pyqtSignal(int, int)       # 物理坐标：松开左键
    cancelled = pyqtSignal(str)           # 取消原因
    status = pyqtSignal(bool, str)        # 钩子安装结果
    clicked = pyqtSignal(int, int)        # 热键模式下单击（物理坐标）
    digit = pyqtSignal(int)               # 热键模式下数字键 1..N
    wheel = pyqtSignal(int)               # 圆盘可见时滚轮（+1 下一环 / -1 上一环）
    ringkey = pyqtSignal(int)             # 圆盘可见时切环按键（+1 / -1 / 0=Tab 循环）


class InputHook(threading.Thread):
    """WH_MOUSE_LL + WH_KEYBOARD_LL 钩子线程，自带消息循环。"""

    def __init__(self, bridge: HookBridge, threshold_phys: int = 12,
                 swallow_moves: bool = True) -> None:
        super().__init__(name="dial-input-hook", daemon=True)
        self.bridge = bridge
        self.threshold = max(4, int(threshold_phys))
        # 硬性禁止：吞掉 WM_MOUSEMOVE 会让系统不再移动光标（指针冻结），
        # 方向投掷将永远无法生效，且 WM_LBUTTONUP/GetCursorPos 取到的都是冻结坐标。
        if swallow_moves:
            log.warning("swallow_moves=True 已被忽略：低层钩子吞掉鼠标移动会冻结系统光标，"
                        "使方向判定永远停在死区、松手也取不到真实坐标")
        self.swallow_moves = False

        self._mouse_cb = HOOKPROC(self._mouse_proc)
        self._kbd_cb = HOOKPROC(self._kbd_proc)
        self.h_mouse: Optional[int] = None
        self.h_kbd: Optional[int] = None
        self.thread_id: int = 0
        self.installed = False
        self.mouse_ok = False
        self.kbd_ok = False
        self.last_error = ""
        self.ready = threading.Event()

        # 状态机（全部在钩子线程内读写，不加锁）
        self.armed = False          # 已在桌面按下左键
        self.active = False         # 圆盘已弹出（桌面拖动入口）
        self.anchor: Tuple[int, int] = (0, 0)
        # 热键入口：由主线程置位。激活时鼠标移动实时上报、单击=确认、
        # 数字键 1..N=直接启动；与桌面拖动入口互斥，互不干扰。
        self.hotkey_mode = False
        # 托盘菜单弹出期间置位：此时所有鼠标事件直接放行，不进入桌面拖动
        # 状态机，避免「点菜单」被误判成「桌面拖动唤起圆盘」。
        self.suppress = False

    # -------------------------------------------------- 回调
    def _hotkey_mouse(self, wParam: int, x: int, y: int) -> int:
        """热键模式下的鼠标处理，返回 1 表示吞掉该事件。"""
        if wParam == WM_MOUSEMOVE:
            self.bridge.moved.emit(x, y)   # 移动必须放行，否则光标会被冻结
            return 0
        if wParam == WM_LBUTTONDOWN:
            self.bridge.clicked.emit(x, y)
            return 1
        if wParam == WM_LBUTTONUP:
            return 1
        if wParam in (WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            self.bridge.cancelled.emit("other_button")
            return 1
        return 0

    def _wheel_delta(self, lParam: int) -> int:
        """从 MSLLHOOKSTRUCT.mouseData 高 16 位取滚轮增量（正=向上）。"""
        try:
            info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            raw = int(ctypes.c_uint32(info.mouseData).value)
            delta = ctypes.c_short((raw >> 16) & 0xFFFF).value
            if delta == 0:
                return 0
            return 1 if delta > 0 else -1
        except Exception:
            return 0

    def _mouse_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode == 0:
            if self.suppress:   # 托盘菜单打开中：只放行，不动状态机
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            try:
                info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = int(info.pt.x), int(info.pt.y)
                if wParam == WM_MOUSEWHEEL and (self.active or self.hotkey_mode):
                    step = self._wheel_delta(lParam)
                    if step:
                        self.bridge.wheel.emit(step)
                    return 1     # 圆盘可见期间滚轮只用于切环，不再滚桌面/窗口
                if self.hotkey_mode:
                    if self._hotkey_mouse(wParam, x, y):
                        return 1
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                if wParam == WM_LBUTTONDOWN:
                    self._on_down(x, y)
                elif wParam == WM_MOUSEMOVE:
                    swallow = self._on_move(x, y)
                    if swallow:
                        return 1
                elif wParam == WM_LBUTTONUP:
                    self._on_up(x, y)
                elif wParam in (WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                    if self.active:
                        self._reset()
                        self.bridge.cancelled.emit("other_button")
            except Exception:  # 钩子回调绝不能抛异常
                log.debug("mouse hook error", exc_info=True)
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _kbd_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            try:
                info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(info.vkCode)
                if vk == VK_ESCAPE and (self.active or self.hotkey_mode):
                    self._reset()
                    self.bridge.cancelled.emit("esc")
                    return 1
                # 切环按键：Tab（循环下一环）/ [（上一环）/ ]（下一环），两套入口都支持
                if self.active or self.hotkey_mode:
                    if vk == VK_TAB:
                        self.bridge.ringkey.emit(0)
                        return 1
                    if vk == VK_OEM_4:
                        self.bridge.ringkey.emit(-1)
                        return 1
                    if vk == VK_OEM_6:
                        self.bridge.ringkey.emit(1)
                        return 1
                if self.hotkey_mode:
                    # 数字键 1..9 直接启动对应扇区，0 视为第 10 个
                    if 0x31 <= vk <= 0x39:
                        self.bridge.digit.emit(vk - 0x30 - 1)
                        return 1
                    if vk == 0x30:
                        self.bridge.digit.emit(9)
                        return 1
            except Exception:
                log.debug("kbd hook error", exc_info=True)
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    # -------------------------------------------------- 状态机
    def _reset(self) -> None:
        self.armed = False
        self.active = False

    def _on_down(self, x: int, y: int) -> None:
        if self.active:
            return
        blank = is_blank_desktop_point(x, y)
        if TRACE:
            log.info("DOWN (%d,%d) is_desktop=%s blank=%s top=%s chain=%s",
                     x, y, is_desktop_point(x, y), blank,
                     _class_name(user32.WindowFromPoint(POINT(x, y))),
                     " -> ".join(_class_name(h) for h in
                                 _ancestor_chain(user32.WindowFromPoint(POINT(x, y)))))
        if not blank:
            self.armed = False
            return
        self.armed = True
        self.anchor = (x, y)
        if TRACE:
            log.info("ARMED anchor=(%d,%d) threshold=%d", x, y, self.threshold)

    def _left_down(self) -> bool:
        """当前物理左键是否按下（独立成方法便于自检时替换）。"""
        return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)

    def _on_move(self, x: int, y: int) -> bool:
        if self.active:
            self.bridge.moved.emit(x, y)
            return self.swallow_moves
        if not self.armed:
            return False
        if not self._left_down():
            self.armed = False
            return False
        ax, ay = self.anchor
        if math.hypot(x - ax, y - ay) >= self.threshold:
            self.active = True
            self.bridge.triggered.emit(ax, ay)
            self.bridge.moved.emit(x, y)
            return self.swallow_moves
        return False

    def _on_up(self, x: int, y: int) -> None:
        if not self.active:
            self.armed = False
            return
        # 重要：当 swallow_move_events=True 时，被吞掉的 WM_MOUSEMOVE 会让系统
        # 不更新后续按键事件的 pt，导致 WM_LBUTTONUP 的坐标停留在按下点，
        # 松手点会落回圆心死区，表现为“圆盘弹出来了但松手不执行”。
        # 因此在松开时以实时光标位置为准。
        try:
            p = POINT()
            if user32.GetCursorPos(ctypes.byref(p)):
                gx, gy = int(p.x), int(p.y)
                if math.hypot(gx - x, gy - y) >= 2:
                    log.debug("UP pt 陈旧 (%d,%d)，改用实时光标 (%d,%d)", x, y, gx, gy)
                x, y = gx, gy
        except Exception:
            log.debug("GetCursorPos 失败，沿用钩子 pt", exc_info=True)
        if TRACE:
            log.info("UP (%d,%d) active=%s armed=%s", x, y, self.active, self.armed)
        self.active = False
        self.armed = False
        self.bridge.released.emit(x, y)

    # -------------------------------------------------- 线程体
    def run(self) -> None:
        self.thread_id = int(kernel32.GetCurrentThreadId())
        hmod = kernel32.GetModuleHandleW(None)
        try:
            self.h_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_cb, hmod, 0)
            self.mouse_ok = bool(self.h_mouse)
            if not self.mouse_ok:
                self.last_error = "SetWindowsHookEx(WH_MOUSE_LL) 失败, err=%s" % ctypes.get_last_error()
            self.h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_cb, hmod, 0)
            self.kbd_ok = bool(self.h_kbd)
            if not self.kbd_ok:
                tail = "SetWindowsHookEx(WH_KEYBOARD_LL) 失败, err=%s" % ctypes.get_last_error()
                self.last_error = (self.last_error + " | " + tail) if self.last_error else tail
        except Exception as exc:  # pragma: no cover
            self.last_error = "钩子安装异常: %r" % (exc,)

        self.installed = self.mouse_ok
        self.ready.set()
        try:
            self.bridge.status.emit(self.installed, self.last_error)
        except Exception:
            pass

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self.h_mouse:
            user32.UnhookWindowsHookEx(self.h_mouse)
        if self.h_kbd:
            user32.UnhookWindowsHookEx(self.h_kbd)
        self.installed = False

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


# ================================================================ 圆盘窗口
class DialWindow(QWidget):
    """环形扇区菜单。无边框 / 透明 / 置顶 / 不抢焦点 / 鼠标穿透，全部交互由钩子驱动。"""

    hidden = pyqtSignal()          # 圆盘真正隐藏后通知（用于热键模式复位）
    ringChanged = pyqtSignal(int)  # 滚轮/按键切环后通知（用于回写配置与托盘勾选同步）

    def __init__(self, config: Dict[str, Any], icons: IconCache,
                 launch_cb) -> None:
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus |
                         Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.cfg = config
        self.icons = icons
        self.launch_cb = launch_cb

        self._items: List[Dict[str, Any]] = []
        self._sel = -1
        self._cancel = False
        self._flung = False
        self._visible = False
        self._t0 = 0.0
        self._center = QPointF(0, 0)
        self._center_global = QPointF(0, 0)
        self._anchor_global = QPointF(0, 0)
        self._noactivate_applied = False

        # 动画标量（由 QPropertyAnimation 驱动，paintEvent 只读）
        self._expand = 0.0          # 展开程度 0~1（影响整体缩放与透明度）
        self._hover = 0.0           # 选中扇区的悬停程度 0~1（放大 + 光晕）
        self._closing = False       # 收起动画中，忽略指针更新
        self._base_cache: Dict[Any, Any] = {}   # 静态绘制层位图缓存（按选中扇区分档）
        self._paint_key: Any = None             # 上次重绘时的可见状态签名（用于抑制空重绘）
        self.sound: Optional[SoundKit] = None
        self.hotkey_mode = False    # 本次唤起来自全局热键（中心区提示文案不同）

        self._expand_anim = QPropertyAnimation(self, b"expand", self)
        self._expand_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._expand_anim.finished.connect(self._on_expand_finished)

        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setEasingCurve(QEasingCurve.OutBack)

        # 多环切换过渡（旧环淡出 + 新环淡入），由 QPropertyAnimation 驱动
        self._ring_fade = 1.0
        self._ring_prev_items: Optional[List[Dict[str, Any]]] = None
        self._ring_prev_name = ""
        self._ring_anim = QPropertyAnimation(self, b"ringfade", self)
        self._ring_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._ring_anim.finished.connect(self._on_ring_anim_finished)

        # 环级显示状态（由 set_config 根据 current_ring 填充）
        self._ring_index = 0
        self._ring_total = 1
        self._ring_name = "圆环 1"
        self._ring_key = "ring1"
        self._ring_icon_size = 0

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(15000)
        self._watchdog.timeout.connect(self.force_hide)

        self.set_config(config)

    # -------------------------------------------------- 动画属性
    @pyqtProperty(float)
    def expand(self) -> float:
        return self._expand

    @expand.setter
    def expand(self, value: float) -> None:
        self._expand = max(0.0, min(1.0, float(value)))
        self.update()

    @pyqtProperty(float)
    def hover(self) -> float:
        return self._hover

    @hover.setter
    def hover(self, value: float) -> None:
        # 允许 OutBack 缓动的轻微过冲（>1）以形成"弹一下"的手感，绘制时再收敛
        self._hover = max(-0.3, min(1.3, float(value)))
        self.update()

    def _anim_cfg(self, key: str, default: float) -> float:
        pack = self.cfg.get("animation") or {}
        try:
            return float(pack.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def animations_enabled(self) -> bool:
        return bool((self.cfg.get("animation") or {}).get("enabled", True))

    # -------------------------------------------------- 多环切换
    @pyqtProperty(float)
    def ringfade(self) -> float:
        return getattr(self, "_ring_fade", 1.0)

    @ringfade.setter
    def ringfade(self, value: float) -> None:
        self._ring_fade = max(0.0, min(1.0, float(value)))
        self.update()

    def ring_names(self) -> List[str]:
        return [str(r.get("name") or ("圆环 %d" % (i + 1)))
                for i, r in enumerate(ring_list(self.cfg))]

    def switch_ring(self, delta: int = 1, absolute: Optional[int] = None) -> bool:
        """切换当前环（滚轮 / [ ] Tab / 托盘菜单），带淡出淡入过渡。

        只换 items/名字/图标尺寸，不改几何与命中逻辑——几何与命中依然同源，
        因此切环不会影响已有的交互手感。
        """
        rings = ring_list(self.cfg)
        total = len(rings)
        if total <= 1:
            return False
        cur = int(getattr(self, "_ring_index", 0))
        nxt = (cur + int(delta)) % total if absolute is None else int(absolute) % total
        if nxt == cur:
            return False

        self._ring_prev_items = list(self._items)
        self._ring_prev_name = str(getattr(self, "_ring_name", ""))
        new_cfg = dict(self.cfg)
        new_cfg["current_ring"] = nxt
        self.set_config(new_cfg)
        self.invalidate_paint_cache()

        if not self.animations_enabled():
            self._ring_fade = 1.0
            self._ring_prev_items = None
            self.update()
        else:
            self._ring_fade = 0.0
            self._ring_anim.stop()
            self._ring_anim.setDuration(int(max(1, self._anim_cfg("ring_ms", 190))))
            self._ring_anim.setStartValue(0.0)
            self._ring_anim.setEndValue(1.0)
            self._ring_anim.start()
        log.info("RING switch %d -> %d name=%s", cur, nxt, self._ring_name)
        self.ringChanged.emit(nxt)
        return True

    def _on_ring_anim_finished(self) -> None:
        self._ring_prev_items = None
        self._ring_prev_name = ""
        self.invalidate_paint_cache()
        self.update()

    # -------------------------------------------------- 配置
    def set_config(self, config: Dict[str, Any]) -> None:
        # 深/浅色：按 theme.follow_system + system_theme_mode() 解析出实际调色板，
        # 不修改传入的配置对象（保持 app 侧配置干净）。
        colors, self.theme_note = resolve_colors(config)
        eff = dict(config)
        eff["colors"] = dict(colors)
        n = self._sector_count()

        # 多环：当前环的槽位 / 名称 / 图标尺寸 / 配色覆盖全部反映到绘制状态
        ring = active_ring(config)
        self._ring_index = ring_index(config)
        self._ring_total = max(1, len(ring_list(config)))
        self._ring_name = str(ring.get("name") or ("圆环 %d" % (self._ring_index + 1)))
        self._ring_key = str(ring.get("id") or ("ring%d" % (self._ring_index + 1)))
        try:
            self._ring_icon_size = int(ring.get("icon_size") or 0)
        except (TypeError, ValueError):
            self._ring_icon_size = 0
        accent = str(ring.get("accent") or "").strip()
        if accent and QColor(accent).isValid():
            eff["colors"]["accent"] = accent

        items = [normalize_item(it, i) for i, it in enumerate(ring.get("items") or [])]
        if len(items) < n:
            items = items + [normalize_item({}, i) for i in range(len(items), n)]
        self._items = items[:n]

        # 让 self.cfg 也保持一致（_paint_geo 通过 self.cfg 读外观参数）
        eff["items"] = self._items
        eff["rings"] = config.get("rings")
        eff["current_ring"] = self._ring_index
        self.cfg = eff
        log.info("调色板：%s accent=%s 当前环=%s（%d/%d）",
                 self.theme_note, self.cfg["colors"].get("accent"),
                 self._ring_name, self._ring_index + 1, self._ring_total)
        self.invalidate_paint_cache()
        self.update()


    def _sector_count(self) -> int:
        try:
            n = int(self.cfg.get("sectors", 8))
        except (TypeError, ValueError):
            n = 8
        return max(3, min(16, n))

    def _num(self, key: str, default: float) -> float:
        try:
            return float(self.cfg.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _color(self, key: str, default: str, alpha: int = 255) -> QColor:
        colors = self.cfg.get("colors") or {}
        c = QColor(str(colors.get(key, default)))
        if not c.isValid():
            c = QColor(default)
        c.setAlpha(max(0, min(255, int(alpha))))
        return c

    # -------------------------------------------------- 显示 / 隐藏
    def open_at(self, anchor: QPoint) -> None:
        n = self._sector_count()
        if len(self._items) != n:
            self.set_config(self.cfg)
        R = self._num("radius", 168)
        margin = self._num("cancel_margin", 70)
        extent = int(R + margin + 18)

        # 多显示器：以「光标所在屏」为定位基准，贴边钳制也只在该屏范围内侧进行
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen else QRectF(0, 0, 1920, 1080).toRect()
        try:
            self._screen_name = screen.name() if screen else "?"
            self._screen_dpr = float(screen.devicePixelRatio()) if screen else 1.0
        except Exception:
            self._screen_name, self._screen_dpr = "?", 1.0

        # 边缘防溢出：保证整个扇区圆（外径 R）+ 一点余量都留在屏幕内，
        # 否则贴边唤起时最外侧扇区被裁到屏幕外，指针根本够不到它。
        # 圆心平移不影响方向手感——命中判定已与绘制同源（都以 _center_global 为基准）。
        half = int(R + 18)
        left, right = geo.x() + half, geo.x() + geo.width() - half
        top, bottom = geo.y() + half, geo.y() + geo.height() - half
        if left > right:
            left = right = geo.center().x()
        if top > bottom:
            top = bottom = geo.center().y()
        cx = min(max(anchor.x(), left), right)
        cy = min(max(anchor.y(), top), bottom)

        self.setGeometry(int(round(cx - extent)), int(round(cy - extent)),
                         extent * 2, extent * 2)
        self._center = QPointF(self.width() / 2.0, self.height() / 2.0)
        self._center_global = QPointF(self.geometry().x() + self._center.x(),
                                      self.geometry().y() + self._center.y())
        self._anchor_global = QPointF(anchor)

        self._sel = -1
        self._cancel = False
        self._flung = False
        self._visible = True
        self._closing = False
        self._paint_key = None      # 新一轮唤出：强制首帧重绘
        self._t0 = time.perf_counter()
        self._hover_anim.stop()
        self._hover = 0.0
        self._collapse_sound_done = False

        self.show()
        self.raise_()
        self._apply_noactivate()
        # 展开缓动延后到事件队列空闲（首帧绘制完成）再启动：
        # 进程内首次 show 需要创建原生窗口并做首次绘制（实测约 270ms），
        # 若立即启动 QPropertyAnimation，缓动时长会被这次冷启动吃掉，
        # 表现为「先僵住再突然弹出」。延后一帧可让 170ms 的缓动完整可见。
        QTimer.singleShot(0, self._start_expand_anim)
        # 唤出音：与展开动画同时发生
        if self.sound is not None:
            self.sound.play("show")
        self._watchdog.start()
        self.update()
        log.info("DIAL open anchor=(%d,%d) center=(%.0f,%.0f) screen=%s dpr=%.2f hotkey=%s",
                 anchor.x(), anchor.y(), self._center_global.x(), self._center_global.y(),
                 getattr(self, "_screen_name", "?"), getattr(self, "_screen_dpr", 1.0),
                 self.hotkey_mode)

    def _start_expand_anim(self) -> None:
        """展开缓动：0 -> 1（OutCubic），由 QPropertyAnimation 驱动，无定时器抖动。"""
        self._expand_anim.stop()
        if not self.animations_enabled():
            self.expand = 1.0
            return
        self._expand_anim.setDuration(int(max(1, self._anim_cfg("expand_ms", 170))))
        self._expand_anim.setStartValue(0.0)
        self._expand_anim.setEndValue(1.0)
        self._expand_anim.start()

    def _on_expand_finished(self) -> None:
        if self._closing:
            self._final_hide()

    def _apply_noactivate(self) -> None:
        """补上 Qt 未覆盖的 WS_EX_NOACTIVATE / WS_EX_TOOLWINDOW，确保不进 Alt+Tab。"""
        if self._noactivate_applied:
            return
        try:
            hwnd = int(self.winId())
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            self._noactivate_applied = True
        except Exception:
            log.debug("set exstyle failed", exc_info=True)

    def force_hide(self) -> None:
        """立即隐藏（看门狗 / 退出 / 兜底）：不走收起动画。"""
        self._closing = False
        self._final_hide()

    def _final_hide(self) -> None:
        was_visible = self._visible
        self._visible = False
        self._closing = False
        self._sel = -1
        self._cancel = False
        self._flung = False
        self._paint_key = None
        self._expand_anim.stop()
        self._hover_anim.stop()
        self._watchdog.stop()
        self.hide()
        if was_visible:
            # 收起音：若收起前已播过 select/cancel（collapse 显式传入）则不重复，
            # 仅在看门狗/强制隐藏等无音路径补一个 hide 音。
            if self.sound is not None and not getattr(self, "_collapse_sound_done", False):
                self.sound.play("hide")
            log.info("DIAL hide")
            self.hidden.emit()

    def collapse(self, sound: Optional[str] = None) -> None:
        """收起：缓动收缩后隐藏。sound 为收起瞬间要播的事件音（select/cancel/hide）。"""
        if not self._visible:
            return
        if sound and self.sound:
            self.sound.play(sound)
            self._collapse_sound_done = True
        if not self.animations_enabled():
            self._final_hide()
            return
        self._closing = True
        self._watchdog.stop()
        self._hover_anim.stop()
        self._hover = 0.0
        self._expand_anim.stop()
        self._expand_anim.setDuration(int(max(1, self._anim_cfg("collapse_ms", 130))))
        self._expand_anim.setStartValue(self._expand)
        self._expand_anim.setEndValue(0.0)
        self._expand_anim.start()

    # -------------------------------------------------- 鼠标更新
    def _cancel_radius(self) -> float:
        """取消圈半径。绘制（虚线圆）与命中判定必须同源，否则"画了一圈却按另一圈取消"。"""
        return self._num("radius", 168) + self._num("cancel_margin", 70) * 0.55

    def _animate_hover(self, on: bool) -> None:
        """选中扇区的悬停缓动（OutBack，带轻微过冲）。"""
        self._hover_anim.stop()
        if not self.animations_enabled():
            self.hover = 1.0 if on else 0.0
            return
        self._hover_anim.setDuration(int(max(1, self._anim_cfg("hover_ms", 110))))
        self._hover_anim.setStartValue(0.0 if on else max(0.0, self._hover))
        self._hover_anim.setEndValue(1.0 if on else 0.0)
        self._hover_anim.start()

    def update_pointer(self, gx: float, gy: float) -> None:
        if not self._visible or self._closing:
            return
        self._watchdog.start()
        R = self._num("radius", 168)
        inner = self._num("inner_radius", 52)
        dz = max(4.0, inner * self._num("dead_zone_ratio", 0.5))
        cr = self._cancel_radius()

        # 关键：方向角与半径必须以「绘制圆心」为基准，与 paintEvent 完全同源。
        # 贴边唤起时圆心会被钳制平移（本例最多 155px），若仍用按下点 (_anchor_global)
        # 作基准：① 指针相对可见圆盘的方向整体错位 → 高亮到错误扇区；
        # ② 按下点附近的移动永远落在死区内 → 指针明明压在扇区上却完全不亮。
        ax, ay = self._center_global.x(), self._center_global.y()
        dx, dy = gx - ax, gy - ay
        dist = math.hypot(dx, dy)

        if self._flung:
            return

        if dist > cr:
            # 甩出安全区：先提示取消，继续甩远则立即收起
            if TRACE:
                log.info("POINTER cancel g=(%0.0f,%0.0f) dist=%0.1f > cr=%0.1f", gx, gy, dist, cr)
            was_sel = self._sel
            self._cancel = True
            self._sel = -1
            if was_sel != -1:
                self._animate_hover(False)
            self._request_repaint()
            if dist > cr + R * 0.9:
                self._flung = True
                self.collapse("cancel")   # 甩出取消：先响取消音再收起
            return

        self._cancel = False
        if dist < dz:
            if self._sel != -1:
                if TRACE:
                    log.info("POINTER deadzone g=(%0.0f,%0.0f) dist=%0.1f < dz=%0.1f", gx, gy, dist, dz)
                self._sel = -1
                self._animate_hover(False)
                self._request_repaint()
            return

        clock = math.degrees(math.atan2(dx, -dy))  # 0=正上，顺时针为正
        clock %= 360.0
        n = self._sector_count()
        idx = _sector_index(clock, n)
        if idx != self._sel:
            if TRACE:
                log.info("POINTER g=(%0.0f,%0.0f) center=(%0.0f,%.0f) dxdy=(%.0f,%.0f) "
                         "dist=%.1f clock=%.1f -> sector=%d",
                         gx, gy, ax, ay, dx, dy, dist, clock, idx)
            prev = self._sel
            self._sel = idx
            # 命中态可观测性：每次换扇区记一条 INFO，便于事后把截图/音效与真实命中态对齐
            log.info("DIAL sel %d->%d clock=%.1f r=%.1f cursor=(%.0f,%.0f) center=(%.0f,%.0f)",
                     prev, idx, clock, dist, gx, gy, ax, ay)
            # 悬停音：音高随扇区序号递变（0..15 档），并触发扇区放大缓动
            if self.sound is not None:
                self.sound.play("hover", idx)
            self._animate_hover(True)
        self._request_repaint()

    def _request_repaint(self) -> None:
        """只有可见状态真的变化时才请求重绘。

        透明分层窗口每次 update() 都要重新合成整窗（实测单帧 0.65~0.98ms）。
        鼠标在同一个扇区内连续移动时，视觉结果完全一样，此前每次钩子移动事件
        都会调用 update()，等于把「鼠标采样率」直接变成了「重绘频率」。
        这里只在 选中扇区 / 取消态 / 可见性 变化时重绘；悬停与展开动画由
        QPropertyAnimation 的 setter 自己触发重绘，不会漏帧。
        """
        key = (self._sel, self._cancel, self._visible)
        if key == getattr(self, "_paint_key", None):
            return
        self._paint_key = key
        self.update()

    def _resolve_item(self, idx: int, cancel: bool) -> Optional[Dict[str, Any]]:
        if cancel or idx < 0 or idx >= len(self._items):
            return None
        item = self._items[idx]
        if str(item.get("type", "")).lower() in ("empty", ""):
            return None
        return item

    def _commit(self, idx: int, cancel: bool) -> None:
        item = self._resolve_item(idx, cancel)
        # 选中=播放 select 音并缓动收起；取消/空扇区=播放 cancel 音
        self.collapse("select" if item else "cancel")
        if item is None:
            log.info("DIAL finish no-op idx=%s cancel=%s", idx, cancel)
            return
        log.info("DIAL select idx=%d label=%s", idx, item.get("label", ""))
        self.launch_cb(item)

    def finish(self, gx: float, gy: float) -> None:
        """松开左键：按当前选中扇区执行动作，然后缓动收起。"""
        if not self._visible or self._closing:
            return
        self.update_pointer(gx, gy)
        self._commit(self._sel, self._cancel or self._flung)

    def finish_index(self, idx: int) -> None:
        """热键模式的数字键直选：跳过指针判定，直接执行第 idx 个扇区。"""
        if not self._visible or self._closing:
            return
        log.info("DIAL digit select idx=%d", idx)
        self._commit(idx, False)

    def cancel(self, reason: str = "") -> None:
        """主动取消（Esc / 右键 / 点击空白）。"""
        if not self._visible or self._closing:
            return
        log.info("DIAL cancel reason=%s", reason)
        self.collapse("cancel")

    # -------------------------------------------------- 绘制（静态层缓存）
    def _paint_geo(self) -> Dict[str, Any]:
        """一帧绘制所需的全部几何/配色参数（paintEvent 与静态层缓存共用，保证同源）。"""
        n = self._sector_count()
        R = self._num("radius", 168)
        r = min(self._num("inner_radius", 52), R - 12)
        icon_size = float(getattr(self, "_ring_icon_size", 0) or self._num("icon_size", 32))
        font_size = self._num("font_size", 10)
        appear = self.cfg.get("appearance") or {}
        try:
            gap_px = float(appear.get("gap_px", 2.4))
        except (TypeError, ValueError):
            gap_px = 2.4
        try:
            corner = float(appear.get("sector_radius",
                                      appear.get("corner_radius", 9.0)))
        except (TypeError, ValueError):
            corner = 9.0
        # 扇区越多，单个扇区的可用角度越小：自动缩小图标/字号，避免挤压重叠
        dense = max(0.62, 8.0 / n) if n > 8 else 1.0
        icon_size = max(14.0, icon_size * dense)
        label_px = max(9, int(round(font_size * 1.3 * dense)))
        span = 360.0 / n
        # 间隙按「像素」换算成角度（在环带中径处换算），这样扇区数量变化时间隙
        # 的视觉粗细保持恒定，与参考图的细缝观感一致。
        mid_r = max(1.0, (r + R) * 0.5)
        gap = math.degrees(max(0.0, gap_px) / mid_r)
        gap = min(span * 0.45, gap)
        cr = max(0.0, min(corner, (R - r) * 0.30, span * math.radians(mid_r) * 0.30))
        cx, cy = self._center.x(), self._center.y()
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(label_px)
        return {
            "n": n, "R": R, "r": r, "icon_size": icon_size, "font_size": font_size,
            "label_px": label_px, "span": span, "gap": gap, "corner": cr,
            "cx": cx, "cy": cy, "mid_r": mid_r,
            "outer_rect": QRectF(cx - R, cy - R, R * 2, R * 2),
            "inner_rect": QRectF(cx - r, cy - r, r * 2, r * 2),
            "font": font, "fm": QFontMetrics(font),
            "base": self._color("base", "#1B1F2A", int(self._cfg_alpha("base_alpha", 205))),
            "empty_alpha": int(self._cfg_alpha("empty_alpha", 60)),
            "accent": self._color("accent", "#4FC3F7", int(self._cfg_alpha("accent_alpha", 225))),
            "warn": self._color("warn", "#FF8A65", 235),
            "text": self._color("text", "#EAF1F8", 240),
        }


    def _draw_sector(self, p: QPainter, geo: Dict[str, Any], i: int,
                     selected: bool, hv: float) -> None:
        """绘制第 i 个扇区（圆角形状 + 磨砂玻璃填充 + 选中光晕 + 图标 + 文案）。"""
        n, R, r = geo["n"], geo["R"], geo["r"]
        cx, cy = geo["cx"], geo["cy"]
        base, accent = geo["base"], geo["accent"]
        gain = max(1.0, self._anim_cfg("sector_gain", 1.10))
        cr = float(geo.get("corner", 0.0))

        start_clock, end_clock = _sector_clock_bounds(i, n, geo["gap"])
        # clock（自 12 点顺时针，与 update_pointer 完全同源）-> Qt 角度（自 3 点逆时针）
        # 必须以 start_clock 作为弧线起点，负跨度顺时针推进到 end_clock。
        # 若误用 end_clock 作起点，整块扇区会顺时针平移一个 span（45°），
        # 表现为「高亮扇区与光标所在扇区差一个」。
        item = self._items[i] if i < len(self._items) else {"type": "empty"}
        is_empty = str(item.get("type", "")).lower() in ("empty", "")

        # 选中扇区：沿半径方向"放大"（外径外扩、内径微缩），放大倍数随悬停缓动变化。
        # 仅调整绘制矩形，不改动角度边界，因此不会引入任何角度偏移。
        Ro, Ri = R, r
        if selected and hv > 0.0:
            Ro = R + R * (gain - 1.0) * hv
            Ri = r - r * (gain - 1.0) * 0.35 * hv

        # 圆角：路径整体内缩 cr，再用 2*cr 宽的圆头描边把边缘补回，
        # 得到「扇区之间留细缝 + 四角圆润」的观感；命中角边界不受影响。
        s_clock, e_clock = start_clock, end_clock
        if cr > 0.4:
            Ro -= cr
            Ri += cr
            pad = math.degrees(cr / max(1.0, (Ro + Ri) * 0.5))
            if (e_clock - s_clock) > pad * 2.4:
                s_clock += pad
                e_clock -= pad
        q_start = 90.0 - s_clock
        q_span = -(e_clock - s_clock)
        o_rect = QRectF(cx - Ro, cy - Ro, Ro * 2, Ro * 2)
        i_rect = QRectF(cx - Ri, cy - Ri, Ri * 2, Ri * 2)

        path = QPainterPath()
        path.arcMoveTo(o_rect, q_start)
        path.arcTo(o_rect, q_start, q_span)
        path.arcTo(i_rect, q_start + q_span, -q_span)
        path.closeSubpath()

        if selected:
            fill = QColor(accent)
            fill.setAlpha(int(self._cfg_alpha("accent_alpha", 225)))
        elif is_empty:
            fill = QColor(base)
            fill.setAlpha(geo["empty_alpha"])
        else:
            fill = QColor(base)

        if cr > 0.4:
            pen = QPen(fill, cr * 2.0)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 92 if selected else (22 if is_empty else 34)), 1.0))
        p.setBrush(fill)
        p.drawPath(path)

        # 磨砂玻璃：沿径向叠一层上亮下暗的白色雾面 + 内缘厚度高光，近似毛玻璃散射。
        # 只在有内容的槽位上叠加，且静态层已缓存，不进入逐帧路径。
        appear = self.cfg.get("appearance") or {}
        try:
            frost = float(appear.get("glass_alpha", 0.80) if appear.get("frosted", True)
                          else appear.get("frost", 0.0))
        except (TypeError, ValueError):
            frost = 1.0
        if frost > 0.05 and not is_empty:
            p.save()
            p.setClipPath(path)
            p.setPen(Qt.NoPen)
            top = int(max(6, min(76, 34 * frost)))
            box = QRectF(cx - Ro - 2, cy - Ro - 2, (Ro + 2) * 2, (Ro + 2) * 2)
            grad = QLinearGradient(cx, cy - Ro, cx, cy + Ro)
            grad.setColorAt(0.0, QColor(255, 255, 255, top))
            grad.setColorAt(0.45, QColor(255, 255, 255, int(top * 0.35)))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(grad))
            p.drawRect(box)
            rim = QLinearGradient(cx, cy - Ri, cx, cy - Ro)
            rim.setColorAt(0.0, QColor(255, 255, 255, int(30 * frost)))
            rim.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(rim))
            p.drawRect(box)
            p.restore()

        # 选中扇区的光晕描边：由内到外三层逐渐变淡的描边叠加，形成柔和外发光
        if selected:
            p.setBrush(Qt.NoBrush)
            for w, a in ((9.0, 34), (5.0, 62), (2.2, 165)):
                gcol = QColor(accent)
                gcol.setAlpha(int(max(0.0, min(1.0, 0.30 + 0.70 * hv)) * a))
                pen2 = QPen(gcol, w)
                pen2.setJoinStyle(Qt.RoundJoin)
                pen2.setCapStyle(Qt.RoundCap)
                p.setPen(pen2)
                p.drawPath(path)
            p.setPen(QPen(QColor(255, 255, 255, 120), 1.0))
            p.setBrush(fill)

        self._draw_sector_content(p, geo, i, selected)

    def _draw_sector_content(self, p: QPainter, geo: Dict[str, Any], i: int,
                             selected: bool) -> None:
        """扇区的图标/文案/空位圆点（与扇区形状分离，便于缓存路径单独补画）。"""
        n, R, r = geo["n"], geo["R"], geo["r"]
        cx, cy = geo["cx"], geo["cy"]
        span, icon_size = geo["span"], geo["icon_size"]
        fm, font = geo["fm"], geo["font"]

        item = self._items[i] if i < len(self._items) else {"type": "empty"}
        is_empty = str(item.get("type", "")).lower() in ("empty", "")

        mid = math.radians(i * span)
        ux, uy = math.sin(mid), -math.cos(mid)
        icon_r = (r + R) * 0.5
        ix, iy = cx + ux * icon_r, cy + uy * icon_r
        if not is_empty:
            pm = self.icons.get(item, int(icon_size), getattr(self, "_ring_key", ""))
            if not pm.isNull():
                p.drawPixmap(QPointF(ix - pm.width() / 2.0, iy - pm.height() / 2.0), pm)

            label = str(item.get("label", "") or item.get("name", "") or "")
            if label:
                arc_w = max(46.0, (2 * math.pi * icon_r / n) * 0.92)
                txt = fm.elidedText(label, Qt.ElideRight, int(arc_w))
                ly = iy + icon_size / 2.0 + 3
                p.setFont(font)
                p.setPen(QColor(255, 255, 255, 235) if selected else geo["text"])
                p.drawText(QRectF(ix - arc_w / 2.0, ly, arc_w, fm.height() + 2),
                           Qt.AlignHCenter | Qt.AlignTop, txt)
        else:
            # 空槽位：虚线圆 + 加号，视觉上明确「可在此配置程序」
            pen = QPen(QColor(255, 255, 255, 62), 1.4)
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([2.4, 2.4])
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            rr = max(9.0, icon_size * 0.30)
            p.drawEllipse(QRectF(ix - rr, iy - rr, rr * 2, rr * 2))
            p.setPen(QPen(QColor(255, 255, 255, 84), 1.4))
            p.drawLine(QPointF(ix - rr * 0.45, iy), QPointF(ix + rr * 0.45, iy))
            p.drawLine(QPointF(ix, iy - rr * 0.45), QPointF(ix, iy + rr * 0.45))

    def _draw_cancel_ring(self, p: QPainter, geo: Dict[str, Any]) -> None:
        """取消安全圈（虚线，颜色随取消态变化 -> 必须留在动态层）。"""
        cancel_pen = QPen(geo["warn"] if self._cancel else QColor(255, 255, 255, 46), 1.4)
        cancel_pen.setStyle(Qt.DashLine)
        cancel_pen.setDashPattern([4, 6])
        p.setPen(cancel_pen)
        p.setBrush(Qt.NoBrush)
        cr = self._cancel_radius()  # 与 update_pointer 的取消判定同源
        p.drawEllipse(QRectF(geo["cx"] - cr, geo["cy"] - cr, cr * 2, cr * 2))

    def _draw_hub(self, p: QPainter, geo: Dict[str, Any], fade: float = 1.0) -> None:
        """中心圆盘：常显当前环名 + 选中项名称 + 类型/快捷键 + 环指示点（动态层）。"""
        cx, cy, r = geo["cx"], geo["cy"], geo["r"]
        fm, base, accent = geo["fm"], geo["base"], geo["accent"]
        text_color, font_size = geo["text"], geo["font_size"]
        hub = QColor(base)
        hub.setAlpha(min(255, int(self._cfg_alpha("base_alpha", 205)) + 25))
        p.setBrush(hub)
        p.setPen(QPen(accent if self._sel >= 0 and not self._cancel
                      else QColor(255, 255, 255, 46), 1.4))
        p.drawEllipse(geo["inner_rect"])

        total = max(1, int(getattr(self, "_ring_total", 1)))
        idx = int(getattr(self, "_ring_index", 0))
        ring_name = str(getattr(self, "_ring_name", ""))
        prev_name = str(getattr(self, "_ring_prev_name", "") or "")
        tw = r * 1.72

        title = ""
        hint = ""
        if self._cancel:
            title = "松开取消"
            hint = "离开虚线安全圈"
        elif 0 <= self._sel < len(self._items):
            it = self._items[self._sel]
            title = str(it.get("label", "") or it.get("name", "") or "")
            is_blank = str(it.get("type", "")).lower() in ("empty", "") and not str(
                it.get("target", "") or "")
            if not title:
                title = "空槽位"
            hk = str(it.get("hotkey", "") or str(self._sel + 1))
            kind = TYPE_LABELS.get(str(it.get("type", "")).lower(), "")
            if is_blank:
                hint = "未配置 · 右键托盘「打开设置」指定"
            else:
                hint = ("[%s] %s" % (hk, kind)).strip() if kind else ("[%s]" % hk)
        else:
            # 无选中时中心显示当前环名（与参考图一致：环名常驻中心）
            title = ring_name
            hint = ("滚轮/[ ] 切环 · 拖动选择" if total > 1 else "拖动选择")

        hub_font = QFont("Microsoft YaHei UI")
        hub_font.setPixelSize(int(max(10, font_size * 1.40)))
        hub_font.setBold(True)
        title_rect = QRectF(cx - tw / 2, cy - fm.height() * 0.62, tw, fm.height() * 1.4)

        # 选中槽位时，在名称上方补一行小字环名（中心同时体现「当前环」与「当前选中项」）
        if 0 <= self._sel < len(self._items) and not self._cancel and ring_name:
            small_font = QFont("Microsoft YaHei UI")
            small_font.setPixelSize(int(max(9, font_size * 0.92)))
            p.save()
            p.setOpacity(0.62)
            p.setFont(small_font)
            p.setPen(QColor(text_color))
            sfm = QFontMetrics(small_font)
            p.drawText(QRectF(cx - tw / 2, cy - fm.height() * 0.62 - sfm.height() - 5,
                              tw, sfm.height()),
                       Qt.AlignCenter,
                       sfm.elidedText(ring_name, Qt.ElideRight, int(tw)))
            p.restore()

        # 切环过渡时中心文案交叉淡出淡入（旧环名 -> 新环名）
        if fade < 0.999 and prev_name and self._sel < 0 and not self._cancel:
            p.save()
            p.setOpacity(1.0 - fade)
            p.setFont(hub_font)
            p.setPen(QColor(text_color))
            p.drawText(title_rect, Qt.AlignCenter,
                       fm.elidedText(prev_name, Qt.ElideRight, int(tw)))
            p.restore()
            p.save()
            p.setOpacity(fade)
            p.setFont(hub_font)
            p.setPen(QColor(text_color))
            p.drawText(title_rect, Qt.AlignCenter,
                       fm.elidedText(title, Qt.ElideRight, int(tw)))
            p.restore()
        else:
            p.setFont(hub_font)
            p.setPen(geo["warn"] if self._cancel else text_color)
            p.drawText(title_rect, Qt.AlignCenter,
                       fm.elidedText(title, Qt.ElideRight, int(tw)))

        sub_y = cy + fm.height() * 0.42
        if hint:
            sub = QFont("Microsoft YaHei UI")
            sub.setPixelSize(int(max(8, font_size * 0.95)))
            p.setFont(sub)
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(cx - tw / 2, sub_y, tw, fm.height()),
                       Qt.AlignCenter, fm.elidedText(hint, Qt.ElideRight, int(tw)))
            sub_y += fm.height() * 0.95

        # 环指示点：多环时在中心下方显示位置，当前环高亮加宽
        if total > 1:
            gap = 7.0
            span_w = (total - 1) * gap
            y = min(cy + r * 0.72, sub_y + 4)
            for k in range(total):
                on = (k == idx)
                col = QColor(accent if on else QColor(255, 255, 255))
                col.setAlpha(230 if on else 90)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                w = 9.0 if on else 4.6
                p.drawRoundedRect(QRectF(cx - span_w / 2 + k * gap - w / 2, y, w, 4.6),
                                  w / 2, 2.3)
            p.setBrush(Qt.NoBrush)


    def invalidate_paint_cache(self) -> None:
        """配置/条目/尺寸变化后作废静态层缓存。"""
        self._base_cache = {}

    def _base_pixmap(self, sel_skip: int, geo: Dict[str, Any]):
        """未选中扇区 + 图标 + 文案的静态底层缓存（按 sel_skip 分档）。

        命中时机：展开动画结束后（缩放=1、不透明=1），此时扇区几何完全静止，
        逐帧重画 8 个 QPainterPath + 图标 + 文案没有意义——实测单帧 0.65~0.98ms，
        缓存后同帧只剩一次位图合成 + 动态层（选中扇区光晕、虚线圈、中心文案）。
        """
        try:
            dpr = float(self.devicePixelRatioF()) or 1.0
        except Exception:
            dpr = 1.0
        key = (sel_skip, self.width(), self.height(), round(dpr, 3))
        cache = getattr(self, "_base_cache", None)
        if cache is None:
            self._base_cache = cache = {}
        pm = cache.get(key)
        if pm is not None:
            return pm
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        pm = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        pp = QPainter(pm)
        pp.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform |
                          QPainter.TextAntialiasing, True)
        for i in range(geo["n"]):
            if i == sel_skip:
                continue
            self._draw_sector(pp, geo, i, False, 0.0)
        pp.end()
        cache[key] = pm
        return pm

    def _draw_sector_layer(self, p: QPainter, geo: Dict[str, Any],
                           items: List[Dict[str, Any]], opacity: float,
                           sel: int, hv: float) -> None:
        """以指定 items 绘制整圈扇区（切环过渡时新旧两圈叠加淡出淡入）。"""
        if opacity <= 0.01 or not items:
            return
        p.save()
        p.setOpacity(p.opacity() * max(0.0, min(1.0, opacity)))
        saved = self._items
        self._items = items
        try:
            for i in range(geo["n"]):
                self._draw_sector(p, geo, i, (i == sel), hv if i == sel else 0.0)
        finally:
            self._items = saved
        p.restore()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._visible:
            return
        geo = self._paint_geo()
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform |
                         QPainter.TextAntialiasing, True)

        prog = self._expand                    # 由 QPropertyAnimation 驱动的展开进度
        hv = max(0.0, min(1.2, self._hover))   # 选中扇区悬停程度（含 OutBack 轻微过冲）
        scale = 0.84 + 0.16 * prog
        cx, cy = geo["cx"], geo["cy"]
        sel = self._sel if not self._cancel else -1
        fade = getattr(self, "_ring_fade", 1.0)
        prev_items = getattr(self, "_ring_prev_items", None)

        if fade >= 0.999 or not prev_items:
            cached = None
            if prog >= 0.999:
                cached = self._base_pixmap(sel, geo)
            if cached is not None:
                # 静态层：一次位图合成，省掉 8 个扇区路径 + 图标 + 文案的重绘
                p.drawPixmap(QPoint(0, 0), cached)
                self._draw_cancel_ring(p, geo)
                if sel >= 0:
                    self._draw_sector(p, geo, sel, True, hv)
            else:
                p.setOpacity(0.25 + 0.75 * prog)
                p.translate(cx, cy)
                p.scale(scale, scale)
                p.translate(-cx, -cy)
                self._draw_cancel_ring(p, geo)
                for i in range(geo["n"]):
                    self._draw_sector(p, geo, i, (i == self._sel and not self._cancel), hv)
            self._draw_hub(p, geo)
        else:
            # 切环过渡：旧环淡出 + 新环淡入。过渡期间绕过静态层缓存
            # （缓存里已烘焙旧环的图标与文案），持续 ~190ms，不影响稳态性能。
            p.save()
            p.setOpacity(0.25 + 0.75 * prog)
            p.translate(cx, cy)
            p.scale(scale, scale)
            p.translate(-cx, -cy)
            self._draw_cancel_ring(p, geo)
            self._draw_sector_layer(p, geo, prev_items, 1.0 - fade, -1, 0.0)
            self._draw_sector_layer(p, geo, self._items, fade, sel, hv)
            p.restore()
            self._draw_hub(p, geo, fade)
        p.end()

    def _cfg_alpha(self, key: str, default: int) -> float:
        colors = self.cfg.get("colors") or {}
        try:
            return float(colors.get(key, default))
        except (TypeError, ValueError):
            return float(default)


# ================================================================ 控制器
# ================================================================ 图形配置界面
TYPE_UI = [("app", "程序"), ("file", "文件"), ("folder", "文件夹"),
           ("url", "网址"), ("empty", "空槽位")]
THEME_UI = [("glass", "玻璃"), ("mechanical", "机械"), ("minimal", "极简")]


def guess_item_type(path: str) -> str:
    """按路径推断槽位类型：目录->folder，.url->url，其余可执行/文件->app。"""
    low = (path or "").lower()
    if os.path.isdir(path):
        return "folder"
    if low.endswith(".url"):
        return "url"
    return "app"


_SHORTCUT_TAIL_RE = re.compile(
    r"\s*[-\u2013\u2014]\s*(?:快捷方式|shortcut)\s*(?:[\(（]\s*\d+\s*[\)）])?\s*$",
    re.IGNORECASE)
_URL_HOST_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://([^/?#]+)")


def auto_slot_label(path: str, kind: str = "") -> str:
    """由目标路径推导槽位的默认显示名。

    * 磁盘上真实存在且在「文件夹」类型下 -> 取目录名；
    * 其余文件 -> 取去掉扩展名的主名；
    * .lnk 快捷方式额外去掉尾部「- 快捷方式 / - Shortcut」，如「豆包 - 快捷方式」->「豆包」；
    * 网址 -> 取主机名（去掉 www. 前缀）；
    * 结果截断到 16 字符，与槽位名称字段的历史长度上限保持一致。
    """
    raw_path = str(path or "").strip()
    if not raw_path:
        return ""
    if "://" in raw_path:
        m = _URL_HOST_RE.match(raw_path)
        host = ""
        if m:
            host = m.group(1).split("@")[-1].split(":")[0].strip()
            if host.lower().startswith("www."):
                host = host[4:]
        return (host or raw_path)[:16]
    raw = os.path.basename(raw_path.rstrip("\\/"))
    if not raw:
        return ""
    stem = os.path.splitext(raw)[0]
    is_dir: Optional[bool]
    try:
        is_dir = os.path.isdir(raw_path) if os.path.exists(raw_path) else None
    except OSError:
        is_dir = None
    if is_dir is None:                      # 路径暂时不可判定（未展开的变量等）时按类型推断
        is_dir = str(kind or "").lower() == "folder"
    name = raw if is_dir else stem
    name = _SHORTCUT_TAIL_RE.sub("", name).strip()
    if not name:
        name = stem.strip()
    return name[:16]


def _settings_qss(dark: bool) -> str:
    if dark:
        bg, panel, line, text, sub = "#1A1E28", "#222836", "#333B4C", "#EAF1F8", "#8C97A8"
        accent, hover = "#4FC3F7", "#2C3446"
    else:
        bg, panel, line, text, sub = "#F4F6FA", "#FFFFFF", "#DCE2EC", "#1B1F2A", "#6B7686"
        accent, hover = "#1E88E5", "#E8EEF8"
    return f"""
    QDialog {{ background: {bg}; }}
    QWidget {{ color: {text}; font-family: 'Microsoft YaHei UI'; font-size: 13px; }}
    QLabel#Sub {{ color: {sub}; }}
    QLabel#Title {{ font-size: 15px; font-weight: 600; }}
    QListWidget {{ background: {panel}; border: 1px solid {line}; border-radius: 8px;
                   outline: none; padding: 4px; }}
    QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {hover}; color: {text}; }}
    QListWidget::item:hover {{ background: {hover}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {panel}; border: 1px solid {line}; border-radius: 6px;
        padding: 5px 8px; selection-background-color: {accent}; }}
    /* 下拉框：右侧留出独立箭头区，避免窗口变窄时箭头被文字挤掉 */
    QComboBox {{ padding-right: 26px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
                            width: 22px; border-left: 1px solid {line}; }}
    QComboBox::down-arrow {{ width: 9px; height: 9px; }}
    QComboBox QAbstractItemView {{ background: {panel}; border: 1px solid {line};
                                   selection-background-color: {hover}; color: {text}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {accent}; }}
    QPushButton {{ background: {panel}; border: 1px solid {line}; border-radius: 6px;
                   padding: 6px 12px; }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton#Primary {{ background: {accent}; color: #FFFFFF; border: none;
                           font-weight: 600; padding: 8px 18px; }}
    QPushButton#Primary:hover {{ background: {accent}; }}
    QGroupBox {{ border: 1px solid {line}; border-radius: 8px; margin-top: 14px;
                 padding: 12px 10px 8px 10px; background: {panel}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px;
                        color: {sub}; }}
    QFrame#Preview {{ background: {panel}; border: 1px solid {line}; border-radius: 10px; }}
    QSplitter::handle {{ background: transparent; width: 8px; height: 8px; }}
    /* 每页滚动区：内容装不下时出滚动条，而不是把控件压扁 */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {line}; border-radius: 5px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {sub}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {line}; border-radius: 5px; min-width: 28px; }}
    QScrollBar::handle:horizontal:hover {{ background: {sub}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """


class RingPreview(QWidget):
    """启动器页中部的实时环形预览（点击扇区定位槽位、支持拖入文件）。"""

    slotClicked = pyqtSignal(int)
    filesDropped = pyqtSignal(list)

    def __init__(self, icons: "IconCache", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.icons = icons
        self._items: List[Dict[str, Any]] = []
        self._ring_name = "圆环"
        self._accent = "#4FC3F7"
        self._icon_size = 30
        self._sel = -1
        self._dark = True
        self.setMinimumSize(320, 320)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

    def set_data(self, items: List[Dict[str, Any]], ring_name: str, icon_size: int = 0,
                 accent: str = "", sel: int = -1, dark: bool = True) -> None:
        self._items = list(items or [])
        self._ring_name = ring_name or "圆环"
        self._icon_size = int(icon_size or 0) or 30
        self._accent = accent if QColor(accent).isValid() else "#4FC3F7"
        self._sel = int(sel)
        self._dark = bool(dark)
        self.update()

    # ---------------------------------------------------------- 几何
    def _geo(self) -> Dict[str, Any]:
        n = max(1, len(self._items))
        w, h = self.width(), self.height()
        R = max(40.0, min(w, h) * 0.5 - 14.0)
        r = max(30.0, R * 0.34)
        span = 360.0 / n
        gap = math.degrees(max(0.0, 2.6) / max(1.0, (r + R) * 0.5))
        gap = min(span * 0.42, gap)
        cr = max(0.0, min(8.0, (R - r) * 0.30))
        return {"n": n, "R": R, "r": r, "span": span, "gap": gap, "corner": cr,
                "cx": w * 0.5, "cy": h * 0.5,
                "icon_size": max(14, min(int(self._icon_size), int((R - r) * 0.55)))}

    # ---------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        g = self._geo()
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform |
                         QPainter.TextAntialiasing, True)
        base = QColor("#1B1F2A" if self._dark else "#FFFFFF")
        base.setAlpha(226 if self._dark else 240)
        accent = QColor(self._accent)
        text_col = QColor("#EAF1F8" if self._dark else "#1B1F2A")
        sub_col = QColor("#9AA6B8" if self._dark else "#6B7686")

        # 外圈淡淡的一层玻璃底，突出环带
        halo = QColor(255, 255, 255, 14 if self._dark else 120)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(g["cx"] - g["R"] - 10, g["cy"] - g["R"] - 10,
                             (g["R"] + 10) * 2, (g["R"] + 10) * 2))

        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(max(9, int(g["icon_size"] * 0.34)))
        fm = QFontMetrics(font)

        items = self._items
        for i, item in enumerate(items):
            s_clock, e_clock = _sector_clock_bounds(i, g["n"], g["gap"])
            cr = g["corner"]
            Ro, Ri = g["R"], g["r"]
            if i == self._sel:
                Ro += (g["R"] - g["r"]) * 0.05
            if cr > 0.4:
                Ro -= cr
                Ri += cr
                pad = math.degrees(cr / max(1.0, (Ro + Ri) * 0.5))
                if (e_clock - s_clock) > pad * 2.4:
                    s_clock += pad
                    e_clock -= pad
            q_start = 90.0 - s_clock
            q_span = -(e_clock - s_clock)
            o_rect = QRectF(g["cx"] - Ro, g["cy"] - Ro, Ro * 2, Ro * 2)
            i_rect = QRectF(g["cx"] - Ri, g["cy"] - Ri, Ri * 2, Ri * 2)
            path = QPainterPath()
            path.arcMoveTo(o_rect, q_start)
            path.arcTo(o_rect, q_start, q_span)
            path.arcTo(i_rect, q_start + q_span, -q_span)
            path.closeSubpath()

            empty = str(item.get("type", "empty")).lower() in ("empty", "")
            fill = QColor(accent if i == self._sel else base)
            if i == self._sel:
                fill.setAlpha(205)
            elif empty:
                fill.setAlpha(70 if self._dark else 130)
            if cr > 0.4:
                pen = QPen(fill, cr * 2.0)
                pen.setJoinStyle(Qt.RoundJoin)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
            else:
                p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
            p.setBrush(fill)
            p.drawPath(path)

            # 磨砂玻璃：径向高光
            if not empty:
                p.save()
                p.setClipPath(path)
                p.setPen(Qt.NoPen)
                grad = QLinearGradient(g["cx"], g["cy"] - Ro, g["cx"], g["cy"] + Ro)
                grad.setColorAt(0.0, QColor(255, 255, 255, 40))
                grad.setColorAt(0.5, QColor(255, 255, 255, 12))
                grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setBrush(QBrush(grad))
                p.drawRect(QRectF(g["cx"] - Ro, g["cy"] - Ro, Ro * 2, Ro * 2))
                p.restore()

            mid = math.radians(i * g["span"])
            ux, uy = math.sin(mid), -math.cos(mid)
            icon_r = (g["r"] + g["R"]) * 0.5
            ix, iy = g["cx"] + ux * icon_r, g["cy"] + uy * icon_r
            if empty:
                p.setPen(QPen(QColor(255, 255, 255, 70), 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRectF(ix - 5, iy - 5, 10, 10))
                continue
            pm = self.icons.get(item, g["icon_size"], self._ring_name)
            if pm is not None and not pm.isNull():
                p.drawPixmap(QPointF(ix - pm.width() / 2.0, iy - pm.height() / 2.0), pm)
            label = str(item.get("label", "") or item.get("name", "") or "")
            if label:
                arc_w = max(44.0, (2 * math.pi * icon_r / g["n"]) * 0.94)
                p.setFont(font)
                p.setPen(QColor(255, 255, 255, 240) if i == self._sel else text_col)
                p.drawText(QRectF(ix - arc_w / 2, iy + g["icon_size"] / 2 + 2,
                                  arc_w, fm.height() + 2),
                           Qt.AlignHCenter | Qt.AlignTop,
                           fm.elidedText(label, Qt.ElideRight, int(arc_w)))

        # 中心：环名 + 槽位序号
        hub = QColor(base)
        hub.setAlpha(246)
        p.setPen(QPen(accent if self._sel >= 0 else QColor(255, 255, 255, 46), 1.4))
        p.setBrush(hub)
        p.drawEllipse(QRectF(g["cx"] - g["r"], g["cy"] - g["r"], g["r"] * 2, g["r"] * 2))
        hf = QFont("Microsoft YaHei UI")
        hf.setPixelSize(max(12, int(g["r"] * 0.30)))
        hf.setBold(True)
        p.setFont(hf)
        p.setPen(text_col)
        p.drawText(QRectF(g["cx"] - g["r"], g["cy"] - g["r"] * 0.45, g["r"] * 2, g["r"] * 0.8),
                   Qt.AlignCenter, fm.elidedText(self._ring_name, Qt.ElideRight,
                                                 int(g["r"] * 1.7)))
        sf = QFont("Microsoft YaHei UI")
        sf.setPixelSize(max(9, int(g["r"] * 0.20)))
        p.setFont(sf)
        p.setPen(sub_col)
        tip = ("槽位 %d" % (self._sel + 1)) if self._sel >= 0 else "点击扇区编辑"
        p.drawText(QRectF(g["cx"] - g["r"], g["cy"] + g["r"] * 0.08, g["r"] * 2, g["r"] * 0.5),
                   Qt.AlignCenter, tip)
        p.end()

    def _sector_at(self, pos) -> int:
        g = self._geo()
        dx = pos.x() - g["cx"]
        dy = pos.y() - g["cy"]
        dist = math.hypot(dx, dy)
        if dist < g["r"] * 1.02 or dist > g["R"] + 12:
            return -1
        clock = math.degrees(math.atan2(dx, -dy)) % 360.0
        return int(((clock + g["span"] / 2) % 360) // g["span"]) % g["n"]

    def mousePressEvent(self, event) -> None:  # noqa: N802
        idx = self._sector_at(event.pos())
        if idx >= 0:
            self.slotClicked.emit(idx)

    # ---------------------------------------------------------- 拖拽
    def dragEnterEvent(self, event: "QDragEnterEvent") -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: "QDropEvent") -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if not paths:
            return
        idx = self._sector_at(event.pos())
        if idx >= 0:
            self.slotClicked.emit(idx)
        self.filesDropped.emit(paths)
        event.acceptProposedAction()


class ElideLabel(QLabel):
    """单行文本标签：宽度不够时显示省略号，并用 tooltip 兜住完整内容（绝不硬裁半截字）。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setText(text)

    def setText(self, text: str) -> None:       # noqa: N802 (Qt 命名)
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._apply_elide()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:       # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        fm = QFontMetrics(self.font())
        avail = self.width() - 2
        if avail <= 0 or fm.horizontalAdvance(self._full) <= avail:
            super().setText(self._full)
            return
        super().setText(fm.elidedText(self._full, Qt.ElideRight, avail))


def _wrap_scroll(page: QWidget) -> QScrollArea:
    """把一页设置内容装进滚动区：空间不够出滚动条，而不是把控件压扁。"""
    sc = QScrollArea()
    sc.setFrameShape(QFrame.NoFrame)
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sc.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    sc.setWidget(page)
    return sc


def _fit_control_height(widget: QWidget) -> None:
    """控件高度按字体度量自适应，不写死像素（兼容 100%/125%/150% DPI）。"""
    fm = QFontMetrics(widget.font())
    need = fm.height() + 14
    if widget.minimumHeight() < need:
        widget.setMinimumHeight(need)


def _fit_combo(combo: QComboBox, min_chars: int = 7) -> None:
    """下拉框给足最小宽度：文字 + 内边距 + 独立箭头区，保证箭头不被压扁。"""
    fm = QFontMetrics(combo.font())
    texts = [combo.itemText(i) for i in range(combo.count())]
    content = max([fm.horizontalAdvance(t) for t in texts]
                  + [fm.horizontalAdvance("M" * int(min_chars))])
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(int(min_chars))
    combo.setMinimumWidth(content + 8 + 22 + 26)     # 文本 + 内边距 + 箭头区 + 边框
    _fit_control_height(combo)
    combo.setToolTip(combo.currentText())
    combo.currentIndexChanged.connect(
        lambda _i, c=combo: c.setToolTip(c.currentText()))


def _fit_spin(spin: QWidget, max_text: str = "", min_chars: int = 4) -> None:
    """数值框给足宽度：数字 + 上下按钮区，避免 4 位以上数字被截。"""
    fm = QFontMetrics(spin.font())
    content = max(fm.horizontalAdvance(str(max_text or "")),
                  fm.horizontalAdvance("8" * int(min_chars)))
    need = content + 8 + 34
    if spin.minimumWidth() < need:
        spin.setMinimumWidth(need)
    _fit_control_height(spin)


def _fit_line_edit(edit: QLineEdit, min_chars: int = 16) -> None:
    """输入框给最小宽度，并把完整内容挂到 tooltip（长路径不硬裁）。"""
    fm = QFontMetrics(edit.font())
    need = max(140, fm.horizontalAdvance("M" * int(min_chars)) + 20)
    if edit.minimumWidth() < need:
        edit.setMinimumWidth(need)
    _fit_control_height(edit)
    edit.textChanged.connect(lambda t, w=edit: w.setToolTip(t))


def _polish_form(form: QFormLayout, min_label_width: int = 0) -> int:
    """统一表单行排版：标签列等宽对齐、控件列拉伸占满、行高随字体自适应。

    RowWrapPolicy=WrapLongRows：窗口过窄时标签自动换到控件上方，
    而不是把控件（含下拉箭头）挤扁。
    """
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(10)
    labels: List[QLabel] = []
    for row in range(form.rowCount()):
        it = form.itemAt(row, QFormLayout.LabelRole)
        w = it.widget() if it is not None else None
        if isinstance(w, QLabel) and w.text():
            labels.append(w)
    target = int(min_label_width)
    if labels:
        fm = QFontMetrics(labels[0].font())
        target = max([target] + [fm.horizontalAdvance(w.text()) for w in labels])
        for w in labels:
            w.setMinimumWidth(target)
    return target


class SettingsDialog(QDialog):
    """图形化设置窗口：左侧导航「通用 / 启动器」，中央环形预览，右侧槽位编辑。"""

    applied = pyqtSignal(dict)      # 保存并应用 -> 主程序热更新
    previewRequest = pyqtSignal()   # 保存后顺便唤出圆盘预览

    def __init__(self, config: Dict[str, Any], config_path: str,
                 icons: "IconCache", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("圆盘启动器 · 设置")
        # 标题栏保留 最小化 / 最大化 / 关闭：窗口可自由拉伸，不再锁死尺寸
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowMinimizeButtonHint
                            | Qt.WindowMaximizeButtonHint
                            | Qt.WindowCloseButtonHint
                            | Qt.WindowSystemMenuHint)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)
        self.config_path = config_path
        self.icons = icons
        self.cfg = json.loads(json.dumps(config, ensure_ascii=False))
        self.cfg.pop("items", None)
        self._slot = 0
        self._building = True
        self._label_manual = False      # 当前槽位名称是否为用户手动编辑（手动名不随路径覆盖）
        self._dark = True
        self._fs_geometry_b64 = ""      # 进入 F11 全屏前保存的几何，退出时还原
        self._fs_prev_state = Qt.WindowNoState
        _, mode = resolve_colors(self.cfg)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 左导航 + 右页面：用分栏代替固定宽度，窗口拉宽时内容整体铺开
        self._body = QSplitter(Qt.Horizontal, self)
        self._body.setChildrenCollapsible(False)
        self._body.setHandleWidth(8)
        root.addWidget(self._body, 1)

        self.nav = QListWidget(self._body)
        self.nav.setMinimumWidth(self._nav_min_width())
        self.nav.setMaximumWidth(self._nav_max_width())
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setWordWrap(True)
        for text in ("通用", "启动器"):
            self.nav.addItem(QListWidgetItem(text))
        self.nav.setCurrentRow(1)
        self._body.addWidget(self.nav)

        self.stack = QStackedWidget(self._body)
        self.stack.setMinimumWidth(self._stack_min_width())
        self._body.addWidget(self.stack)
        self._body.setStretchFactor(0, 0)       # 导航列：宽度基本不变
        self._body.setStretchFactor(1, 1)       # 内容列：吃掉全部富余空间
        self._body.setSizes([self._nav_default_width(), 1000])

        # 每个设置页各自套一层 QScrollArea：小窗口时出滚动条而不是挤压控件
        self._page_widgets: List[QWidget] = []
        for builder in (self._build_general_page, self._build_launcher_page):
            page = builder()
            self._page_widgets.append(page)
            self.stack.addWidget(_wrap_scroll(page))
        # 内容区最小宽度 = 各页真实最小宽度（含滚动条余量），据此定窗口最小尺寸
        self._stack_need = max(
            560, max(p.minimumSizeHint().width() for p in self._page_widgets) + 24)
        self.stack.setMinimumWidth(self._stack_need)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.currentRowChanged.connect(lambda _r: self._refresh_preview())
        # 构造时 nav 已选中「启动器」，信号后连，这里显式对齐一次堆栈
        self.stack.setCurrentIndex(self.nav.currentRow())

        # 底部：状态 + 操作（长提示自动省略号 + tooltip，不硬裁）
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.lbl_status = ElideLabel("修改后点「保存并应用」，运行中的圆盘立即生效（无需重启）", self)
        self.lbl_status.setObjectName("Sub")
        self.lbl_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bar.addWidget(self.lbl_status, 1)
        btn_reset = QPushButton("恢复默认", self)
        btn_reset.clicked.connect(self._restore_default)
        bar.addWidget(btn_reset)
        btn_cancel = QPushButton("关闭", self)
        btn_cancel.clicked.connect(self.close)
        bar.addWidget(btn_cancel)
        self.btn_apply = QPushButton("保存并应用", self)
        self.btn_apply.setObjectName("Primary")
        self.btn_apply.clicked.connect(self.save_and_apply)
        bar.addWidget(self.btn_apply)
        root.addLayout(bar)

        self._apply_theme(mode)
        self._fill_general_fields()
        self._building = False
        self._reload_ring_list()
        self._load_slot(self._slot)
        self._init_window_geometry()

    # ======================================================== 窗口尺寸 / 分栏
    def _nav_default_width(self) -> int:
        fm = QFontMetrics(self.font())
        return max(148, fm.horizontalAdvance("启动器") + 72)

    def _nav_min_width(self) -> int:
        fm = QFontMetrics(self.font())
        return max(120, fm.horizontalAdvance("启动器") + 48)

    def _nav_max_width(self) -> int:
        fm = QFontMetrics(self.font())
        return max(240, fm.horizontalAdvance("启动器") + 140)

    def _stack_min_width(self) -> int:
        """内容区最小宽度：按各页真实最小宽度算，保证最窄时不出横向滚动条。"""
        need = int(getattr(self, "_stack_need", 0) or 0)
        if need > 0:
            return need
        fm = QFontMetrics(self.font())
        return max(560, fm.horizontalAdvance("M") * 46)

    def _design_min_size(self) -> QSize:
        avail = self._available_geometry()
        fm = QFontMetrics(self.font())
        w = self._nav_min_width() + self._stack_min_width() + 56
        h = max(520, int(fm.height() * 27))
        return QSize(min(w, max(480, avail.width() - 24)),
                     min(h, max(360, avail.height() - 24)))

    def _available_geometry(self) -> QRect:
        scr = self.screen() if hasattr(self, "screen") else None
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr.availableGeometry() if scr is not None else QRect(0, 0, 1280, 800)

    def _initial_size(self, avail: QRect) -> QSize:
        w = min(1200, max(self.minimumWidth(), int(avail.width() * 0.72)))
        h = min(860, max(self.minimumHeight(), int(avail.height() * 0.78)))
        return QSize(w, h)

    def _geometry_visible(self) -> bool:
        """恢复的几何是否仍落在真实存在的屏幕上（防拔掉副屏后窗口跑到屏幕外）。"""
        rect = self.frameGeometry()
        if rect.width() < 260 or rect.height() < 200:
            return False
        area = float(rect.width() * rect.height())
        for scr in QGuiApplication.screens():
            inter = scr.availableGeometry().intersected(rect)
            if inter.width() < min(260, rect.width()) or inter.height() < 90:
                continue
            if inter.width() * inter.height() >= area * 0.35:
                return True
        return False

    def _init_window_geometry(self) -> None:
        self.setMinimumSize(self._design_min_size())
        avail = self._available_geometry()
        ui = self.cfg.get("ui") or {}
        restored = False
        geo_b64 = str(ui.get("geometry") or "")
        if geo_b64:
            try:
                restored = bool(self.restoreGeometry(
                    QByteArray.fromBase64(geo_b64.encode("ascii"))))
            except Exception:
                restored = False
        if not restored or not self._geometry_visible():
            # 没有历史记录 / 历史位置已越界 -> 按屏幕大小给一个合适初值并居中
            self.resize(self._initial_size(avail))
            frame = self.frameGeometry()
            self.move(max(avail.left(), avail.center().x() - frame.width() // 2),
                      max(avail.top(), avail.center().y() - frame.height() // 2))
        # 分栏比例：历史值做成对夹取，超出合法区间就回退默认
        nav_w = 0
        try:
            nav_w = int(ui.get("nav_width") or 0)
        except (TypeError, ValueError):
            nav_w = 0
        total = max(self.width(), 700)
        if nav_w <= 0:
            nav_w = self._nav_default_width()
        nav_w = max(self._nav_min_width(), min(self._nav_max_width(), nav_w))
        self._body.setSizes([nav_w, max(360, total - nav_w)])
        inner = ui.get("splitter")
        if isinstance(inner, list) and len(inner) == 3:
            try:
                vals = [max(1, int(v)) for v in inner]
                self._page_splitter.setSizes(vals)
            except (TypeError, ValueError):
                pass
        if bool(ui.get("maximized")):
            self.setWindowState(self.windowState() | Qt.WindowMaximized)
        # F11 全屏 / 还原（Esc 由 QDialog 默认关闭窗口）
        self._shortcut_f11 = QShortcut(QKeySequence("F11"), self)
        self._shortcut_f11.setContext(Qt.WidgetWithChildrenShortcut)
        self._shortcut_f11.activated.connect(self._toggle_fullscreen)

    def _toggle_fullscreen(self) -> None:
        """F11：全屏 / 还原（保留进入前的几何与最大化状态）。"""
        if self.isFullScreen():
            self.setWindowState(self._fs_prev_state or Qt.WindowNoState)
        else:
            self._fs_geometry_b64 = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self._fs_prev_state = self.windowState()
            self.setWindowState(self.windowState() | Qt.WindowFullScreen)

    def _window_state_dict(self) -> Dict[str, Any]:
        geo_b64 = self._fs_geometry_b64 if self.isFullScreen() else bytes(
            self.saveGeometry().toBase64()).decode("ascii")
        maximized = bool(self.isMaximized()) or (
            self.isFullScreen() and bool(self._fs_prev_state & Qt.WindowMaximized))
        sizes = self._body.sizes()
        return {
            "geometry": geo_b64 or "",
            "maximized": maximized,
            "nav_width": int(sizes[0]) if sizes else 0,
            "splitter": [int(v) for v in self._page_splitter.sizes()],
        }

    def _persist_window_state(self) -> None:
        """把窗口尺寸/位置/最大化写回配置文件；只动 ui 段，不动未保存的表单修改。"""
        data: Any = {}
        try:
            if os.path.isfile(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception as exc:
            log.warning("读取配置失败，改用内存配置保存窗口状态：%s", exc)
            data = {}
        if not isinstance(data, dict) or not data.get("rings"):
            data = json.loads(json.dumps(self.cfg, ensure_ascii=False))
        data["ui"] = self._window_state_dict()
        ok, msg = save_config(self.config_path, data)
        if not ok:
            log.warning("窗口状态写入失败：%s", msg)

    def _refit_controls(self) -> None:
        """按真实字体度量重算控件最小尺寸（样式表在 show 后才生效，构造期算偏小）。"""
        for combo in self.findChildren(QComboBox):
            fm = QFontMetrics(combo.font())
            texts = [combo.itemText(i) for i in range(combo.count())]
            chars = max(1, int(combo.minimumContentsLength() or 7))
            content = max([fm.horizontalAdvance(t) for t in texts]
                          + [fm.horizontalAdvance("M" * chars)])
            combo.setMinimumWidth(content + 8 + 22 + 26)
            _fit_control_height(combo)
        for spin in self.findChildren(QAbstractSpinBox):
            _fit_control_height(spin)
        for edit in self.findChildren(QLineEdit):
            _fit_control_height(edit)
        for btn in self.findChildren(QPushButton):
            _fit_control_height(btn)
        for gb in self.findChildren(QGroupBox):
            lay = gb.layout()
            if isinstance(lay, QFormLayout):
                _polish_form(lay)

    def showEvent(self, event) -> None:          # noqa: N802 (Qt 命名)
        super().showEvent(event)
        if not getattr(self, "_refit_done", False):
            self._refit_done = True
            try:
                self._refit_controls()
            except Exception as exc:
                log.warning("控件自适应尺寸重算失败：%s", exc)

    def closeEvent(self, event) -> None:        # noqa: N802 (Qt 命名)
        try:
            self._persist_window_state()
        except Exception as exc:
            log.warning("窗口状态保存异常：%s", exc)
        super().closeEvent(event)

    # ============================================================ 通用页
    def _build_general_page(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 2, 12, 2)
        lay.setSpacing(12)
        label_w = 0     # 三张分组表统一标签列宽，纵向对齐

        # 外观
        gb1 = QGroupBox("外观主题", page)
        f1 = QFormLayout(gb1)
        self.cmb_theme = QComboBox(gb1)
        self.cmb_theme.addItem("跟随系统", "auto")
        self.cmb_theme.addItem("深色", "dark")
        self.cmb_theme.addItem("浅色", "light")
        _fit_combo(self.cmb_theme, 8)
        f1.addRow("主题模式", self.cmb_theme)
        self.chk_frost = QCheckBox("磨砂玻璃质感（半透明 + 内高光）", gb1)
        f1.addRow("", self.chk_frost)
        self.cmb_accent = QComboBox(gb1)
        for name, hexv in (("青蓝", "#4FC3F7"), ("薄荷", "#4FD1C5"), ("橙金", "#FFC24F"),
                           ("紫罗兰", "#B18CFF"), ("珊瑚", "#FF8A65")):
            self.cmb_accent.addItem(name, hexv)
        _fit_combo(self.cmb_accent, 8)
        f1.addRow("强调色", self.cmb_accent)
        self.spin_sectors = QSpinBox(gb1)
        self.spin_sectors.setRange(3, 16)
        _fit_spin(self.spin_sectors, "16")
        f1.addRow("扇区数量", self.spin_sectors)
        self.spin_icon = QSpinBox(gb1)
        self.spin_icon.setRange(16, 64)
        _fit_spin(self.spin_icon, "64")
        f1.addRow("全局图标大小", self.spin_icon)
        self.spin_radius = QSpinBox(gb1)
        self.spin_radius.setRange(110, 320)
        _fit_spin(self.spin_radius, "320")
        f1.addRow("圆盘半径", self.spin_radius)
        self.spin_gap = QDoubleSpinBox(gb1)
        self.spin_gap.setRange(0.0, 8.0)
        self.spin_gap.setSingleStep(0.4)
        self.spin_gap.setDecimals(1)
        _fit_spin(self.spin_gap, "8.0")
        f1.addRow("扇区间隙", self.spin_gap)
        label_w = max(label_w, _polish_form(f1, label_w))
        lay.addWidget(gb1)

        # 音效
        gb2 = QGroupBox("音效", page)
        f2 = QFormLayout(gb2)
        self.cmb_sound = QComboBox(gb2)
        for key, label in THEME_UI:
            self.cmb_sound.addItem(label, key)
        _fit_combo(self.cmb_sound, 8)
        f2.addRow("音效主题", self.cmb_sound)
        self.slider_vol = QSlider(Qt.Horizontal, gb2)
        self.slider_vol.setRange(0, 100)
        _fit_control_height(self.slider_vol)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.slider_vol, 1)
        self.lbl_vol = QLabel("55%", gb2)
        # 数值标签按字体度量给最小宽度（"100%" 也不会被截）
        self.lbl_vol.setMinimumWidth(
            QFontMetrics(self.lbl_vol.font()).horizontalAdvance("100%") + 10)
        self.lbl_vol.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.lbl_vol, 0)
        holder = QWidget(gb2)
        holder.setLayout(row)
        f2.addRow("音量", holder)
        self.slider_vol.valueChanged.connect(lambda v: self.lbl_vol.setText("%d%%" % v))
        self.chk_mute = QCheckBox("静音", gb2)
        f2.addRow("", self.chk_mute)
        self.chk_sound = QCheckBox("启用音效", gb2)
        f2.addRow("", self.chk_sound)
        label_w = max(label_w, _polish_form(f2, label_w))
        lay.addWidget(gb2)

        # 交互
        gb3 = QGroupBox("交互与热键", page)
        f3 = QFormLayout(gb3)
        self.chk_anim = QCheckBox("启用动画（展开 / 悬停 / 切环）", gb3)
        f3.addRow("", self.chk_anim)
        self.edit_hotkey = QLineEdit(gb3)
        self.edit_hotkey.setPlaceholderText("例：Ctrl+Alt+Space")
        _fit_line_edit(self.edit_hotkey, 14)
        f3.addRow("全局热键", self.edit_hotkey)
        self.chk_hotkey = QCheckBox("启用全局热键", gb3)
        f3.addRow("", self.chk_hotkey)
        self.chk_wheel = QCheckBox("滚轮切换圆环", gb3)
        f3.addRow("", self.chk_wheel)
        self.chk_keys = QCheckBox("按 [ ] / Tab 切换圆环", gb3)
        f3.addRow("", self.chk_keys)
        label_w = max(label_w, _polish_form(f3, label_w))
        lay.addWidget(gb3)
        lay.addStretch(1)
        return page

    # ============================================================ 启动器页
    def _build_launcher_page(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(2, 2, 12, 2)
        outer.setSpacing(0)
        fm = QFontMetrics(self.font())
        # 三栏用分栏承载：中间预览与右侧编辑等比吞掉富余宽度，最窄也压不扁
        self._page_splitter = QSplitter(Qt.Horizontal, page)
        self._page_splitter.setChildrenCollapsible(False)
        self._page_splitter.setHandleWidth(8)
        self._page_splitter.setStretchFactor(0, 0)
        self._page_splitter.setStretchFactor(1, 1)
        self._page_splitter.setStretchFactor(2, 1)
        outer.addWidget(self._page_splitter)

        # ---- 左：圆环管理
        left = QWidget(self._page_splitter)
        left.setMinimumWidth(max(196, fm.horizontalAdvance("本环图标大小（0=跟随全局）") + 36))
        left.setMaximumWidth(max(340, left.minimumWidth() + 120))
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        t1 = QLabel("圆环", left)
        t1.setObjectName("Title")
        lv.addWidget(t1)
        self.ring_list = QListWidget(left)
        self.ring_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        lv.addWidget(self.ring_list, 1)
        self.ring_list.currentRowChanged.connect(self._on_ring_row_changed)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        btn_add = QPushButton("新增", left)
        btn_add.clicked.connect(self._add_ring)
        btn_del = QPushButton("删除", left)
        btn_del.clicked.connect(self._del_ring)
        row1.addWidget(btn_add)
        row1.addWidget(btn_del)
        lv.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        btn_up = QPushButton("上移", left)
        btn_up.clicked.connect(lambda: self._move_ring(-1))
        btn_down = QPushButton("下移", left)
        btn_down.clicked.connect(lambda: self._move_ring(1))
        row2.addWidget(btn_up)
        row2.addWidget(btn_down)
        lv.addLayout(row2)

        lv.addWidget(QLabel("环名称", left))
        self.edit_ring_name = QLineEdit(left)
        self.edit_ring_name.setPlaceholderText("环名称")
        _fit_line_edit(self.edit_ring_name, 10)
        self.edit_ring_name.textEdited.connect(self._on_ring_name_edited)
        lv.addWidget(self.edit_ring_name)
        lv.addWidget(QLabel("本环图标大小（0=跟随全局）", left))
        self.spin_ring_icon = QSpinBox(left)
        self.spin_ring_icon.setRange(0, 64)
        _fit_spin(self.spin_ring_icon, "64")
        self.spin_ring_icon.valueChanged.connect(self._on_ring_icon_changed)
        lv.addWidget(self.spin_ring_icon)
        btn_ring_color = QPushButton("本环强调色…", left)
        btn_ring_color.clicked.connect(self._pick_ring_accent)
        lv.addWidget(btn_ring_color)
        self._page_splitter.addWidget(left)

        # ---- 中：实时预览（可随窗口一起放大，不锁死尺寸）
        mid = QWidget(self._page_splitter)
        mid.setMinimumWidth(max(280, fm.horizontalAdvance("M") * 24))
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(8)
        self.preview = RingPreview(self.icons, mid)
        self.preview.setObjectName("Preview")
        self.preview.setMinimumSize(240, 240)
        self.preview.slotClicked.connect(self._select_slot)
        self.preview.filesDropped.connect(self._on_files_dropped)
        mv.addWidget(self.preview, 1)
        self.lbl_preview_tip = QLabel("点击某个扇区即可编辑该槽位；也可把 exe / 快捷方式 / 文件夹直接拖到扇区上", mid)
        self.lbl_preview_tip.setObjectName("Sub")
        self.lbl_preview_tip.setWordWrap(True)
        mv.addWidget(self.lbl_preview_tip)
        self._page_splitter.addWidget(mid)

        # ---- 右：槽位编辑
        right = QWidget(self._page_splitter)
        right.setMinimumWidth(max(300, fm.horizontalAdvance("启动参数（可选）") + 90))
        right.setMaximumWidth(max(520, right.minimumWidth() + 200))
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        t2 = QLabel("槽位编辑", right)
        t2.setObjectName("Title")
        rv.addWidget(t2)
        gb = QGroupBox(right)
        gf = QFormLayout(gb)
        self.lbl_slot = QLabel("槽位 1", gb)
        gf.addRow(self.lbl_slot)
        self.cmb_type = QComboBox(gb)
        for key, label in TYPE_UI:
            self.cmb_type.addItem(label, key)
        _fit_combo(self.cmb_type, 8)
        gf.addRow("类型", self.cmb_type)
        self.edit_label = QLineEdit(gb)
        self.edit_label.setPlaceholderText("显示名称")
        _fit_line_edit(self.edit_label, 10)
        gf.addRow("名称", self.edit_label)
        self.edit_target = QLineEdit(gb)
        self.edit_target.setPlaceholderText("程序 / 文件 / 文件夹 / 网址")
        _fit_line_edit(self.edit_target, 12)
        gf.addRow("路径", self.edit_target)
        roww = QHBoxLayout()
        roww.setContentsMargins(0, 0, 0, 0)
        roww.setSpacing(8)
        btn_file = QPushButton("浏览文件…", gb)
        btn_file.clicked.connect(self._browse_file)
        btn_dir = QPushButton("文件夹…", gb)
        btn_dir.clicked.connect(self._browse_dir)
        roww.addWidget(btn_file)
        roww.addWidget(btn_dir)
        holder = QWidget(gb)
        holder.setLayout(roww)
        gf.addRow("", holder)
        self.edit_args = QLineEdit(gb)
        self.edit_args.setPlaceholderText("启动参数（可选）")
        _fit_line_edit(self.edit_args, 10)
        gf.addRow("参数", self.edit_args)
        self.edit_hotkey_slot = QLineEdit(gb)
        self.edit_hotkey_slot.setPlaceholderText("如 1 / F5（可选）")
        _fit_line_edit(self.edit_hotkey_slot, 8)
        gf.addRow("快捷键", self.edit_hotkey_slot)
        self.edit_icon = QLineEdit(gb)
        self.edit_icon.setPlaceholderText("自定义图标图片（可选）")
        _fit_line_edit(self.edit_icon, 10)
        gf.addRow("图标", self.edit_icon)
        rowi = QHBoxLayout()
        rowi.setContentsMargins(0, 0, 0, 0)
        rowi.setSpacing(8)
        btn_icon = QPushButton("选择图标…", gb)
        btn_icon.clicked.connect(self._browse_icon)
        btn_icon_clear = QPushButton("清除", gb)
        btn_icon_clear.clicked.connect(lambda: self.edit_icon.setText(""))
        rowi.addWidget(btn_icon)
        rowi.addWidget(btn_icon_clear)
        holder2 = QWidget(gb)
        holder2.setLayout(rowi)
        gf.addRow("", holder2)
        _polish_form(gf)
        rv.addWidget(gb)
        btn_clear_slot = QPushButton("清空该槽位", right)
        btn_clear_slot.clicked.connect(self._clear_slot)
        rv.addWidget(btn_clear_slot)
        rv.addStretch(1)
        lbl_tip = QLabel("提示：修改后记得点右下角「保存并应用」", right)
        lbl_tip.setObjectName("Sub")
        lbl_tip.setWordWrap(True)
        rv.addWidget(lbl_tip)
        self._page_splitter.addWidget(right)

        self._page_splitter.setSizes([left.minimumWidth(),
                                      max(320, int(self.width() * 0.3)),
                                      max(320, int(self.width() * 0.3))])

        # 字段联动（类型/路径变化时先同步名称，再落盘到内存配置）
        self.cmb_type.currentIndexChanged.connect(self._on_type_changed)
        self.edit_label.textEdited.connect(self._on_label_edited)
        self.edit_target.textEdited.connect(self._on_target_edited)
        self.edit_args.textEdited.connect(self._on_slot_edited)
        self.edit_hotkey_slot.textEdited.connect(self._on_slot_edited)
        self.edit_icon.textEdited.connect(self._on_slot_edited)
        return page

    # ============================================================ 主题
    def _apply_theme(self, mode: str) -> None:
        self._dark = (mode == "dark")
        self.setStyleSheet(_settings_qss(self._dark))
        if getattr(self, "_refit_done", False):
            # 主题样式可能改变字体度量，换肤后按新度量重算控件最小尺寸
            try:
                self._refit_controls()
            except Exception as exc:
                log.warning("换肤后控件重算失败：%s", exc)

    def _theme_mode(self) -> str:
        theme = self.cfg.get("theme") or {}
        if bool(theme.get("follow_system", True)):
            return "dark" if system_theme_mode() == "dark" else "light"
        mode = str(theme.get("mode", "dark"))
        return "dark" if mode != "light" else "light"

    # ============================================================ 载入
    def _reload_ring_list(self) -> None:
        rings = ring_list(self.cfg)
        cur = ring_index(self.cfg)
        self.ring_list.blockSignals(True)
        self.ring_list.clear()
        for i, ring in enumerate(rings):
            item = QListWidgetItem("%s（%d 项）" % (
                ring.get("name") or ("圆环 %d" % (i + 1)),
                len([x for x in (ring.get("items") or [])
                     if str(x.get("type", "empty")) not in ("empty", "")])))
            self.ring_list.addItem(item)
        self.ring_list.setCurrentRow(max(0, min(len(rings) - 1, cur)))
        self.ring_list.blockSignals(False)
        self._load_ring_fields()
        self._refresh_preview()

    def _load_ring_fields(self) -> None:
        rings = ring_list(self.cfg)
        if not rings:
            return
        idx = max(0, min(len(rings) - 1, self.ring_list.currentRow()))
        ring = rings[idx]
        self._building = True
        self.edit_ring_name.setText(str(ring.get("name", "")))
        self.spin_ring_icon.setValue(int(ring.get("icon_size", 0) or 0))
        self._building = False

    def _refresh_preview(self) -> None:
        if self._building:
            return
        rings = ring_list(self.cfg)
        if not rings:
            return
        idx = max(0, min(len(rings) - 1, self.ring_list.currentRow()))
        ring = rings[idx]
        appear = self.cfg.get("appearance") or {}
        try:
            icon_size = int(ring.get("icon_size") or 0) or int(self.cfg.get("icon_size", 30))
        except (TypeError, ValueError):
            icon_size = 30
        accent = str(ring.get("accent") or (self.cfg.get("colors") or {}).get("accent", "#4FC3F7"))
        self.preview.set_data(ring.get("items") or [], str(ring.get("name", "")),
                             icon_size, accent, self._slot, self._dark)
        self.preview.update()

    def _load_slot(self, slot: int) -> None:
        rings = ring_list(self.cfg)
        if not rings:
            return
        idx = max(0, min(len(rings) - 1, self.ring_list.currentRow()))
        items = rings[idx].get("items") or []
        if not items:
            return
        slot = max(0, min(len(items) - 1, int(slot)))
        self._slot = slot
        item = normalize_item(items[slot], slot)
        items[slot] = item
        self._building = True
        self.lbl_slot.setText("槽位 %d / %d" % (slot + 1, len(items)))
        self.cmb_type.setCurrentIndex(max(0, [k for k, _ in TYPE_UI].index(
            item.get("type", "empty")) if item.get("type", "empty") in
            [k for k, _ in TYPE_UI] else 0))
        label = str(item.get("label", ""))
        auto = auto_slot_label(str(item.get("target", "")), str(item.get("type", "")))
        if not label.strip() and auto and str(item.get("type", "")) != "empty":
            # 历史配置里名称为空但有目标的槽位，按路径补出默认名
            label = auto
            item["label"] = label
        # 名称与路径推导出的默认名不一致 => 视为用户手动命名，之后换路径不覆盖
        self._label_manual = bool(label.strip()) and bool(auto) and label.strip() != auto
        self.edit_label.setText(label)
        self.edit_target.setText(str(item.get("target", "")))
        self.edit_args.setText(str(item.get("args", "")))
        self.edit_hotkey_slot.setText(str(item.get("hotkey", "")))
        self.edit_icon.setText(str(item.get("icon", "")))
        self._building = False
        self._refresh_preview()

    def _current_ring(self) -> Dict[str, Any]:
        rings = ring_list(self.cfg)
        idx = max(0, min(len(rings) - 1, self.ring_list.currentRow())) if rings else 0
        return rings[idx] if rings else {}

    # ============================================================ 事件
    def _on_ring_row_changed(self, row: int) -> None:
        if self._building or row < 0:
            return
        self.cfg["current_ring"] = row
        self._slot = 0
        self._load_ring_fields()
        self._load_slot(0)

    def _on_ring_name_edited(self, text: str) -> None:
        if self._building:
            return
        ring = self._current_ring()
        ring["name"] = text.strip()[:40] or "未命名环"
        row = self.ring_list.currentRow()
        if row >= 0:
            item = self.ring_list.item(row)
            if item:
                cnt = len([x for x in (ring.get("items") or [])
                           if str(x.get("type", "empty")) not in ("empty", "")])
                item.setText("%s（%d 项）" % (ring["name"], cnt))
        self._refresh_preview()

    def _on_ring_icon_changed(self, value: int) -> None:
        if self._building:
            return
        self._current_ring()["icon_size"] = int(value)
        self._refresh_preview()

    def _pick_ring_accent(self) -> None:
        ring = self._current_ring()
        cur = QColor(str(ring.get("accent") or (self.cfg.get("colors") or {}).get(
            "accent", "#4FC3F7")))
        col = QColorDialog.getColor(cur, self, "选择本环强调色")
        if col.isValid():
            ring["accent"] = col.name()
            self._refresh_preview()

    # ------------------------------------------------------------ 名称自动跟随路径
    def _sync_auto_label(self, force: bool = False, name_path: str = "") -> None:
        """按「类型 + 路径」刷新名称框：未手动命名（或 force=True）时填基础名。

        name_path: 需要按另一路径取名时传入（如 .url 已被解析成网址，仍按 .url 文件名取名）。
        """
        if not force and self._label_manual:
            return
        kind = str(self.cmb_type.currentData() or "app")
        src = name_path or self.edit_target.text().strip()
        auto = "" if kind == "empty" else auto_slot_label(src, kind)
        if self.edit_label.text() == auto:
            self._label_manual = False
            return
        self.edit_label.setText(auto)
        self._label_manual = False

    def _on_label_edited(self, _text: str) -> None:
        if self._building:
            return
        # 用户手填名称 -> 标记手动；手工清空则恢复自动跟随
        self._label_manual = bool(self.edit_label.text().strip())
        self._on_slot_edited()

    def _on_target_edited(self, _text: str) -> None:
        if self._building:
            return
        self._sync_auto_label()     # 手填/粘贴路径也自动带出名称
        self._on_slot_edited()

    def _on_type_changed(self, _index: int) -> None:
        if self._building:
            return
        if str(self.cmb_type.currentData() or "app") == "empty":
            self._clear_slot()      # 切到「空槽位」等同清空
            return
        self._sync_auto_label()
        self._on_slot_edited()

    def _on_slot_edited(self, *_args) -> None:
        if self._building:
            return
        ring = self._current_ring()
        items = ring.get("items") or []
        if not items:
            return
        slot = max(0, min(len(items) - 1, self._slot))
        kind = str(self.cmb_type.currentData() or "app")
        item = normalize_item({
            "label": self.edit_label.text().strip(),
            "type": kind,
            "target": self.edit_target.text().strip(),
            "args": self.edit_args.text().strip(),
            "hotkey": self.edit_hotkey_slot.text().strip(),
            "icon": self.edit_icon.text().strip(),
        }, slot)
        items[slot] = item
        row = self.ring_list.currentRow()
        if row >= 0:
            lst = self.ring_list.item(row)
            if lst:
                cnt = len([x for x in items if str(x.get("type", "empty")) not in ("empty", "")])
                lst.setText("%s（%d 项）" % (ring.get("name") or "圆环", cnt))
        self.icons.clear()      # 目标/图标变化后作废图标缓存，保证预览与圆盘一致
        self._refresh_preview()

    def _select_slot(self, slot: int) -> None:
        self._load_slot(slot)

    def _on_files_dropped(self, paths: List[str]) -> None:
        if not paths:
            return
        path = paths[0]
        name_src = path                      # 取名仍以拖入的文件为准（.url 解析后 path 变成网址）
        kind = guess_item_type(path)
        if kind == "url":
            try:
                info = resolve_lnk(path)
                if info and info.get("target"):
                    path = info["target"]
                    kind = "url"
            except Exception:
                pass
        self._building = True
        self.cmb_type.setCurrentIndex(max(0, [k for k, _ in TYPE_UI].index(kind)))
        self.edit_target.setText(path)
        self._building = False
        self._sync_auto_label(name_path=name_src)   # 未手动命名时，名称自动跟随拖入的文件
        self._on_slot_edited()
        self.lbl_status.setText("已把「%s」填入槽位 %d，点「保存并应用」生效"
                                % (os.path.basename(path), self._slot + 1))

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序或文件", "",
            "程序与快捷方式 (*.exe *.lnk *.bat *.cmd *.url);;所有文件 (*.*)")
        if path:
            self._building = True
            self.edit_target.setText(path.replace("/", os.sep))
            self.cmb_type.setCurrentIndex(max(0, [k for k, _ in TYPE_UI].index(
                guess_item_type(path))))
            self._building = False
            self._sync_auto_label()     # 未手动命名时，名称自动跟随所选文件
            self._on_slot_edited()

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self._building = True
            self.edit_target.setText(path.replace("/", os.sep))
            self.cmb_type.setCurrentIndex(max(0, [k for k, _ in TYPE_UI].index("folder")))
            self._building = False
            self._sync_auto_label()     # 未手动命名时，名称自动跟随所选文件夹
            self._on_slot_edited()

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图标文件", "", "图片 (*.png *.ico *.jpg *.jpeg *.bmp *.svg)")
        if path:
            self.edit_icon.setText(path.replace("/", os.sep))
            self._on_slot_edited()

    def _clear_slot(self) -> None:
        self._building = True
        self.cmb_type.setCurrentIndex([k for k, _ in TYPE_UI].index("empty"))
        self.edit_label.clear()
        self.edit_target.clear()
        self.edit_args.clear()
        self.edit_hotkey_slot.clear()
        self.edit_icon.clear()
        self._label_manual = False      # 清空后恢复自动命名
        self._building = False
        self._on_slot_edited()

    # ------------------------------------------------------------ 环增删改序
    def _add_ring(self) -> None:
        rings = ring_list(self.cfg)
        if len(rings) >= MAX_RINGS:
            self.lbl_status.setText("圆环数量已达上限 %d" % MAX_RINGS)
            return
        sectors = int(self.cfg.get("sectors", 8) or 8)
        rings.append(normalize_ring({"name": "圆环 %d" % (len(rings) + 1)}, len(rings), sectors))
        self.cfg["current_ring"] = len(rings) - 1
        self._reload_ring_list()
        self.ring_list.setCurrentRow(len(rings) - 1)
        self._slot = 0
        self._load_slot(0)

    def _del_ring(self) -> None:
        rings = ring_list(self.cfg)
        if len(rings) <= 1:
            self.lbl_status.setText("至少保留一个圆环")
            return
        idx = self.ring_list.currentRow()
        if idx < 0:
            return
        name = rings[idx].get("name", "")
        self.lbl_status.setText("已删除圆环「%s」（点「保存并应用」后写入配置）" % name)
        rings.pop(idx)
        self.cfg["current_ring"] = max(0, min(len(rings) - 1, idx))
        self._reload_ring_list()
        self._slot = 0
        self._load_slot(0)

    def _move_ring(self, delta: int) -> None:
        rings = ring_list(self.cfg)
        idx = self.ring_list.currentRow()
        nxt = idx + int(delta)
        if idx < 0 or nxt < 0 or nxt >= len(rings):
            return
        rings[idx], rings[nxt] = rings[nxt], rings[idx]
        self.cfg["current_ring"] = nxt
        self._reload_ring_list()
        self.ring_list.setCurrentRow(nxt)

    def _restore_default(self) -> None:
        self.cfg = json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
        self.cfg.pop("items", None)
        self._slot = 0
        self._fill_general_fields()
        self._reload_ring_list()
        self._load_slot(0)
        self.lbl_status.setText("已恢复默认配置（尚未写入）")

    # ============================================================ 保存
    def _fill_general_fields(self) -> None:
        self._building = True
        theme = self.cfg.get("theme") or {}
        if bool(theme.get("follow_system", True)):
            mode = "auto"
        else:
            mode = "light" if str(theme.get("mode", "dark")) == "light" else "dark"
        self.cmb_theme.setCurrentIndex(max(0, [self.cmb_theme.itemData(i)
                                               for i in range(self.cmb_theme.count())
                                               ].index(mode)) if mode in [
            self.cmb_theme.itemData(i) for i in range(self.cmb_theme.count())] else 0)
        appear = self.cfg.get("appearance") or {}
        self.chk_frost.setChecked(bool(appear.get("frosted", True)))
        accent = str((self.cfg.get("colors") or {}).get("accent", "#4FC3F7"))
        idx = self.cmb_accent.findData(accent)
        self.cmb_accent.setCurrentIndex(idx if idx >= 0 else 0)
        self.spin_sectors.setValue(int(self.cfg.get("sectors", 8) or 8))
        self.spin_icon.setValue(int(self.cfg.get("icon_size", 32) or 32))
        self.spin_radius.setValue(int(self.cfg.get("radius", 168) or 168))
        try:
            gap = float(appear.get("gap_px", 3.2))
        except (TypeError, ValueError):
            gap = 3.2
        self.spin_gap.setValue(gap)
        sound = self.cfg.get("sound") or {}
        si = self.cmb_sound.findData(str(sound.get("theme", "glass")))
        self.cmb_sound.setCurrentIndex(si if si >= 0 else 0)
        try:
            vol = int(round(float(sound.get("volume", 0.55)) * 100))
        except (TypeError, ValueError):
            vol = 55
        self.slider_vol.setValue(max(0, min(100, vol)))
        self.lbl_vol.setText("%d%%" % max(0, min(100, vol)))
        self.chk_mute.setChecked(bool(sound.get("mute", False)))
        self.chk_sound.setChecked(bool(sound.get("enabled", True)))
        self.chk_anim.setChecked(bool((self.cfg.get("animation") or {}).get("enabled", True)))
        hk = self.cfg.get("hotkey") or {}
        self.edit_hotkey.setText(str(hk.get("sequence", "")))
        self.chk_hotkey.setChecked(bool(hk.get("enabled", True)))
        self.chk_wheel.setChecked(bool(self.cfg.get("switch_wheel", True)))
        self.chk_keys.setChecked(bool(self.cfg.get("switch_keys", True)))
        self._building = False

    def _collect_general(self) -> None:
        mode = str(self.cmb_theme.currentData() or "auto")
        self.cfg.setdefault("theme", {})
        if mode == "auto":
            self.cfg["theme"]["follow_system"] = True
            self.cfg["theme"]["mode"] = "auto"
        else:
            self.cfg["theme"]["follow_system"] = False
            self.cfg["theme"]["mode"] = mode
        appear = self.cfg.setdefault("appearance", {})
        appear["frosted"] = bool(self.chk_frost.isChecked())
        appear["gap_px"] = round(float(self.spin_gap.value()), 2)
        self.cfg.setdefault("colors", {})["accent"] = str(
            self.cmb_accent.currentData() or "#4FC3F7")
        self.cfg["sectors"] = int(self.spin_sectors.value())
        self.cfg["icon_size"] = int(self.spin_icon.value())
        self.cfg["radius"] = int(self.spin_radius.value())
        sound = self.cfg.setdefault("sound", {})
        sound["theme"] = str(self.cmb_sound.currentData() or "glass")
        sound["volume"] = round(float(self.slider_vol.value()) / 100.0, 3)
        sound["mute"] = bool(self.chk_mute.isChecked())
        sound["enabled"] = bool(self.chk_sound.isChecked())
        self.cfg.setdefault("animation", {})["enabled"] = bool(self.chk_anim.isChecked())
        hk = self.cfg.setdefault("hotkey", {})
        hk["sequence"] = self.edit_hotkey.text().strip() or "Ctrl+Alt+Space"
        hk["enabled"] = bool(self.chk_hotkey.isChecked())
        self.cfg["switch_wheel"] = bool(self.chk_wheel.isChecked())
        self.cfg["switch_keys"] = bool(self.chk_keys.isChecked())

    def _validate(self) -> List[str]:
        """保存前把每个环、每个槽位再收敛一遍，返回提示信息。"""
        notes: List[str] = []
        sectors = int(self.cfg.get("sectors", 8) or 8)
        rings = ring_list(self.cfg)
        for i, ring in enumerate(rings):
            fixed = normalize_ring(ring, i, sectors)
            if len(ring.get("items") or []) != sectors:
                notes.append("「%s」槽位数量已调整为 %d" % (fixed.get("name", ""), sectors))
            rings[i] = fixed
        self.cfg["rings"] = rings
        self.cfg["current_ring"] = max(0, min(len(rings) - 1,
                                              int(self.cfg.get("current_ring", 0) or 0)))
        return notes

    def save_and_apply(self) -> None:
        self._collect_general()
        notes = self._validate()
        # 窗口尺寸/位置/最大化/分栏比例一起落盘，下次打开原样恢复
        self.cfg["ui"] = self._window_state_dict()
        cfg, norm_notes, _migrated = normalize_config(self.cfg)
        ok, msg = save_config(self.config_path, cfg)
        if not ok:
            self.lbl_status.setText("保存失败：%s" % msg)
            return
        self.cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
        self.applied.emit(cfg)
        tips = "；".join(notes + norm_notes)
        stamp = time.strftime("%H:%M:%S")
        self.lbl_status.setText("已保存并热应用（%s）%s" % (
            stamp, ("｜" + tips) if tips else ""))
        self._reload_ring_list()
        self._load_slot(self._slot)


def _to_logical(px: int, py: int) -> QPoint:
    """物理像素坐标 -> Qt 逻辑坐标（适配 Windows 显示缩放）。"""
    screens = QGuiApplication.screens()
    for s in screens:
        try:
            dpr = float(s.devicePixelRatio()) or 1.0
        except Exception:
            dpr = 1.0
        g = s.geometry()
        px0, py0 = int(round(g.x() * dpr)), int(round(g.y() * dpr))
        pw, ph = int(round(g.width() * dpr)), int(round(g.height() * dpr))
        if px0 <= px < px0 + pw and py0 <= py < py0 + ph:
            return QPoint(int(round(g.x() + (px - px0) / dpr)),
                          int(round(g.y() + (py - py0) / dpr)))
    s = QGuiApplication.primaryScreen()
    dpr = float(s.devicePixelRatio()) if s else 1.0
    dpr = dpr or 1.0
    return QPoint(int(round(px / dpr)), int(round(py / dpr)))


def _sector_clock_bounds(i: int, n: int, gap: float = 0.0) -> Tuple[float, float]:
    """第 i 个扇区在 clock 坐标系（自 12 点顺时针 0~360）里的起止角。

    扇区以 i*span 为中心，paintEvent 与命中判定共用，保证绘制与命中同源。
    """
    span = 360.0 / n
    return (i * span - span / 2.0 + gap / 2.0, i * span + span / 2.0 - gap / 2.0)


def _sector_index(clock: float, n: int) -> int:
    """clock 角 -> 扇区索引（与 _sector_clock_bounds 的中心线约定一致）。"""
    span = 360.0 / n
    return int(((clock + span / 2.0) % 360.0) // span) % n


class DialController(QObject):
    """把钩子事件翻译成窗口行为。"""

    def __init__(self, window: DialWindow) -> None:
        super().__init__()
        self.window = window

    @pyqtSlot(int, int)
    def on_triggered(self, px: int, py: int) -> None:
        pos = _to_logical(px, py)
        log.info("唤起圆盘 anchor_physical=(%s,%s) anchor_logical=(%s,%s)",
                 px, py, pos.x(), pos.y())
        self.window.open_at(pos)

    @pyqtSlot(int, int)
    def on_moved(self, px: int, py: int) -> None:
        if not self.window._visible:
            return
        pos = _to_logical(px, py)
        if TRACE:
            q = POINT()
            user32.GetCursorPos(ctypes.byref(q))
            log.info("MOVE hook_pt=(%s,%s) GetCursorPos=(%s,%s) logical=(%s,%s)",
                     px, py, q.x, q.y, pos.x(), pos.y())
        self.window.update_pointer(pos.x(), pos.y())

    @pyqtSlot(int, int)
    def on_released(self, px: int, py: int) -> None:
        if not self.window._visible:
            return
        pos = _to_logical(px, py)
        self.window.finish(pos.x(), pos.y())

    @pyqtSlot(str)
    def on_cancelled(self, reason: str) -> None:
        log.info("取消圆盘: %s", reason)
        self.window.cancel(reason)

    @pyqtSlot(bool, str)
    def on_status(self, ok: bool, err: str) -> None:
        log.info("钩子状态 installed=%s err=%s", ok, err)

    @pyqtSlot(int, int)
    def on_clicked(self, px: int, py: int) -> None:
        """热键模式：单击左键 = 确认当前指向的扇区。"""
        if not self.window._visible:
            return
        pos = _to_logical(px, py)
        self.window.finish(pos.x(), pos.y())

    @pyqtSlot(int)
    def on_digit(self, idx: int) -> None:
        """热键模式：数字键直接启动第 idx 个扇区。"""
        if not self.window._visible:
            return
        self.window.finish_index(idx)

    @pyqtSlot(int)
    def on_wheel(self, delta: int) -> None:
        """圆盘可见时滚轮切换圆环（只换数据，不动几何与命中）。"""
        if not self.window._visible:
            return
        if not bool(self.window.cfg.get("switch_wheel", True)):
            return
        self.window.switch_ring(delta=1 if delta > 0 else -1)

    @pyqtSlot(int)
    def on_ringkey(self, delta: int) -> None:
        """圆盘可见时 [ ] / Tab 切换圆环。"""
        if not self.window._visible:
            return
        if not bool(self.window.cfg.get("switch_keys", True)):
            return
        self.window.switch_ring(delta=delta or 1)
        self.window.update()


# ================================================================ 应用
class TrayNativeHook:
    """托盘窗口消息钩子：补上 Qt 在 Win11 新外壳下丢掉的左键单击。

    Qt 5.15 的托盘实现只在 wndproc 里识别 NIN_SELECT(0x0400) 等少数事件，
    而 Windows 11 新外壳左键单击托盘图标投递的事件值并不稳定（实测既可能
    是 NIN_SELECT，也可能是 WM_USER+5，且 Explorer 用 SendMessage 直投、
    不经过 Qt 事件分发器，QAbstractNativeEventFilter 收不到）。这里直接
    子类化 Qt 的托盘消息窗口，在 wndproc 层识别左键单击并转成同一套菜单
    逻辑；其余消息原样转交原窗口过程，不影响 Qt 自身行为。
    """

    # WM_APP + 100 / 101：Qt5 与 Qt6 托盘回调消息号
    TRAY_MESSAGE_IDS = (0x8000 + 100, 0x8000 + 101)
    # 左键单击（抬起 / NIN_SELECT / NIN_KEYSELECT / WM_USER+5）
    LEFT_CLICK_EVENTS = (0x0405, 0x0400, 0x0401, 0x0202)

    _PROC_TYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    def __init__(self, owner):
        self.owner = owner
        self.hwnd = None
        self.seen: List[int] = []
        self.installed = False
        self._old = None
        self._proc = None
        self._user32 = None
        self._enum = None

    # -------------------------------------------------- 安装
    def install(self) -> bool:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
        u.EnumWindows.restype = wt.BOOL
        self._user32 = u
        pid = os.getpid()
        hits: List[int] = []
        enum_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def _cb(hwnd, _lp):
            proc_id = wt.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid:
                buf = ctypes.create_unicode_buffer(128)
                u.GetClassNameW(hwnd, buf, 128)
                if "TrayIcon" in buf.value:
                    hits.append(int(hwnd))
            return True

        enum_cb = enum_type(_cb)
        self._enum = enum_cb
        u.EnumWindows(enum_cb, 0)
        if not hits:
            return False
        self.hwnd = hits[0]
        proc = self._PROC_TYPE(self._wndproc)
        self._proc = proc
        self._old = u.SetWindowLongPtrW(self.hwnd, -4, ctypes.cast(proc, ctypes.c_void_p))
        self.installed = self._old is not None
        return self.installed

    # -------------------------------------------------- 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if int(msg) in self.TRAY_MESSAGE_IDS:
                event = int(lparam) & 0xFFFF
                self.seen.append(event)
                if len(self.seen) > 64:
                    del self.seen[:-64]
                if event in self.LEFT_CLICK_EVENTS:
                    self.owner._on_tray_left_click_native(event)
        except Exception:  # 原生窗口过程里绝不能让异常逃逸
            log.debug("TrayNativeHook 处理异常", exc_info=True)
        return self._user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)


class DialApp:
    def __init__(self, app: QApplication, config_file: str) -> None:
        self.app = app
        self.config_file = config_file
        self.config, self.config_status = load_config(config_file)
        self.icons = IconCache()
        self.settings: Optional[SettingsDialog] = None
        self.tray: Optional[QSystemTrayIcon] = None
        self.hook: Optional[InputHook] = None
        self.bridge = HookBridge()

        # 音效套件（wav 位于脚本同目录 sounds/<theme>/）
        self.sound = SoundKit(self.sound_dir(), self.config)
        self.window = DialWindow(self.config, self.icons, self.launch_item)
        self.window.sound = self.sound
        self.controller = DialController(self.window)
        self.window.hidden.connect(self.on_dial_hidden)
        self.window.ringChanged.connect(self.on_window_ring_changed)

        self.bridge.triggered.connect(self.controller.on_triggered)
        self.bridge.moved.connect(self.controller.on_moved)
        self.bridge.released.connect(self.controller.on_released)
        self.bridge.cancelled.connect(self.controller.on_cancelled)
        self.bridge.status.connect(self.controller.on_status)
        self.bridge.clicked.connect(self.controller.on_clicked)
        self.bridge.digit.connect(self.controller.on_digit)
        self.bridge.wheel.connect(self.controller.on_wheel)
        self.bridge.ringkey.connect(self.controller.on_ringkey)

        # 全局热键（独立线程 + 独立消息循环）
        self.hotkey_bridge = HotkeyBridge()
        self.hotkey_thread: Optional[HotkeyThread] = None
        self.hotkey_status = {"ok": False, "err": "未启动", "sequence": ""}
        self.hotkey_bridge.pressed.connect(self.on_hotkey_pressed)
        self.hotkey_bridge.status.connect(self.on_hotkey_status)

        self._build_tray()
        self._start_hook()
        self._start_hotkey()
        self._prewarm_window()

    # -------------------------------------------------- 首帧预热
    def _prewarm_window(self) -> None:
        """在屏幕外完成一次原生窗口创建与首帧绘制。

        进程内第一次 show() 需要建窗口 + 首次绘制（实测约 270ms），
        放在真正唤出时会被用户感知为「按下热键半天没反应」。
        这里在启动阶段把它消耗掉，之后每次唤出首帧 <2ms。
        """
        try:
            win = self.window
            win._visible = True
            win.expand = 1.0
            win.move(-32000, -32000)
            win.show()
            self.app.processEvents()
            win.hide()
            win._visible = False
            win.expand = 0.0
            log.info("圆盘首帧预热完成（屏幕外 show/hide）")
        except Exception as exc:  # 预热失败不影响主流程
            log.warning("圆盘首帧预热失败：%r", exc)

    # -------------------------------------------------- 钩子
    def _phys_threshold(self) -> int:
        try:
            dpr = float(QGuiApplication.primaryScreen().devicePixelRatio()) or 1.0
        except Exception:
            dpr = 1.0
        thr = float(self.config.get("drag_threshold", 12))
        return max(4, int(round(thr * dpr)))

    def _start_hook(self) -> None:
        self.hook = InputHook(self.bridge, self._phys_threshold(),
                              bool(self.config.get("swallow_move_events", True)))
        self.hook.start()
        self.hook.ready.wait(timeout=5.0)

    def _stop_hook(self) -> None:
        if self.hook:
            self.hook.stop()
            self.hook.join(timeout=2.0)
            self.hook = None

    # -------------------------------------------------- 音效 / 热键
    def sound_dir(self) -> str:
        """只读音效资源目录：冻结时取 sys._MEIPASS/sounds。"""
        return resource_path("sounds")

    def _start_hotkey(self) -> None:
        pack = self.config.get("hotkey") or {}
        seq = str(pack.get("sequence", "") or "").strip()
        if not bool(pack.get("enabled", True)):
            self.hotkey_status = {"ok": False, "err": "配置已禁用热键", "sequence": seq}
            log.info("热键未启用（config.hotkey.enabled=false）")
            return
        self.hotkey_thread = HotkeyThread(self.hotkey_bridge, seq)
        self.hotkey_thread.start()
        self.hotkey_thread.ready.wait(timeout=3.0)

    def _stop_hotkey(self) -> None:
        if self.hotkey_thread:
            self.hotkey_thread.stop()
            self.hotkey_thread.join(timeout=2.0)
            self.hotkey_thread = None

    # 注意：DialApp 不是 QObject 子类，这里的方法不能加 @pyqtSlot，
    # 否则 signal.connect 会报 "connect() failed"。
    def on_hotkey_status(self, ok: bool, err: str) -> None:
        seq = str((self.config.get("hotkey") or {}).get("sequence", ""))
        self.hotkey_status = {"ok": bool(ok), "err": err, "sequence": seq}
        log.info("HOTKEY 状态 ok=%s seq=%s err=%s", ok, seq, err)
        self._update_tray_tooltip()
        if not ok:
            self.notify("全局热键注册失败：%s（桌面按住拖动仍可用）" % (err or "未知原因"))

    def on_hotkey_pressed(self) -> None:
        """任意窗口下按热键：在光标处唤出圆盘；再按一次收起。"""
        if self.window._visible:
            log.info("HOTKEY 收起（重复触发）")
            self.window.cancel("hotkey-toggle")
            return
        self.window.hotkey_mode = True
        if self.hook:
            self.hook.hotkey_mode = True
        pos = QCursor.pos()
        log.info("HOTKEY 唤出 cursor_logical=(%d,%d)", pos.x(), pos.y())
        self.window.open_at(pos)

    def _persist_current_ring(self) -> None:
        """把圆盘里切到的环写回配置（滚轮/[ ]/Tab 切环的持久化）。

        只在圆盘隐藏时写一次，避免滚动过程中频繁落盘。
        """
        try:
            idx = int(getattr(self.window, "_ring_index", 0))
        except (TypeError, ValueError):
            return
        if idx == int(self.config.get("current_ring", 0) or 0):
            return
        self.config["current_ring"] = idx
        save_config(self.config_file, self.config)
        self._rebuild_ring_menu()
        log.info("切环已持久化 current_ring=%s", idx)

    def on_dial_hidden(self) -> None:
        """圆盘隐藏后解除热键模式，恢复桌面拖动入口。"""
        self._persist_current_ring()
        self.window.hotkey_mode = False
        if self.hook:
            self.hook.hotkey_mode = False
            self.hook._reset()
        log.info("DIAL 已隐藏，热键模式复位 hook.hotkey_mode=%s",
                 getattr(self.hook, "hotkey_mode", None))

    # -------------------------------------------------- 托盘
    def _tray_icon(self) -> QIcon:
        size = 64
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor(27, 31, 42, 235))
        p.setPen(QPen(QColor(79, 195, 247), 2.5))
        p.drawEllipse(QRectF(4, 4, size - 8, size - 8))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(79, 195, 247))
        for i in range(8):
            ang = math.radians(i * 45)
            x = 32 + math.sin(ang) * 17
            y = 32 - math.cos(ang) * 17
            p.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
        p.setBrush(QColor(240, 246, 252))
        p.drawEllipse(QRectF(27, 27, 10, 10))
        p.end()
        return QIcon(pm)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("系统托盘不可用，跳过托盘图标创建")
            return
        tray = QSystemTrayIcon(self._tray_icon(), self.app)
        menu = QMenu()

        # 显示 / 隐藏圆盘（文案随圆盘可见状态切换，见 _refresh_tray_menu）
        self.act_show = QAction("显示圆盘", menu)
        self.act_show.triggered.connect(self.toggle_dial)
        menu.addAction(self.act_show)

        self.ring_menu = menu.addMenu("切换圆环")
        self._rebuild_ring_menu()

        menu.addSeparator()

        # 图形配置窗口（QDialog，热应用到运行中的圆盘）
        self.act_settings = QAction("打开设置…", menu)
        self.act_settings.triggered.connect(self.open_settings)
        menu.addAction(self.act_settings)

        self.act_reload = QAction("重新加载配置", menu)
        self.act_reload.triggered.connect(self.reload_config)
        menu.addAction(self.act_reload)

        menu.addSeparator()

        # 音效开关（勾选态 = 音效开启）
        self.act_mute = QAction("启用音效", menu)
        self.act_mute.setCheckable(True)
        self.act_mute.setChecked(not bool(self.sound.mute))
        self.act_mute.toggled.connect(self._on_sound_toggled)
        menu.addAction(self.act_mute)

        theme_menu = menu.addMenu("音效主题")
        self.act_themes: List[QAction] = []
        for name, label in (("glass", "玻璃"), ("mechanical", "机械"), ("minimal", "极简")):
            act = QAction(label, theme_menu)
            act.setCheckable(True)
            act.setChecked(self.sound.theme == name)
            act.triggered.connect(lambda _c=False, n=name: self.set_sound_theme(n))
            theme_menu.addAction(act)
            self.act_themes.append(act)

        menu.addSeparator()

        # 保留原始配置文件入口（高级用户直接改 JSON）
        self.act_open_cfg = QAction("编辑配置文件（JSON）", menu)
        self.act_open_cfg.triggered.connect(self.open_config_file)
        menu.addAction(self.act_open_cfg)

        act_open_dir = QAction("打开配置文件所在文件夹", menu)
        act_open_dir.triggered.connect(self.open_config_dir)
        menu.addAction(act_open_dir)

        menu.addSeparator()

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        # 关键：不再 setContextMenu —— 否则 Windows 只会在右键自动弹菜单，
        # 左键单击永远没反应。改为 activated 里自行处理 Trigger/Context 两个
        # Reason，两者弹同一份菜单，且菜单能在光标处按屏幕边界钳制定位。
        self.tray_menu = menu
        tray.activated.connect(self._on_tray_activated)
        tray.show()

        # 窗口过程兜底：Win11 新外壳下 Qt 收不到左键单击（详见 TrayNativeHook）
        try:
            hook_obj = TrayNativeHook(self)
            if hook_obj.install():
                self._tray_native_hook = hook_obj
                log.info("TRAY 窗口过程钩子已安装 hwnd=%s", hook_obj.hwnd)
            else:
                self._tray_native_hook = None
                log.warning("TRAY 窗口过程钩子安装失败：未找到托盘消息窗口")
        except Exception:
            self._tray_native_hook = None
            log.warning("TRAY 窗口过程钩子安装异常", exc_info=True)
        self.tray = tray
        self._update_tray_tooltip()

    def _on_tray_activated(self, reason) -> None:
        """托盘图标交互分发。

        - Trigger（左键单击，含按下/释放）→ 延迟 220ms 弹菜单，
          给 DoubleClick 留出取消窗口，避免与「双击唤出圆盘」冲突；
        - Context（右键）→ 立即弹同一份菜单；
        - DoubleClick（双击）→ 取消待弹菜单，沿用原行为唤出圆盘。
        """
        if reason == QSystemTrayIcon.DoubleClick:
            timer = getattr(self, "_tray_menu_timer", None)
            if timer is not None:
                timer.stop()
            # 双击的第二下松手，外壳还会再补一个 NIN_SELECT(Trigger)，
            # 这里打时间戳把它吃掉，避免菜单盖在刚唤出的圆盘上。
            self._tray_dblclick_ts = time.time()
            log.info("TRAY double-click -> 唤出圆盘")
            self.preview_at_cursor()
            return
        if reason == QSystemTrayIcon.Trigger:
            self._schedule_tray_menu()
            return
        if reason == QSystemTrayIcon.Context:
            timer = getattr(self, "_tray_menu_timer", None)
            if timer is not None:
                timer.stop()
            self._popup_tray_menu()
            return
        log.info("TRAY activated reason=%s（未处理）", reason)

    def _schedule_tray_menu(self) -> None:
        """左键单击 → 延迟 220ms 弹菜单，给双击留出取消窗口。"""
        if time.time() - float(getattr(self, "_tray_dblclick_ts", 0.0)) < 0.6:
            log.info("TRAY 刚发生双击，忽略本次 Trigger")
            return
        timer = getattr(self, "_tray_menu_timer", None)
        if timer is None:
            timer = QTimer(self.app)
            timer.setSingleShot(True)
            timer.timeout.connect(self._popup_tray_menu)
            self._tray_menu_timer = timer
        else:
            timer.stop()
        timer.start(220)
        log.info("TRAY trigger -> 菜单将在 220ms 后弹出")

    def _on_tray_left_click_native(self, event: int) -> None:
        """原生托盘回调里识别出的左键单击（Qt 自身未处理的那部分）。"""
        log.info("TRAY native left-click event=0x%04X", event)
        self._schedule_tray_menu()

    def _refresh_tray_menu(self) -> None:
        """弹菜单前同步动态项：显示/隐藏文案、音效勾选、圆环勾选。"""
        act_show = getattr(self, "act_show", None)
        if act_show is not None:
            visible = bool(getattr(self.window, "_visible", False))
            act_show.setText("隐藏圆盘" if visible else "显示圆盘")
        act_mute = getattr(self, "act_mute", None)
        if act_mute is not None:
            act_mute.blockSignals(True)
            act_mute.setChecked(not bool(self.sound.mute))
            act_mute.blockSignals(False)
        self._rebuild_ring_menu()
        self._update_tray_tooltip()

    def _popup_tray_menu(self) -> None:
        """在光标处弹出托盘菜单，位置做屏幕边界钳制。"""
        menu = getattr(self, "tray_menu", None)
        if menu is None:
            return
        if getattr(self, "_tray_menu_shown", False):
            return
        self._refresh_tray_menu()
        pos = QCursor.pos()
        hint = menu.sizeHint()
        screen = None
        try:
            screen = QGuiApplication.screenAt(pos)
        except (AttributeError, TypeError):
            screen = None
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(max(pos.x() + 4, avail.left() + 2),
                    max(avail.left() + 2, avail.left() + avail.width() - hint.width() - 2))
            y = min(max(pos.y() + 4, avail.top() + 2),
                    max(avail.top() + 2, avail.top() + avail.height() - hint.height() - 2))
            pos = QPoint(int(x), int(y))
        log.info("TRAY menu popup at (%d,%d) size=%dx%d actions=%d",
                 pos.x(), pos.y(), hint.width(), hint.height(), len(menu.actions()))
        self._tray_menu_shown = True
        hook = self.hook
        if hook is not None:
            hook.suppress = True
        try:
            menu.exec_(pos)
        finally:
            self._tray_menu_shown = False
            if hook is not None:
                hook.suppress = False
            log.info("TRAY menu closed")

    def toggle_dial(self) -> None:
        """菜单「显示/隐藏圆盘」：可见则收起，不可见则在光标处唤出。"""
        if getattr(self.window, "_visible", False):
            log.info("TRAY 菜单 -> 隐藏圆盘")
            self.window.cancel("tray-toggle")
        else:
            log.info("TRAY 菜单 -> 显示圆盘")
            self.preview_at_cursor()

    def _on_sound_toggled(self, checked: bool) -> None:
        """勾选 = 音效开启，未勾选 = 静音。"""
        self.toggle_mute(not bool(checked))

    def _update_tray_tooltip(self) -> None:
        """托盘 tooltip：软件名 + 当前环名 + 热键状态（+ 静音）。"""
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        rings = ring_list(self.config)
        cur = ring_index(self.config)
        name = ""
        if rings:
            try:
                ring = rings[int(cur) % len(rings)]
                name = str(ring.get("name") or ("圆环 %d" % (int(cur) + 1)))
            except (TypeError, ValueError, IndexError):
                name = ""
        seq = str((self.config.get("hotkey") or {}).get("sequence", ""))
        status = getattr(self, "hotkey_status", None) or {}
        hot = ("热键 %s" % seq) if status.get("ok") else "热键未生效"
        bits = [APP_TITLE]
        if name:
            bits.append("当前环：%s" % name)
        bits.append(hot)
        if bool(getattr(self.sound, "mute", False)):
            bits.append("静音")
        tray.setToolTip(" · ".join(bits))

    # -------------------------------------------------- 图形设置
    def _rebuild_ring_menu(self) -> None:
        """托盘「切换圆环」子菜单：与配置中的环保持同步。"""
        menu = getattr(self, "ring_menu", None)
        if menu is None:
            return
        menu.clear()
        self.act_rings: List[QAction] = []
        rings = ring_list(self.config)
        cur = ring_index(self.config)
        if len(rings) <= 1:
            act = QAction("（仅一个圆环）", menu)
            act.setEnabled(False)
            menu.addAction(act)
            return
        for i, ring in enumerate(rings):
            act = QAction(str(ring.get("name") or ("圆环 %d" % (i + 1))), menu)
            act.setCheckable(True)
            act.setChecked(i == cur)
            act.triggered.connect(lambda _c=False, k=i: self.switch_ring(k))
            menu.addAction(act)
            self.act_rings.append(act)

    def switch_ring(self, index: int) -> None:
        """从托盘菜单切环：更新配置并持久化，圆盘下次唤出即生效。"""
        rings = ring_list(self.config)
        if not rings:
            return
        idx = int(index) % len(rings)
        self.config["current_ring"] = idx
        ok, msg = save_config(self.config_file, self.config)
        self.window.set_config(self.config)
        self._rebuild_ring_menu()
        self._update_tray_tooltip()
        log.info("托盘切环 -> %s（写入 %s）", idx, ok)
        self.notify("已切换圆环：%s" % (rings[idx].get("name") or ("圆环 %d" % (idx + 1))))

    def open_settings(self) -> None:
        """打开图形配置窗口（非模态，可一边改一边预览圆盘）。"""
        if self.settings is not None:
            try:
                self.settings.raise_()
                self.settings.activateWindow()
                return
            except RuntimeError:
                self.settings = None
        dlg = SettingsDialog(self.config, self.config_file, self.icons)
        dlg.applied.connect(self.on_settings_applied)
        dlg.finished.connect(self._on_settings_closed)
        self.settings = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        log.info("设置窗口已打开")

    def _on_settings_closed(self, _result: int) -> None:
        self.settings = None
        log.info("设置窗口已关闭")

    def on_window_ring_changed(self, index: int) -> None:
        """滚轮 / [ ] 切环后回写配置并同步托盘勾选，重启仍停在最后使用的环。"""
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return
        rings = ring_list(self.config)
        if not rings:
            return
        idx = idx % len(rings)
        changed = int(self.config.get("current_ring", 0) or 0) != idx
        self.config["current_ring"] = idx
        if changed:
            ok, msg = save_config(self.config_file, self.config)
            if not ok:
                log.warning("切环配置写入失败：%s", msg)
        self._rebuild_ring_menu()
        self._update_tray_tooltip()
        log.info("窗口切环 -> %s（%d/%d）", idx + 1, idx + 1, len(rings))

    def on_settings_applied(self, cfg: Dict[str, Any]) -> None:
        """设置保存后热应用：圆盘 / 音效 / 热键 / 托盘菜单全部同步，无需重启。"""
        self.config = cfg
        self.icons.clear()             # 图标可能被改过，作废缓存保证重取
        self.window.set_config(cfg)
        self.sound.set_config(cfg)
        if hasattr(self, "act_mute"):
            self.act_mute.blockSignals(True)
            self.act_mute.setChecked(not bool(self.sound.mute))
            self.act_mute.blockSignals(False)
        for act, nm in zip(getattr(self, "act_themes", []),
                           ("glass", "mechanical", "minimal")):
            act.blockSignals(True)
            act.setChecked(nm == self.sound.theme)
            act.blockSignals(False)
        # 热键序列 / 启用状态变化 -> 重注册
        seq = str((cfg.get("hotkey") or {}).get("sequence", ""))
        if seq != str((self.hotkey_status or {}).get("sequence", "")) or \
                (bool((cfg.get("hotkey") or {}).get("enabled", True))
                 and not self.hotkey_status.get("ok")):
            self._stop_hotkey()
            self._start_hotkey()
        self._rebuild_ring_menu()
        self._update_tray_tooltip()
        # 拖拽阈值可能被改动 -> 同步到钩子线程
        if self.hook:
            self.hook.threshold = self._phys_threshold()
        log.info("设置已热应用：环=%s 扇区=%s 音效=%s",
                 self.window.ring_names(), cfg.get("sectors"), self.sound.theme)
        self.notify("设置已应用，圆盘立即生效")

    # -------------------------------------------------- 动作
    def preview_at_cursor(self) -> None:
        # 预览/托盘入口走桌面拖动语义，关闭热键模式以免两套入口互相干扰
        self.window.hotkey_mode = False
        if self.hook:
            self.hook.hotkey_mode = False
        pos = QCursor.pos()
        self.window.open_at(pos)

    def reload_config(self) -> None:
        cfg, status = load_config(self.config_file)
        self.config = cfg
        self.window.set_config(cfg)
        # 音效与热键同步热更新；热键序列变化时需重注册
        self.sound.set_config(cfg)
        if hasattr(self, "act_mute"):
            self.act_mute.blockSignals(True)
            self.act_mute.setChecked(not bool(self.sound.mute))
            self.act_mute.blockSignals(False)
        new_seq = str((cfg.get("hotkey") or {}).get("sequence", ""))
        old_seq = str((self.hotkey_status or {}).get("sequence", ""))
        enabled = bool((cfg.get("hotkey") or {}).get("enabled", True))
        if new_seq != old_seq or (enabled and not self.hotkey_status.get("ok")):
            self._stop_hotkey()
            self._start_hotkey()
        log.info("重载配置: %s（音效 theme=%s mute=%s，热键 %s）", status,
                 self.sound.theme, self.sound.mute, new_seq)
        self._update_tray_tooltip()
        self.notify(f"配置已重载（{status}）")

    def toggle_mute(self, checked: bool) -> None:
        self.sound.mute = bool(checked)
        self.config.setdefault("sound", {})["mute"] = bool(checked)
        log.info("音效静音切换 mute=%s", checked)
        self._update_tray_tooltip()
        if not checked:
            self.sound.play("show")   # 解除静音时给一声确认反馈

    def set_sound_theme(self, name: str) -> None:
        self.sound.theme = name
        self.config.setdefault("sound", {})["theme"] = name
        self.sound.set_config(self.config)
        for act, nm in zip(getattr(self, "act_themes", []),
                           ("glass", "mechanical", "minimal")):
            act.blockSignals(True)
            act.setChecked(nm == name)
            act.blockSignals(False)
        log.info("音效主题切换 theme=%s", name)
        self.sound.play("select")
        self.notify("音效主题：%s" % {"glass": "玻璃", "mechanical": "机械",
                                     "minimal": "极简"}.get(name, name))

    def open_config_file(self) -> None:
        if not os.path.isfile(self.config_file):
            load_config(self.config_file)
        os.startfile(self.config_file)  # noqa: S606

    def open_config_dir(self) -> None:
        d = os.path.dirname(os.path.abspath(self.config_file))
        if os.path.isdir(d):
            os.startfile(d)  # noqa: S606

    def notify(self, msg: str) -> None:
        if self.tray:
            self.tray.showMessage(APP_TITLE, msg, QSystemTrayIcon.Information, 3000)

    def launch_item(self, item: Dict[str, Any]) -> None:
        kind = str(item.get("type", "app")).lower()
        target = os.path.expandvars(os.path.expanduser(str(item.get("target", "")).strip()))
        args = str(item.get("args", "") or "").strip()
        if not target:
            return
        try:
            low = target.lower()
            # ① shell: 目标（含 shell:AppsFolder\<AUMID> UWP、shell:MyComputerFolder 等）
            if low.startswith("shell:"):
                launch_shell_target(target)
                log.info("已启动 [uwp/shell] %s", target)
                return
            # ② .url 网络快捷方式
            if low.endswith(".url") and os.path.isfile(target):
                url = resolve_url_shortcut(target)
                if url:
                    os.startfile(url)  # noqa: S606
                    log.info("已启动 [.url] %s -> %s", target, url)
                    return
            # ③ .lnk 快捷方式：优先解析真实目标（便于日志/图标），失败则直接 startfile
            if low.endswith(".lnk") and os.path.isfile(target):
                info = resolve_lnk(target)
                if info and info.get("target"):
                    real = os.path.expandvars(info["target"])
                    log.info("解析 .lnk %s -> target=%s args=%s workdir=%s",
                             target, real, info.get("args"), info.get("workdir"))
                    try:
                        os.startfile(target)  # noqa: S606  交给 shell 保证参数/工作目录正确
                        return
                    except Exception as exc:
                        log.warning(".lnk 直接启动失败，回退目标：%r", exc)
                        os.startfile(real)  # noqa: S606
                        return
                os.startfile(target)  # noqa: S606
                log.info("已启动 [.lnk/未解析] %s", target)
                return
            # ④ URL
            if kind == "url" or re.match(r"^(https?|ftp|file)://", target, re.I):
                if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", target):
                    target = "http://" + target
                os.startfile(target)  # noqa: S606
            else:
                if not os.path.exists(target):
                    self.notify(f"目标不存在：{target}")
                    log.warning("目标不存在: %s", target)
                    return
                if args:
                    os.startfile(target, "open", args)  # noqa: S606
                else:
                    os.startfile(target)  # noqa: S606
            log.info("已启动 [%s] %s", kind, target)
        except Exception as exc:
            log.error("启动失败 %s: %r", target, exc)
            self.notify(f"启动失败：{exc}")

    def quit(self) -> None:
        self._stop_hook()
        self._stop_hotkey()
        if self.tray:
            self.tray.hide()
        self.app.quit()


# ================================================================ 入口
def _setup_logging() -> None:
    """日志写到可写目录（exe 同目录或 %APPDATA%\\DesktopDial\\），失败退到临时目录。"""
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    candidates = [
        os.path.join(data_dir(), "desktop_dial.log"),
        os.path.join(tempfile.gettempdir(), "desktop_dial.log"),
    ]
    for path in candidates:
        try:
            handler = logging.FileHandler(path, encoding="utf-8")
        except OSError:
            continue
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=[handler])
        log.info("==== %s 启动 ==== 冻结=%s 资源=%s 数据=%s 日志=%s",
                 APP_TITLE, is_frozen(), resource_dir(), data_dir(), path)
        return
    logging.basicConfig(level=logging.INFO, format=fmt)
    log.info("==== %s 启动 ====（无日志文件）", APP_TITLE)


def _probe_mode(points: List[Tuple[int, int]]) -> int:
    """--probe 诊断模式：不启动 UI，只打印每个采样点的判定证据。

    用法：python desktop_dial.py --probe            # 默认采样点
          python desktop_dial.py --probe 960,540    # 指定坐标
    """
    if not points:
        points = [(10, 10), (960, 540), (400, 700), (1900, 1070), (60, 120)]
    print("屏幕: %d x %d" % (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)))
    for x, y in points:
        hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
        chain = _ancestor_chain(hwnd)
        cn_chain = " -> ".join(_class_name(h) for h in chain)
        icon_hit = ""
        for node in chain:
            if _class_name(node) in DESKTOP_ICON_CLASSES:
                icon_hit = " icon_iItem=%s" % (_listview_hit_item(node, int(x), int(y)),)
                break
        print("(%4d,%4d) top=%-28s is_desktop=%-5s is_blank=%-5s%s\n           chain=%s"
              % (x, y, _class_name(hwnd), is_desktop_point(x, y),
                 is_blank_desktop_point(x, y), icon_hit, cn_chain))
    return 0


def _ensure_streams() -> None:
    """--noconsole 打包后 sys.stdout/stderr 为 None，print 会抛异常；
    此时把输出接到可写目录的 dial_stdout.log，保证诊断信息不丢。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        fh = open(os.path.join(data_dir(), "dial_stdout.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_streams()
    _setup_logging()
    argv = list(sys.argv if argv is None else argv)
    selftest = "--selftest" in argv

    if "--probe" in argv:
        pts: List[Tuple[int, int]] = []
        idx = argv.index("--probe")
        for extra in argv[idx + 1:]:
            if "," in extra:
                try:
                    a, b = extra.split(",", 1)
                    pts.append((int(a), int(b)))
                except ValueError:
                    pass
        return _probe_mode(pts)

    cfg_file = config_path_default()
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            cfg_file = argv[i + 1]
        elif a.startswith("--config="):
            cfg_file = a.split("=", 1)[1]

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)

    try:
        dial = DialApp(app, cfg_file)
    except Exception as exc:
        log.exception("初始化失败")
        print(f"初始化失败: {exc!r}")
        return 2

    if selftest:
        result: Dict[str, Any] = {}

        def _collect() -> None:
            hook = dial.hook
            result.update({
                "config_file": cfg_file,
                "config_status": dial.config_status,
                "sectors": dial.window._sector_count(),
                "items": len(dial.config.get("items") or []),
                "tray": bool(dial.tray is not None and dial.tray.isVisible()),
                "hook_installed": bool(hook and hook.installed),
                "hook_mouse_ok": bool(hook and hook.mouse_ok),
                "hook_keyboard_ok": bool(hook and hook.kbd_ok),
                "hook_error": (hook.last_error if hook else ""),
                "screen": (app.primaryScreen().geometry().width(),
                           app.primaryScreen().geometry().height()),
                "dpr": app.primaryScreen().devicePixelRatio(),
                # 本轮新增能力自检
                "sound": dial.sound.status(),
                "sound_themes": dial.sound.themes(),
                "hotkey": dial.hotkey_status,
                "theme_note": getattr(dial.window, "theme_note", ""),
                "colors": dial.window.cfg.get("colors"),
                "animations": bool(dial.window.animations_enabled()),
                "tray_mute": bool(getattr(dial, "act_mute", None) and dial.act_mute.isChecked()),
            })
            print("SELFTEST " + json.dumps(result, ensure_ascii=False))
            dial.quit()

        QTimer.singleShot(1800, _collect)

    # --settings：启动即打开图形设置窗口（排障/自检用，不影响正常双击运行）
    if ("--settings" in argv) and not selftest:
        QTimer.singleShot(0, dial.open_settings)

    rc = app.exec_()
    log.info("==== 退出 rc=%s ====", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import ctypes
from ctypes import wintypes
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

try:
    from PySide6.QtCore import QRectF, QTimer, Qt
    from PySide6.QtGui import QIcon, QPainterPath, QPixmap, QRegion, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    QApplication = None

try:
    import winreg
except Exception:
    winreg = None

try:
    from pywinauto import Desktop
except Exception:
    Desktop = None

CONFIG_PATH = "config.json"


def resource_path(*parts: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, *parts)


ICON_PATH = resource_path("assets", "app.ico")
APP_ICON_IMAGE_PATH = resource_path("assets", "app-source.png")


def setup_console_encoding():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def save_config(cfg: dict):
    visible_cfg = {key: value for key, value in cfg.items() if key != "search_template"}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(visible_cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"保存配置失败: {e}")


def load_config():
    default = {
        "search_template": "https://osu.sayobot.cn/?search={id}",
        "keep_original": False,
        "auto_download": False,
        "download_mode": "full",
        "download_method": "direct",
        "download_dir": "downloads",
        "open_after_download": False,
        "open_with": "default",
        "stable_path": "",
        "lazer_path": "",
    }
    if not os.path.exists(CONFIG_PATH):
        cfg = autofill_launcher_paths(default.copy())
        save_config(cfg)
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = {**default, **cfg}
        updated = autofill_launcher_paths(merged)
        visible_updated = {key: value for key, value in updated.items() if key != "search_template"}
        if visible_updated != cfg:
            save_config(updated)
        return updated
    except Exception:
        return autofill_launcher_paths(default.copy())


def is_url(string: str) -> bool:
    return string.startswith(("http://", "https://"))


def open_new_tab_in_source_browser(url: str, source_window=None) -> bool:
    if not source_window:
        return False

    try:
        source_window.set_focus()
        source_window.type_keys("^t", set_foreground=False)
        time.sleep(0.1)

        edit = find_address_edit(source_window)
        if edit is not None:
            try:
                edit.set_edit_text(url)
                edit.type_keys("{ENTER}", set_foreground=False)
                return True
            except Exception:
                pass

        source_window.type_keys(url, with_spaces=True, set_foreground=False)
        source_window.type_keys("{ENTER}", set_foreground=False)
        return True
    except Exception:
        return False


def get_download_mode(cfg: dict) -> str:
    mode = str(cfg.get("download_mode", "full")).lower().strip()
    if mode in ("novideo", "无视频谱面", "无视频"):
        return "novideo"
    return "full"


def get_download_method(cfg: dict) -> str:
    method = str(cfg.get("download_method", "direct")).lower().strip()
    if method in ("browser", "浏览器打开", "浏览器"):
        return "browser"
    return "direct"


def build_download_url(set_id: str, cfg: dict) -> str:
    mode = get_download_mode(cfg)
    return f"https://txy1.sayobot.cn/beatmaps/download/{mode}/{set_id}"


def safe_filename(filename: str) -> str:
    filename = filename.strip().strip(".")
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    return filename or "beatmap.osz"


def get_filename_from_response(response, set_id: str, mode: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if match:
        return safe_filename(urllib.parse.unquote(match.group(1)))

    match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    if match:
        return safe_filename(urllib.parse.unquote(match.group(1)))

    return f"{set_id}-{mode}.osz"


def get_unique_path(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    path = os.path.join(directory, filename)
    counter = 1
    while os.path.exists(path):
        path = os.path.join(directory, f"{base} ({counter}){ext}")
        counter += 1
    return path


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def print_progress(downloaded: int, total: int, started_at: float, final: bool = False):
    elapsed = max(time.time() - started_at, 0.001)
    speed = downloaded / elapsed

    if total:
        percent = min(downloaded / total, 1.0)
        width = 30
        filled = int(width * percent)
        bar = "#" * filled + "-" * (width - filled)
        text = (
            f"\r[{bar}] {percent * 100:5.1f}% "
            f"{format_size(downloaded)}/{format_size(total)} "
            f"{format_size(speed)}/s"
        )
    else:
        text = f"\r{format_size(downloaded)} downloaded | {format_size(speed)}/s"

    print(text, end="\n" if final else "", flush=True)


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", ctypes.c_void_p),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", ctypes.c_void_p),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", ctypes.c_void_p),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def choose_folder_windows(title: str, initial_dir: str = "") -> str:
    display_name = ctypes.create_unicode_buffer(260)
    path_buffer = ctypes.create_unicode_buffer(32768)
    flags = 0x00000001 | 0x00000040 | 0x00000010
    browse_info = BROWSEINFOW(
        None,
        None,
        ctypes.cast(display_name, ctypes.c_void_p),
        title,
        flags,
        None,
        0,
        0,
    )
    pidl = ctypes.windll.shell32.SHBrowseForFolderW(ctypes.byref(browse_info))
    if not pidl:
        return ""
    try:
        if ctypes.windll.shell32.SHGetPathFromIDListW(pidl, path_buffer):
            return path_buffer.value
        return ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pidl)


def choose_file_windows(title: str, initial_dir: str = "") -> str:
    file_buffer = ctypes.create_unicode_buffer(32768)
    filters = "Executable (*.exe)\0*.exe\0All files (*.*)\0*.*\0\0"
    flags = 0x00001000 | 0x00000800 | 0x00000008 | 0x00080000
    open_filename = OPENFILENAMEW()
    open_filename.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    open_filename.hwndOwner = None
    open_filename.lpstrFilter = filters
    open_filename.nFilterIndex = 1
    open_filename.lpstrFile = ctypes.cast(file_buffer, ctypes.c_void_p)
    open_filename.nMaxFile = len(file_buffer)
    open_filename.lpstrInitialDir = initial_dir or None
    open_filename.lpstrTitle = title
    open_filename.Flags = flags
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(open_filename)):
        return file_buffer.value
    return ""


def get_open_with(cfg: dict) -> str:
    target = str(cfg.get("open_with", "default")).lower().strip()
    aliases = {
        "系统默认": "default",
        "默认": "default",
        "osu! stable": "stable",
        "stable": "stable",
        "osu! lazer": "lazer",
        "lazer": "lazer",
    }
    target = aliases.get(target, target)
    if target in ("stable", "lazer"):
        return target
    return "default"


def get_common_launcher_paths(target: str) -> list[str]:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
    user_profile = os.environ.get("USERPROFILE", "")
    steam_roots = [
        os.path.join(program_files_x86, "Steam", "steamapps", "common"),
        os.path.join(program_files, "Steam", "steamapps", "common"),
    ]

    if target == "stable":
        paths = [
            os.path.join(local_appdata, "osu!", "osu!.exe"),
            os.path.join(appdata, "osu!", "osu!.exe"),
            os.path.join(program_files, "osu!", "osu!.exe"),
            os.path.join(program_files_x86, "osu!", "osu!.exe"),
        ]
        paths.extend(os.path.join(root, "osu!", "osu!.exe") for root in steam_roots)
        return paths

    paths = [
        os.path.join(local_appdata, "osulazer", "osu!.exe"),
        os.path.join(local_appdata, "Programs", "osu!", "osu!.exe"),
        os.path.join(local_appdata, "Programs", "osu!lazer", "osu!.exe"),
        os.path.join(local_appdata, "Programs", "osu! lazer", "osu!.exe"),
        os.path.join(program_files, "osu!lazer", "osu!.exe"),
        os.path.join(program_files, "osu! lazer", "osu!.exe"),
        os.path.join(program_files_x86, "osu!lazer", "osu!.exe"),
        os.path.join(program_files_x86, "osu! lazer", "osu!.exe"),
        os.path.join(user_profile, "scoop", "apps", "osu-lazer", "current", "osu!.exe"),
    ]
    paths.extend(os.path.join(root, "osu!", "osu!.exe") for root in steam_roots)
    paths.extend(os.path.join(root, "osu!lazer", "osu!.exe") for root in steam_roots)
    return paths


def get_registry_launcher_paths(target: str) -> list[str]:
    if winreg is None:
        return []

    registry_roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    paths = []

    for root, subkey in registry_roots:
        try:
            with winreg.OpenKey(root, subkey) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        app_key_name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, app_key_name) as app_key:
                            display_name = read_registry_value(app_key, "DisplayName").lower()
                            install_location = read_registry_value(app_key, "InstallLocation")
                            display_icon = read_registry_value(app_key, "DisplayIcon")
                    except OSError:
                        continue

                    if "osu" not in display_name:
                        continue
                    if target == "lazer" and "lazer" not in display_name:
                        continue
                    if target == "stable" and "lazer" in display_name:
                        continue

                    if install_location:
                        paths.append(get_exe_from_install_location(install_location))
                    if display_icon:
                        paths.append(clean_exe_path(display_icon))
        except OSError:
            continue

    return paths


def read_registry_value(key, name: str) -> str:
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value).strip()


def clean_exe_path(path: str) -> str:
    path = path.strip().strip('"')
    if "," in path:
        path = path.split(",", 1)[0].strip().strip('"')
    return path


def get_exe_from_install_location(path: str) -> str:
    path = clean_exe_path(path)
    if path.lower().endswith(".exe"):
        return path
    return os.path.join(path, "osu!.exe")


def find_existing_path(paths: list[str]) -> str:
    for path in paths:
        if path and os.path.exists(path):
            return os.path.abspath(path)
    return ""


def autofill_launcher_paths(cfg: dict) -> dict:
    updated = cfg.copy()
    stable_path = str(updated.get("stable_path", "")).strip()
    lazer_path = str(updated.get("lazer_path", "")).strip()

    if not stable_path:
        detected = find_existing_path([*get_common_launcher_paths("stable"), *get_registry_launcher_paths("stable")])
        if detected:
            updated["stable_path"] = detected
            print(f"已自动检测到 osu! stable: {detected}")

    if not lazer_path:
        detected = find_existing_path([*get_common_launcher_paths("lazer"), *get_registry_launcher_paths("lazer")])
        if detected:
            updated["lazer_path"] = detected
            print(f"已自动检测到 osu! lazer: {detected}")

    return updated


def get_launcher_candidates(target: str, cfg: dict) -> list[str]:
    if target == "stable":
        configured = str(cfg.get("stable_path", "")).strip()
    else:
        configured = str(cfg.get("lazer_path", "")).strip()
    return [configured, *get_common_launcher_paths(target), *get_registry_launcher_paths(target)]


def open_downloaded_file(path: str, cfg: dict) -> bool:
    absolute_path = os.path.abspath(path)
    target = get_open_with(cfg)

    if target == "default":
        try:
            os.startfile(absolute_path)
            print("已使用系统默认程序打开下载文件。")
            return True
        except OSError as e:
            print(f"自动打开文件失败: {e}")
            return False

    for launcher_path in get_launcher_candidates(target, cfg):
        if launcher_path and os.path.exists(launcher_path):
            try:
                subprocess.Popen([launcher_path, absolute_path])
                print(f"已使用 {target} 启动器打开下载文件。")
                return True
            except OSError as e:
                print(f"使用 {target} 启动器打开失败: {e}")
                return False

    print(f"未找到 {target} 启动器，请在 config.json 中配置对应路径。")
    return False


def download_to_file(set_id: str, cfg: dict) -> bool:
    mode = get_download_mode(cfg)
    download_url = build_download_url(set_id, cfg)
    download_dir = str(cfg.get("download_dir", "downloads")).strip() or "downloads"
    os.makedirs(download_dir, exist_ok=True)

    print(f"正在下载谱面 {set_id} ({mode})...")
    print(f"下载链接: {download_url}")

    request = urllib.request.Request(
        download_url,
        headers={
            "User-Agent": "osu-sayobot-helper/1.0",
            "Accept": "application/octet-stream,*/*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            filename = get_filename_from_response(response, set_id, mode)
            target_path = get_unique_path(download_dir, filename)
            temp_path = target_path + ".part"
            total = response.headers.get("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else 0
            downloaded = 0
            started_at = time.time()
            last_report = started_at

            with open(temp_path, "wb") as output:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_report >= 0.1:
                        print_progress(downloaded, total_bytes, started_at)
                        last_report = now

            print_progress(downloaded, total_bytes, started_at, final=True)
            os.replace(temp_path, target_path)
            print(f"下载完成: {os.path.abspath(target_path)}")
            if cfg.get("open_after_download"):
                open_downloaded_file(target_path, cfg)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        if "temp_path" in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        print(f"下载失败: {e}")
        return False


def open_download_in_browser(set_id: str, cfg: dict, source_window=None) -> bool:
    try:
        mode = get_download_mode(cfg)
        print(f"尝试自动下载谱面 {set_id} ({mode})...")
        download_url = build_download_url(set_id, cfg)
        print(f"打开下载链接: {download_url}")
        if not open_new_tab_in_source_browser(download_url, source_window):
            webbrowser.open_new_tab(download_url)
        print("已打开下载链接，请在浏览器中确认下载。")
        return True
    except Exception as e:
        print(f"打开下载链接时出错: {e}")
        return False


def auto_download(set_id: str, cfg: dict, source_window=None):
    if get_download_method(cfg) == "browser":
        return open_download_in_browser(set_id, cfg, source_window)
    return download_to_file(set_id, cfg)


def extract_set_id(url: str) -> str | None:
    match = re.search(r"/beatmapsets/(\d+)(?:[/?#]|$)", url, re.I)
    return match.group(1) if match else None


def open_search(set_id: str, cfg: dict, source_window=None, source_edit=None):
    url = cfg["search_template"].format(id=set_id)
    if cfg.get("keep_original"):
        if open_new_tab_in_source_browser(url, source_window):
            return
        try:
            webbrowser.open_new_tab(url)
            return
        except Exception:
            pass

    if source_window and source_edit:
        try:
            source_window.set_focus()
            source_edit.set_edit_text(url)
            source_edit.type_keys("{ENTER}", set_foreground=False)
            return
        except Exception:
            pass

    webbrowser.open(url, new=0)


def find_address_edit(window):
    patterns = [
        "address and search bar",
        "search google or type a url",
        "search or enter address",
        "search with",
        "address bar",
        "url",
        "地址",
        "搜索",
        "地址栏",
        "地址和搜索栏",
    ]
    try:
        for edit in window.descendants(control_type="Edit"):
            name = (edit.element_info.name or "").lower()
            if any(p in name for p in patterns):
                return edit
            try:
                value = edit.get_value()
            except Exception:
                continue
            if isinstance(value, str) and is_url(value.strip()):
                return edit
    except Exception:
        pass
    return None


def get_url_from_window(window):
    edit = find_address_edit(window)
    if edit is not None:
        try:
            value = edit.get_value()
        except Exception:
            value = None
        if isinstance(value, str):
            value = value.strip()
            if extract_set_id(value):
                return value, window, edit

    try:
        title = window.window_text().strip()
        if extract_set_id(title):
            return title, window, None
    except Exception:
        pass
    return None, None, None


def get_active_browser_url():
    if Desktop is None:
        return None, None, None
    try:
        desktop = Desktop(backend="uia")
    except Exception:
        return None, None, None

    active = None
    try:
        active = desktop.get_active()
    except Exception:
        pass

    candidates = [active] if active is not None else []
    try:
        for window in desktop.windows():
            title = (window.window_text() or "").lower()
            class_name = (window.element_info.class_name or "").lower()
            if any(key in title for key in ("chrome", "edge", "browser", "osu.ppy.sh")) or any(
                key in class_name
                for key in ("chrome_widgetwin_1", "applicationframewindow", "browser")
            ):
                if window not in candidates:
                    candidates.append(window)
    except Exception:
        pass

    for window in candidates:
        result = get_url_from_window(window)
        if result[0]:
            return result
    return None, None, None


def monitor_browser_url(loop_interval: float = 1.0, stop_event=None, cfg_provider=None):
    cfg_provider = cfg_provider or load_config
    last_url = ""
    print("开始监听当前浏览器地址栏，检测 osu 谱面链接。")

    try:
        while not (stop_event and stop_event.is_set()):
            cfg = cfg_provider()
            try:
                url, source_window, source_edit = get_active_browser_url()
            except Exception as err:
                print("检测浏览器地址栏时发生异常:", err)
                url, source_window, source_edit = None, None, None

            if not url:
                last_url = ""
                time.sleep(loop_interval)
                continue

            if url and url != last_url:
                last_url = url
                set_id = extract_set_id(url)
                if set_id:
                    if cfg.get("auto_download"):
                        if get_download_method(cfg) != "direct":
                            print(f"检测到 set_id: {set_id}，正在打开 sayobot。")
                            open_search(set_id, cfg, source_window, source_edit)
                        print("尝试自动下载谱面...")
                        auto_download(set_id, cfg, source_window)
                    else:
                        print(f"检测到 set_id: {set_id}，正在打开 sayobot。")
                        open_search(set_id, cfg, source_window, source_edit)
                else:
                    last_url = ""
            time.sleep(loop_interval)
    except KeyboardInterrupt:
        print("已退出。")
    print("监听已停止。")


class QueueWriter:
    def __init__(self, output_queue):
        self.output_queue = output_queue

    def write(self, text):
        if text:
            self.output_queue.put(text)

    def flush(self):
        pass


class PySideConfigApp(QMainWindow if QApplication else object):
    DOWNLOAD_MODE_LABELS = {"完整谱面": "full", "无视频谱面": "novideo"}
    DOWNLOAD_METHOD_LABELS = {"程序内下载": "direct", "浏览器打开": "browser"}
    OPEN_WITH_LABELS = {"系统默认": "default", "osu! stable": "stable", "osu! lazer": "lazer"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("osu! Sayobot Helper")
        self.setFixedSize(1180, 780)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = QueueWriter(self.log_queue)
        sys.stderr = QueueWriter(self.log_queue)
        self.cfg = load_config()

        self.vars = {}
        self.option_groups = {}
        self.nav_buttons = {}
        self.drag_pos = None
        self.loading_vars = False

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.auto_save)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.drain_log_queue)
        self.log_timer.start(100)

        self.build_ui()
        self.load_vars()
        self.show_page("status")
        self.start_monitor()

    def build_ui(self):
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei UI";
                color: #111827;
                background: transparent;
                font-size: 14px;
            }
            QWidget#AppShell {
                background: #f6f8fb;
                border-radius: 18px;
            }
            #Sidebar {
                background: #ffffff;
                border-right: 1px solid #e5e7eb;
            }
            #Brand {
                color: #1261ff;
                font-size: 22px;
                font-weight: 800;
            }
            #PageTitle {
                background: #f6f8fb;
                font-size: 22px;
                font-weight: 800;
            }
            QPushButton {
                border: none;
                border-radius: 12px;
                padding: 10px 14px;
                text-align: left;
                background: transparent;
            }
            QPushButton:hover {
                background: #f1f6ff;
            }
            QPushButton[active="true"], QPushButton:checked {
                background: #e8f0ff;
                color: #1261ff;
            }
            QPushButton#PrimaryButton {
                background: #e8f0ff;
                color: #111827;
                text-align: center;
                font-weight: 600;
            }
            QPushButton#PrimaryButton:hover {
                background: #dbeafe;
            }
            QPushButton#WindowButton {
                background: transparent;
                color: #64748b;
                font-size: 20px;
                font-weight: 800;
                text-align: center;
                padding: 0;
            }
            QPushButton#WindowButton:hover {
                background: #e8f0ff;
            }
            QPushButton#CloseButton:hover {
                background: #fee2e2;
                color: #dc2626;
            }
            QFrame#Card, QFrame#SoftCard, QPushButton#OptionCard {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 18px;
            }
            QFrame#SoftCard {
                background: #f8fafc;
                border-radius: 14px;
            }
            QPushButton#OptionCard {
                padding: 18px 22px;
                text-align: left;
                color: #111827;
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton#OptionCard:checked {
                background: #eef4ff;
                border: 1px solid #1261ff;
                color: #111827;
            }
            QLabel#Muted {
                color: #64748b;
                background: transparent;
            }
            QLabel#SectionTitle {
                font-size: 18px;
                font-weight: 800;
                background: transparent;
            }
            QLabel#Metric {
                font-size: 36px;
                font-weight: 900;
                background: transparent;
            }
            QLabel#Badge {
                border-radius: 0;
                padding: 8px 18px;
                background: #ffe8e8;
                color: #dc2626;
            }
            QLineEdit {
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 10px 12px;
                background: #ffffff;
            }
            QCheckBox {
                background: transparent;
                spacing: 12px;
                font-weight: 700;
                padding: 4px;
            }
            QCheckBox::indicator {
                width: 22px;
                height: 22px;
                border-radius: 6px;
                border: 2px solid #cbd5e1;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #1261ff;
                border: 2px solid #1261ff;
                image: none;
            }
            QTextEdit {
                background: #0f172a;
                color: #e5e7eb;
                border: none;
                border-radius: 14px;
                padding: 14px;
                font-family: Consolas;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                border-radius: 5px;
                min-height: 36px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )

        root = QWidget()
        root.setObjectName("AppShell")
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(255)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 18)
        sidebar_layout.setSpacing(10)

        brand_row = QWidget()
        brand_row.setObjectName("Brand")
        brand_row.setFixedHeight(52)
        brand_layout = QHBoxLayout(brand_row)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)
        brand_icon = QLabel()
        brand_icon.setFixedSize(40, 40)
        brand_icon_path = APP_ICON_IMAGE_PATH if os.path.exists(APP_ICON_IMAGE_PATH) else ICON_PATH
        if os.path.exists(brand_icon_path):
            brand_icon.setPixmap(QPixmap(brand_icon_path).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_text = QLabel("osu! Sayobot")
        brand_text.setObjectName("Brand")
        brand_layout.addWidget(brand_icon)
        brand_layout.addWidget(brand_text)
        brand_layout.addStretch()
        sidebar_layout.addWidget(brand_row)

        nav_items = [
            ("status", "设备状态"),
            ("config", "参数配置"),
            ("paths", "osu! 路径"),
            ("logs", "日志"),
            ("about", "关于"),
        ]
        for key, text in nav_items:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setFixedHeight(50)
            button.clicked.connect(lambda _checked=False, page=key: self.show_page(page))
            self.nav_buttons[key] = button
            sidebar_layout.addWidget(button)

        sidebar_layout.addStretch()
        version = QLabel("版本 3.5.1")
        version.setObjectName("Muted")
        author = QLabel("作者 ColdSnowFox")
        author.setObjectName("Muted")
        sidebar_layout.addWidget(version)
        sidebar_layout.addWidget(author)
        root_layout.addWidget(sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(28, 22, 28, 20)
        main_layout.setSpacing(20)

        topbar = QWidget()
        topbar.setFixedHeight(42)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(0, 0, 0, 0)
        self.page_title = QLabel("设备状态")
        self.page_title.setObjectName("PageTitle")
        topbar_layout.addWidget(self.page_title)
        topbar_layout.addStretch()
        self.status_badge = QLabel("未连接")
        self.status_badge.setObjectName("Badge")
        topbar_layout.addWidget(self.status_badge)
        minimize = QPushButton("−")
        minimize.setObjectName("WindowButton")
        minimize.setFixedSize(44, 38)
        minimize.clicked.connect(self.showMinimized)
        close = QPushButton("×")
        close.setObjectName("CloseButton")
        close.setFixedSize(44, 38)
        close.clicked.connect(self.close)
        topbar_layout.addWidget(minimize)
        topbar_layout.addWidget(close)
        topbar.mousePressEvent = self.begin_move
        topbar.mouseMoveEvent = self.do_move
        main_layout.addWidget(topbar)

        self.pages = QStackedWidget()
        self.page_indexes = {}
        main_layout.addWidget(self.pages, 1)
        root_layout.addWidget(main, 1)

        self.build_status_page()
        self.build_config_page()
        self.build_paths_page()
        self.build_logs_page()
        self.build_about_page()

    def make_page(self, key):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        self.page_indexes[key] = self.pages.addWidget(page)
        return page, layout

    def card(self, title=None):
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        if title:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            layout.addWidget(label)
        return frame, layout

    def build_status_page(self):
        page, layout = self.make_page("status")
        top = QHBoxLayout()
        top.setSpacing(20)
        monitor, monitor_layout = self.card("监听状态")
        self.status_label = QLabel("监听中")
        self.status_label.setObjectName("Metric")
        self.save_state_label = QLabel("配置会自动保存")
        self.save_state_label.setObjectName("Muted")
        monitor_layout.addWidget(self.status_label)
        monitor_layout.addWidget(self.save_state_label)
        top.addWidget(monitor, 1)

        quick, quick_layout = self.card("快速操作")
        quick_layout.addWidget(self.action_button("启动监听", self.start_monitor))
        quick_layout.addWidget(self.action_button("停止监听", self.stop_monitor))
        top.addWidget(quick, 1)
        layout.addLayout(top)

        info, info_layout = self.card("当前下载配置")
        info_grid = QGridLayout()
        info_grid.setSpacing(16)
        self.status_download_mode = QLabel()
        self.status_download_method = QLabel()
        self.status_open_with = QLabel()
        self.info_tile(info_grid, 0, 0, "下载类型", self.status_download_mode)
        self.info_tile(info_grid, 0, 1, "下载方式", self.status_download_method)
        self.info_tile(info_grid, 0, 2, "打开方式", self.status_open_with)
        info_layout.addLayout(info_grid)
        layout.addWidget(info)
        layout.addStretch()

    def info_tile(self, grid, row, column, label, value_label):
        tile = QFrame()
        tile.setObjectName("SoftCard")
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel(label)
        title.setObjectName("Muted")
        value_label.setStyleSheet("background: transparent; font-size: 16px; font-weight: 700;")
        tile_layout.addWidget(title)
        tile_layout.addWidget(value_label)
        grid.addWidget(tile, row, column)

    def build_config_page(self):
        page, layout = self.make_page("config")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 10, 0)
        content_layout.setSpacing(16)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        form, form_layout = self.card("参数配置")
        self.add_option_group(
            form_layout,
            "下载类型",
            "download_mode",
            [
                ("完整谱面", "包含背景视频与完整资源"),
                ("无视频谱面", "体积更小，下载更快"),
            ],
            self.DOWNLOAD_MODE_LABELS,
        )
        self.add_option_group(
            form_layout,
            "下载方式",
            "download_method",
            [
                ("程序内下载", "直接保存到本地目录"),
                ("浏览器打开", "沿用浏览器下载链接"),
            ],
            self.DOWNLOAD_METHOD_LABELS,
        )
        self.add_path_row(form_layout, "保存目录", "download_dir", True)
        self.add_option_group(
            form_layout,
            "打开方式",
            "open_with",
            [
                ("系统默认", "使用 .osz 默认关联"),
                ("osu! stable", "下载后导入 stable"),
                ("osu! lazer", "下载后导入 lazer"),
            ],
            self.OPEN_WITH_LABELS,
        )

        checks = QFrame()
        checks.setObjectName("SoftCard")
        checks_layout = QVBoxLayout(checks)
        checks_layout.setContentsMargins(18, 16, 18, 16)
        checks_layout.setSpacing(10)
        self.vars["auto_download"] = QCheckBox("自动下载谱面")
        self.vars["open_after_download"] = QCheckBox("下载完成后自动打开")
        self.vars["keep_original"] = QCheckBox("浏览器打开时保留 osu! 原页面")
        for check in (self.vars["auto_download"], self.vars["open_after_download"], self.vars["keep_original"]):
            check.stateChanged.connect(self.schedule_auto_save)
            checks_layout.addWidget(check)
        form_layout.addWidget(checks)
        content_layout.addWidget(form)
        content_layout.addStretch()

    def add_option_group(self, parent_layout, title, key, options, mapping):
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        parent_layout.addWidget(title_label)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.option_groups[key] = {"group": group, "buttons": {}, "mapping": mapping}
        for label, hint in options:
            button = QPushButton(f"{label}\n{hint}")
            button.setObjectName("OptionCard")
            button.setCheckable(True)
            button.setMinimumHeight(86)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda _checked=False, option_key=key: self.option_changed(option_key))
            group.addButton(button)
            self.option_groups[key]["buttons"][label] = button
            parent_layout.addWidget(button)

    def add_path_row(self, parent_layout, label, key, folder=False):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)
        title = QLabel(label)
        title.setFixedWidth(90)
        edit = QLineEdit()
        edit.textChanged.connect(self.schedule_auto_save)
        self.vars[key] = edit
        button = self.action_button("浏览", lambda: self.choose_path(key, folder))
        button.setFixedWidth(116)
        row_layout.addWidget(title)
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(button)
        parent_layout.addWidget(row)

    def build_paths_page(self):
        page, layout = self.make_page("paths")
        card, card_layout = self.card("osu! 路径")
        self.add_path_row(card_layout, "stable", "stable_path")
        self.add_path_row(card_layout, "lazer", "lazer_path")
        card_layout.addWidget(self.action_button("检测 osu! 路径", self.detect_osu_paths))
        layout.addWidget(card)
        layout.addStretch()

    def build_logs_page(self):
        page, layout = self.make_page("logs")
        card, card_layout = self.card("日志")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        card_layout.addWidget(self.log_text, 1)
        layout.addWidget(card, 1)

    def build_about_page(self):
        page, layout = self.make_page("about")
        card, card_layout = self.card("关于")
        title = QLabel("osu! Sayobot Helper")
        title.setObjectName("Metric")
        body = QLabel("一个用于监听 osu! 谱面页面、自动下载并导入谱面的轻量工具。")
        body.setObjectName("Muted")
        github = QLabel(
            'GitHub：<a href="https://github.com/ColdSnowFox/osu-sayobot-helper">'
            "https://github.com/ColdSnowFox/osu-sayobot-helper</a>"
        )
        github.setObjectName("Muted")
        github.setOpenExternalLinks(True)
        releases = QLabel(
            '发布页：<a href="https://github.com/ColdSnowFox/osu-sayobot-helper/releases">'
            "https://github.com/ColdSnowFox/osu-sayobot-helper/releases</a>"
        )
        releases.setObjectName("Muted")
        releases.setOpenExternalLinks(True)
        card_layout.addWidget(title)
        card_layout.addWidget(body)
        card_layout.addSpacing(8)
        card_layout.addWidget(github)
        card_layout.addWidget(releases)
        layout.addWidget(card)
        layout.addStretch()

    def action_button(self, text, callback):
        button = QPushButton(text)
        button.setObjectName("PrimaryButton")
        button.setFixedHeight(42)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def show_page(self, key):
        self.pages.setCurrentIndex(self.page_indexes[key])
        titles = {
            "status": "设备状态",
            "config": "参数配置",
            "paths": "osu! 路径",
            "logs": "日志",
            "about": "关于",
        }
        self.page_title.setText(titles[key])
        for page_key, button in self.nav_buttons.items():
            button.setChecked(page_key == key)

    def load_vars(self):
        self.loading_vars = True
        for key, data in self.option_groups.items():
            value = self.cfg.get(key, "")
            selected = self.value_to_label(key, str(value))
            for label, button in data["buttons"].items():
                button.setChecked(label == selected)
        for key, widget in self.vars.items():
            value = self.cfg.get(key, "")
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
        self.loading_vars = False
        self.update_status_tiles()

    def option_changed(self, key):
        self.schedule_auto_save()
        self.update_status_tiles()

    def selected_option_label(self, key):
        for label, button in self.option_groups[key]["buttons"].items():
            if button.isChecked():
                return label
        return next(iter(self.option_groups[key]["buttons"]))

    def collect_config(self):
        updated = self.cfg.copy()
        for key in self.option_groups:
            updated[key] = self.label_to_value(key, self.selected_option_label(key))
        for key, widget in self.vars.items():
            if isinstance(widget, QCheckBox):
                updated[key] = widget.isChecked()
            elif isinstance(widget, QLineEdit):
                updated[key] = widget.text().strip()
        return updated

    def schedule_auto_save(self, *_args):
        if self.loading_vars:
            return
        self.save_state_label.setText("正在自动保存...")
        self.update_status_tiles()
        self.save_timer.start(350)

    def auto_save(self):
        self.cfg = self.collect_config()
        save_config(self.cfg)
        self.save_state_label.setText("配置已自动保存")
        print("配置已自动保存。")

    def update_status_tiles(self):
        if hasattr(self, "status_download_mode"):
            self.status_download_mode.setText(self.selected_option_label("download_mode"))
            self.status_download_method.setText(self.selected_option_label("download_method"))
            self.status_open_with.setText(self.selected_option_label("open_with"))

    def value_to_label(self, key, value):
        maps = {
            "download_mode": self.DOWNLOAD_MODE_LABELS,
            "download_method": self.DOWNLOAD_METHOD_LABELS,
            "open_with": self.OPEN_WITH_LABELS,
        }
        for label, internal in maps.get(key, {}).items():
            if internal == value:
                return label
        return next(iter(maps[key]))

    def label_to_value(self, key, label):
        maps = {
            "download_mode": self.DOWNLOAD_MODE_LABELS,
            "download_method": self.DOWNLOAD_METHOD_LABELS,
            "open_with": self.OPEN_WITH_LABELS,
        }
        return maps.get(key, {}).get(label, label)

    def choose_path(self, key, folder=False):
        try:
            title = "选择保存目录" if folder else "选择启动器"
            current = self.vars[key].text().strip()
            start_dir = current if folder else os.path.dirname(current)
            path = choose_folder_windows(title, start_dir) if folder else choose_file_windows(title, start_dir)

            if path:
                self.vars[key].setText(path)
        except Exception as e:
            print(f"打开选择窗口失败: {e}")

    def detect_osu_paths(self):
        self.cfg = self.collect_config()
        self.cfg["stable_path"] = ""
        self.cfg["lazer_path"] = ""
        self.cfg = autofill_launcher_paths(self.cfg)
        save_config(self.cfg)
        self.load_vars()
        self.save_state_label.setText("osu! 路径已检测并保存")
        print("osu! 路径检测完成。")

    def start_monitor(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        self.cfg = self.collect_config()
        save_config(self.cfg)
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(
            target=monitor_browser_url,
            kwargs={"stop_event": self.stop_event, "cfg_provider": lambda: self.cfg},
            daemon=True,
        )
        self.monitor_thread.start()
        self.status_label.setText("监听中")
        self.status_badge.setText("监听中")
        self.status_badge.setStyleSheet("background: #e8f8ef; color: #15803d; padding: 8px 18px;")
        print("监听已启动。")

    def stop_monitor(self):
        self.stop_event.set()
        self.status_label.setText("监听已停止")
        self.status_badge.setText("已停止")
        self.status_badge.setStyleSheet("background: #ffe8e8; color: #dc2626; padding: 8px 18px;")
        print("正在停止监听...")

    def begin_move(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def do_move(self, event):
        if self.drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)

    def apply_rounded_mask(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 18, 18)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):
        self.apply_rounded_mask()
        super().resizeEvent(event)

    def showEvent(self, event):
        self.apply_rounded_mask()
        super().showEvent(event)

    def drain_log_queue(self):
        while True:
            try:
                text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if hasattr(self, "log_text"):
                if text.startswith("\r"):
                    cursor = self.log_text.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    cursor.select(QTextCursor.LineUnderCursor)
                    cursor.removeSelectedText()
                    cursor.insertText(text[1:])
                else:
                    self.log_text.moveCursor(QTextCursor.End)
                    self.log_text.insertPlainText(text)
                self.log_text.ensureCursorVisible()

    def closeEvent(self, event):
        self.stop_monitor()
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        event.accept()


def run_gui():
    setup_console_encoding()
    if QApplication is None:
        raise RuntimeError("PySide6 未安装，请先运行 pip install -r requirements.txt")
    app = QApplication(sys.argv)
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = PySideConfigApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--cli" in sys.argv:
        setup_console_encoding()
        monitor_browser_url()
    else:
        run_gui()

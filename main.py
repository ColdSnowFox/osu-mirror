# -*- coding: utf-8 -*-

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

try:
    import ctypes
except Exception:
    ctypes = None

try:
    import winreg
except Exception:
    winreg = None

try:
    from pywinauto import Desktop
except Exception:
    Desktop = None

CONFIG_PATH = "config.json"


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


class RoundedFrame(tk.Frame):
    def __init__(self, parent, radius=16, bg="#ffffff", border="#e5e7eb", padding=6, **kwargs):
        parent_bg = "#f6f8fb"
        try:
            parent_bg = parent.cget("bg")
        except tk.TclError:
            pass
        super().__init__(parent, bg=parent_bg, **kwargs)
        self.radius = radius
        self.bg_color = bg
        self.border_color = border
        self.padding = padding
        self.canvas = tk.Canvas(self, bg=parent_bg, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.inner = tk.Frame(self, bg=bg)
        self.inner.place(x=padding, y=padding, relwidth=1, relheight=1, width=-padding * 2, height=-padding * 2)
        self.bind("<Configure>", self.draw)

    def draw(self, _event=None):
        self.canvas.delete("all")
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        self.draw_round_rect(0, 0, width - 1, height - 1, self.radius, self.border_color)
        self.draw_round_rect(1, 1, width - 2, height - 2, max(1, self.radius - 1), self.bg_color)

    def draw_round_rect(self, x1, y1, x2, y2, radius, color):
        radius = min(radius, int((x2 - x1) / 2), int((y2 - y1) / 2))
        self.canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=color, outline=color)
        self.canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=color, outline=color)
        self.canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, fill=color, outline=color)
        self.canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, fill=color, outline=color)
        self.canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, fill=color, outline=color)
        self.canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, fill=color, outline=color)


class RoundedButton(RoundedFrame):
    def __init__(self, parent, text, command=None, radius=10, bg="#e8f0ff", fg="#111827", active_bg="#dbeafe", height=40):
        super().__init__(parent, radius=radius, bg=bg, border=bg, padding=2, height=height, cursor="hand2")
        self.command = command
        self.normal_bg = bg
        self.normal_fg = fg
        self.active_bg = active_bg
        self.label = tk.Label(self.inner, text=text, bg=bg, fg=fg, font=("Microsoft YaHei UI", 10), anchor="w", padx=14)
        self.label.pack(fill=tk.BOTH, expand=True)
        for widget in (self, self.canvas, self.inner, self.label):
            widget.bind("<Button-1>", self.invoke)
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)

    def invoke(self, _event=None):
        if self.command:
            self.command()

    def on_enter(self, _event=None):
        self.set_colors(self.active_bg, self.normal_fg)

    def on_leave(self, _event=None):
        self.set_colors(self.normal_bg, self.normal_fg)

    def set_colors(self, bg, fg):
        self.bg_color = bg
        self.border_color = bg
        self.inner.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg)
        self.draw()

    def set_active(self, active):
        if active:
            self.normal_bg = "#e8f0ff"
            self.normal_fg = "#1261ff"
        else:
            self.normal_bg = "#ffffff"
            self.normal_fg = "#1f2937"
        self.set_colors(self.normal_bg, self.normal_fg)


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg="#f6f8fb"):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = tk.Canvas(self, width=10, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self.update_scroll_region)
        self.canvas.bind("<Configure>", self.update_canvas_width)
        self.canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        self.scrollbar.bind("<Button-1>", self.on_scrollbar_click)

    def update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.draw_scrollbar()

    def update_canvas_width(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self.draw_scrollbar()

    def on_mousewheel(self, event):
        if self.winfo_containing(event.x_root, event.y_root) is None:
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        self.draw_scrollbar()

    def on_scrollbar_click(self, event):
        height = max(1, self.scrollbar.winfo_height())
        self.canvas.yview_moveto(event.y / height)
        self.draw_scrollbar()

    def draw_scrollbar(self):
        self.scrollbar.delete("all")
        first, last = self.canvas.yview()
        height = max(1, self.scrollbar.winfo_height())
        if first <= 0 and last >= 1:
            return
        thumb_top = max(4, int(first * height))
        thumb_bottom = min(height - 4, int(last * height))
        if thumb_bottom - thumb_top < 30:
            thumb_bottom = min(height - 4, thumb_top + 30)
        self.scrollbar.create_rectangle(3, thumb_top, 7, thumb_bottom, fill="#cbd5e1", outline="")


class ConfigApp:
    DOWNLOAD_MODE_LABELS = {"完整谱面": "full", "无视频谱面": "novideo"}
    DOWNLOAD_METHOD_LABELS = {"程序内下载": "direct", "浏览器打开": "browser"}
    OPEN_WITH_LABELS = {"系统默认": "default", "osu! stable": "stable", "osu! lazer": "lazer"}

    def __init__(self, root):
        self.root = root
        self.root.title("osu! Sayobot Helper")
        self.root.geometry("1180x780")
        self.root.resizable(False, False)
        self.root.configure(bg="#f6f8fb")
        self.root.overrideredirect(True)

        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = QueueWriter(self.log_queue)
        sys.stderr = QueueWriter(self.log_queue)
        self.cfg = load_config()

        self.vars = {}
        self.option_cards = {}
        self.nav_buttons = {}
        self.pages = {}
        self.current_page = None
        self.status_var = tk.StringVar(value="监听未启动")
        self.save_state_var = tk.StringVar(value="配置会自动保存")
        self.page_title_var = tk.StringVar(value="设备状态")
        self.save_after_id = None
        self.loading_vars = False

        self.setup_style()
        self.build_ui()
        self.load_vars()
        self.bind_auto_save()
        self.show_page("status")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain_log_queue)
        self.root.after(200, self.show_in_taskbar)
        self.start_monitor()

    def setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background="#ffffff", foreground="#111827", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#ffffff")
        style.configure("App.TFrame", background="#f6f8fb")
        style.configure("Sidebar.TFrame", background="#ffffff")
        style.configure("Main.TFrame", background="#f6f8fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#111827")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#64748b")
        style.configure("PageTitle.TLabel", background="#f6f8fb", foreground="#111827", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Brand.TLabel", background="#ffffff", foreground="#1261ff", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#111827", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Metric.TLabel", background="#ffffff", foreground="#111827", font=("Microsoft YaHei UI", 24, "bold"))
        style.configure("TButton", padding=(12, 8), background="#edf5ff", foreground="#111827", borderwidth=0)
        style.map("TButton", background=[("active", "#dbeafe")])
        style.configure("TCheckbutton", background="#ffffff", foreground="#111827")
        style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff", arrowcolor="#111827")

    def build_ui(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", padding=(14, 20))
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_rowconfigure(8, weight=1)
        sidebar.configure(width=255)
        sidebar.grid_propagate(False)

        ttk.Label(sidebar, text="🎮 osu! Sayobot", style="Brand.TLabel").grid(row=0, column=0, sticky="w", padx=6, pady=(0, 28))
        nav_items = [
            ("status", "设备状态"),
            ("config", "参数配置"),
            ("paths", "osu! 路径"),
            ("logs", "日志"),
            ("about", "关于"),
        ]
        for index, (key, text) in enumerate(nav_items, start=1):
            button = RoundedButton(
                sidebar,
                text=text,
                bg="#ffffff",
                fg="#1f2937",
                active_bg="#e8f0ff",
                height=50,
                command=lambda page=key: self.show_page(page),
            )
            button.grid(row=index, column=0, sticky="ew", pady=4)
            self.nav_buttons[key] = button

        footer = ttk.Frame(sidebar, style="Sidebar.TFrame")
        footer.grid(row=9, column=0, sticky="sew", pady=(20, 0))
        ttk.Label(footer, text="版本 2.1.0", style="Muted.TLabel").pack(anchor="w", padx=6)
        ttk.Label(footer, text="作者 ColdSnowFox", style="Muted.TLabel").pack(anchor="w", padx=6, pady=(8, 0))

        main = ttk.Frame(self.root, style="Main.TFrame", padding=(24, 18))
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        topbar = ttk.Frame(main, style="Main.TFrame")
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        topbar.grid_columnconfigure(0, weight=1)
        topbar.bind("<ButtonPress-1>", self.begin_move)
        topbar.bind("<B1-Motion>", self.do_move)
        ttk.Label(topbar, textvariable=self.page_title_var, style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.status_badge = tk.Label(
            topbar,
            textvariable=self.status_var,
            bg="#ffe8e8",
            fg="#dc2626",
            padx=14,
            pady=7,
            font=("Microsoft YaHei UI", 10),
        )
        self.status_badge.grid(row=0, column=1, sticky="e", padx=(0, 18))
        tk.Button(
            topbar,
            text="−",
            command=self.minimize_window,
            relief=tk.FLAT,
            bd=0,
            bg="#f6f8fb",
            fg="#64748b",
            activebackground="#e8f0ff",
            font=("Microsoft YaHei UI", 16, "bold"),
            width=3,
        ).grid(row=0, column=2, sticky="e")
        tk.Button(
            topbar,
            text="×",
            command=self.on_close,
            relief=tk.FLAT,
            bd=0,
            bg="#f6f8fb",
            fg="#64748b",
            activebackground="#fee2e2",
            activeforeground="#dc2626",
            font=("Microsoft YaHei UI", 16, "bold"),
            width=3,
        ).grid(row=0, column=3, sticky="e")

        self.content = ttk.Frame(main, style="Main.TFrame")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.build_status_page()
        self.build_config_page()
        self.build_paths_page()
        self.build_logs_page()
        self.build_about_page()

    def create_page(self, key):
        frame = ttk.Frame(self.content, style="Main.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = frame
        return frame

    def card(self, parent, row, column, title, columnspan=1, rowspan=1, sticky="nsew", height=160):
        rounded = RoundedFrame(parent, radius=18, bg="#ffffff", border="#e5e7eb", height=height)
        rounded.grid(row=row, column=column, columnspan=columnspan, rowspan=rowspan, sticky=sticky, padx=10, pady=10)
        frame = rounded.inner
        frame.grid_columnconfigure(0, weight=1)
        ttk.Label(frame, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=20, pady=(18, 10))
        return frame

    def build_status_page(self):
        page = self.create_page("status")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)

        monitor = self.card(page, 0, 0, "监听状态", height=160)
        ttk.Label(monitor, textvariable=self.status_var, style="Metric.TLabel").grid(row=1, column=0, sticky="w", padx=22)
        ttk.Label(monitor, textvariable=self.save_state_var, style="Muted.TLabel").grid(row=2, column=0, sticky="w", padx=22, pady=(4, 20))

        quick = self.card(page, 0, 1, "快速操作", height=160)
        self.add_round_button(quick, "启动监听", self.start_monitor, row=1, column=0, padx=22, pady=(2, 8))
        self.add_round_button(quick, "停止监听", self.stop_monitor, row=2, column=0, padx=22, pady=(0, 20))

        info = self.card(page, 1, 0, "当前下载配置", columnspan=2, height=410)
        info.grid_columnconfigure((0, 1, 2), weight=1)
        self.status_download_mode = tk.StringVar()
        self.status_download_method = tk.StringVar()
        self.status_open_with = tk.StringVar()
        self.info_tile(info, 1, 0, "下载类型", self.status_download_mode)
        self.info_tile(info, 1, 1, "下载方式", self.status_download_method)
        self.info_tile(info, 1, 2, "打开方式", self.status_open_with)

    def info_tile(self, parent, row, column, label, value_var):
        rounded = RoundedFrame(parent, radius=10, bg="#f8fafc", border="#edf2f7", height=76)
        rounded.grid(row=row, column=column, sticky="ew", padx=20 if column == 0 else 8, pady=(8, 20))
        tile = rounded.inner
        tk.Label(tile, text=label, bg="#f8fafc", fg="#64748b", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(tile, textvariable=value_var, bg="#f8fafc", fg="#111827", font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=14, pady=(0, 12))

    def build_config_page(self):
        page = self.create_page("config")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.config_scroll = ScrollFrame(page)
        self.config_scroll.grid(row=0, column=0, sticky="nsew")
        self.config_scroll.inner.grid_columnconfigure(0, weight=1)
        form_card = self.card(self.config_scroll.inner, 0, 0, "参数配置", height=860)
        form_card.grid_columnconfigure(0, weight=1)

        self.vars["download_mode"] = tk.StringVar()
        self.vars["download_method"] = tk.StringVar()
        self.vars["open_with"] = tk.StringVar()

        self.add_option_group(
            form_card,
            1,
            "下载类型",
            "download_mode",
            [
                ("完整谱面", "包含背景视频与完整资源"),
                ("无视频谱面", "体积更小，下载更快"),
            ],
        )
        self.add_option_group(
            form_card,
            2,
            "下载方式",
            "download_method",
            [
                ("程序内下载", "直接保存到本地目录"),
                ("浏览器打开", "沿用浏览器下载链接"),
            ],
        )
        self.add_path_entry(form_card, 3, "保存目录", "download_dir", folder=True)
        self.add_option_group(
            form_card,
            4,
            "打开方式",
            "open_with",
            [
                ("系统默认", "使用 .osz 默认关联"),
                ("osu! stable", "下载后导入 stable"),
                ("osu! lazer", "下载后导入 lazer"),
            ],
            columns=2,
        )

        self.vars["keep_original"] = tk.BooleanVar()
        self.vars["auto_download"] = tk.BooleanVar()
        self.vars["open_after_download"] = tk.BooleanVar()
        checks_round = RoundedFrame(form_card, radius=16, bg="#f8fafc", border="#e5e7eb", height=158)
        checks_round.grid(row=5, column=0, sticky="ew", padx=20, pady=(10, 22))
        checks = checks_round.inner
        self.check_cards = []
        self.add_check_option(checks, "自动下载谱面", self.vars["auto_download"])
        self.add_check_option(checks, "下载完成后自动打开", self.vars["open_after_download"])
        self.add_check_option(checks, "浏览器打开时保留 osu! 原页面", self.vars["keep_original"])

    def add_option_group(self, parent, row, title, key, options, columns=None):
        columns = columns or len(options)
        rows = (len(options) + columns - 1) // columns
        group = ttk.Frame(parent)
        group.grid(row=row, column=0, sticky="ew", padx=22, pady=(10, 14))
        min_width = 220 if columns >= 3 else 270
        for column in range(columns):
            group.grid_columnconfigure(column, weight=1, minsize=min_width, uniform=key)
        ttk.Label(group, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=columns, sticky="w", pady=(0, 12))
        self.option_cards[key] = []

        for index, (label, hint) in enumerate(options):
            row_index = 1 + index // columns
            column = index % columns
            rounded = RoundedFrame(group, radius=14, bg="#ffffff", border="#d8dee8", cursor="hand2", height=106)
            rounded.grid(
                row=row_index,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 9, 0 if column == columns - 1 else 9),
                pady=(0, 12 if row_index < rows else 0),
            )
            card = rounded.inner
            dot = tk.Label(card, text="○", bg="#ffffff", fg="#cbd5e1", font=("Microsoft YaHei UI", 20))
            dot.grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=18, sticky="n")
            title_label = tk.Label(card, text=label, bg="#ffffff", fg="#111827", font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
            title_label.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(18, 3))
            hint_label = tk.Label(card, text=hint, bg="#ffffff", fg="#64748b", font=("Microsoft YaHei UI", 9), wraplength=240, justify="left", anchor="w")
            hint_label.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=(0, 16))
            card.grid_columnconfigure(1, weight=1)

            widgets = (rounded, card, dot, title_label, hint_label)
            for widget in widgets:
                widget.bind("<Button-1>", lambda _event, value=label, var=self.vars[key]: var.set(value))
            self.option_cards[key].append((label, widgets))

    def add_check_option(self, parent, text, var):
        row = len(getattr(self, "check_cards", []))
        item = tk.Frame(parent, bg="#f8fafc", cursor="hand2")
        item.pack(fill=tk.X, padx=16, pady=(10 if row == 0 else 4, 0))
        box = tk.Label(
            item,
            width=2,
            height=1,
            text="",
            bg="#ffffff",
            fg="#ffffff",
            bd=0,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        box.pack(side=tk.LEFT, padx=(0, 10))
        label = tk.Label(item, text=text, bg="#f8fafc", fg="#111827", font=("Microsoft YaHei UI", 10), cursor="hand2")
        label.pack(side=tk.LEFT)

        def toggle(_event=None):
            var.set(not var.get())

        def refresh(*_args):
            active = bool(var.get())
            box.configure(text="✓" if active else "", bg="#1261ff" if active else "#ffffff")

        for widget in (item, box, label):
            widget.bind("<Button-1>", toggle)
        var.trace_add("write", refresh)
        refresh()
        self.check_cards.append((item, box, label))

    def build_paths_page(self):
        page = self.create_page("paths")
        page.grid_columnconfigure(0, weight=1)
        path_card = self.card(page, 0, 0, "osu! 路径", height=230)
        path_card.grid_columnconfigure(1, weight=1)
        self.add_path_entry(path_card, 1, "stable", "stable_path")
        self.add_path_entry(path_card, 2, "lazer", "lazer_path")
        self.add_round_button(path_card, "检测 osu! 路径", self.detect_osu_paths, row=3, column=0, columnspan=3, padx=20, pady=(10, 20))

    def build_logs_page(self):
        page = self.create_page("logs")
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)
        log_card = self.card(page, 0, 0, "日志", height=580)
        log_card.grid_rowconfigure(1, weight=1)
        self.log_text = tk.Text(
            log_card,
            bg="#0f172a",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief=tk.FLAT,
            wrap=tk.WORD,
            font=("Consolas", 10),
            padx=14,
            pady=12,
        )
        self.log_scroll = ttk.Scrollbar(log_card, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=self.log_scroll.set)
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=(20, 0), pady=(0, 20))
        self.log_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 20), pady=(0, 20))

    def build_about_page(self):
        page = self.create_page("about")
        page.grid_columnconfigure(0, weight=1)
        about = self.card(page, 0, 0, "关于", height=180)
        ttk.Label(about, text="osu! Sayobot Helper", style="Metric.TLabel").grid(row=1, column=0, sticky="w", padx=22)
        ttk.Label(
            about,
            text="一个用于监听 osu! 谱面页面、自动下载并导入谱面的轻量工具。",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", padx=22, pady=(4, 20))

    def add_combo(self, parent, row, label, key, values):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=20, pady=9)
        var = tk.StringVar()
        self.vars[key] = var
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly").grid(
            row=row, column=1, columnspan=2, sticky="ew", padx=(0, 20), pady=9
        )

    def add_path_entry(self, parent, row, label, key, folder=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=20, pady=9)
        var = tk.StringVar()
        self.vars[key] = var
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=9, padx=(0, 8))
        command = (lambda: self.choose_folder(key)) if folder else (lambda: self.choose_file(key))
        self.add_round_button(parent, "浏览", command, row=row, column=2, padx=(0, 20), pady=9, sticky="e", width=114)

    def add_round_button(self, parent, text, command, row, column, columnspan=1, padx=0, pady=0, sticky="ew", width=None):
        button = RoundedButton(parent, text=text, command=command, bg="#e8f0ff", fg="#111827", active_bg="#dbeafe", height=38)
        if width:
            button.configure(width=width)
        button.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, padx=padx, pady=pady)
        return button

    def show_page(self, key):
        titles = {
            "status": "设备状态",
            "config": "参数配置",
            "paths": "osu! 路径",
            "logs": "日志",
            "about": "关于",
        }
        self.current_page = key
        self.page_title_var.set(titles[key])
        for page_key, page in self.pages.items():
            if page_key == key:
                page.tkraise()
        for page_key, button in self.nav_buttons.items():
            active = page_key == key
            button.set_active(active)

    def load_vars(self):
        self.loading_vars = True
        for key, var in self.vars.items():
            value = self.cfg.get(key, "")
            if isinstance(var, tk.BooleanVar):
                var.set(bool(value))
            else:
                var.set(self.value_to_label(key, str(value)))
        for key in self.option_cards:
            self.refresh_option_cards(key)
        self.update_status_tiles()
        self.loading_vars = False

    def bind_auto_save(self):
        for var in self.vars.values():
            var.trace_add("write", self.schedule_auto_save)
        for key in self.option_cards:
            self.vars[key].trace_add("write", lambda *_args, option_key=key: self.refresh_option_cards(option_key))

    def collect_config(self):
        updated = self.cfg.copy()
        for key, var in self.vars.items():
            if isinstance(var, tk.BooleanVar):
                updated[key] = bool(var.get())
            else:
                updated[key] = self.label_to_value(key, var.get().strip())
        return updated

    def schedule_auto_save(self, *_):
        if self.loading_vars:
            return
        self.save_state_var.set("正在自动保存...")
        self.update_status_tiles()
        if self.save_after_id:
            self.root.after_cancel(self.save_after_id)
        self.save_after_id = self.root.after(350, self.auto_save)

    def refresh_option_cards(self, key):
        selected = self.vars[key].get()
        for label, widgets in self.option_cards.get(key, []):
            active = label == selected
            bg = "#eef4ff" if active else "#ffffff"
            border = "#1261ff" if active else "#d8dee8"
            fg = "#1261ff" if active else "#cbd5e1"
            rounded = widgets[0]
            rounded.bg_color = bg
            rounded.border_color = border
            rounded.draw()
            for widget in widgets[1:]:
                widget.configure(bg=bg)
            widgets[2].configure(text="●" if active else "○", fg=fg)

    def auto_save(self):
        self.cfg = self.collect_config()
        save_config(self.cfg)
        self.save_state_var.set("配置已自动保存")
        print("配置已自动保存。")

    def update_status_tiles(self):
        if hasattr(self, "status_download_mode"):
            self.status_download_mode.set(self.vars.get("download_mode", tk.StringVar(value="")).get())
            self.status_download_method.set(self.vars.get("download_method", tk.StringVar(value="")).get())
            self.status_open_with.set(self.vars.get("open_with", tk.StringVar(value="")).get())

    def value_to_label(self, key, value):
        maps = {
            "download_mode": self.DOWNLOAD_MODE_LABELS,
            "download_method": self.DOWNLOAD_METHOD_LABELS,
            "open_with": self.OPEN_WITH_LABELS,
        }
        if key not in maps:
            return value
        for label, internal in maps[key].items():
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

    def detect_osu_paths(self):
        self.cfg = self.collect_config()
        self.cfg["stable_path"] = ""
        self.cfg["lazer_path"] = ""
        self.cfg = autofill_launcher_paths(self.cfg)
        save_config(self.cfg)
        self.load_vars()
        self.save_state_var.set("osu! 路径已检测并保存")
        print("osu! 路径检测完成。")

    def choose_file(self, key):
        path = filedialog.askopenfilename(title="选择启动器", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.vars[key].set(path)

    def choose_folder(self, key):
        path = filedialog.askdirectory(title="选择保存目录")
        if path:
            self.vars[key].set(path)

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
        self.status_var.set("监听中")
        self.status_badge.configure(bg="#e8f8ef", fg="#15803d")
        print("监听已启动。")

    def stop_monitor(self):
        self.stop_event.set()
        self.status_var.set("监听已停止")
        self.status_badge.configure(bg="#ffe8e8", fg="#dc2626")
        print("正在停止监听...")

    def begin_move(self, event):
        self.move_start_x = event.x_root
        self.move_start_y = event.y_root
        self.window_start_x = self.root.winfo_x()
        self.window_start_y = self.root.winfo_y()

    def do_move(self, event):
        dx = event.x_root - self.move_start_x
        dy = event.y_root - self.move_start_y
        self.root.geometry(f"+{self.window_start_x + dx}+{self.window_start_y + dy}")

    def minimize_window(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.after(50, self.restore_frameless)

    def restore_frameless(self):
        if self.root.state() != "iconic":
            self.root.overrideredirect(True)
            self.root.after(50, self.show_in_taskbar)
        else:
            self.root.after(100, self.restore_frameless)

    def show_in_taskbar(self):
        if ctypes is None:
            return
        try:
            hwnd = self.root.winfo_id()
            parent = ctypes.windll.user32.GetParent(hwnd)
            if parent:
                hwnd = parent

            get_window_long = ctypes.windll.user32.GetWindowLongW
            set_window_long = ctypes.windll.user32.SetWindowLongW
            ex_style = get_window_long(hwnd, -20)
            ex_style = (ex_style | 0x00040000) & ~0x00000080
            set_window_long(hwnd, -20, ex_style)

            self.root.withdraw()
            self.root.after(10, self.root.deiconify)
        except Exception:
            pass

    def drain_log_queue(self):
        while True:
            try:
                text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log_text(text)
        self.root.after(100, self.drain_log_queue)

    def append_log_text(self, text):
        if text.startswith("\r"):
            self.log_text.delete("end-1c linestart", "end-1c lineend")
            self.log_text.insert(tk.END, text[1:])
        else:
            self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def on_close(self):
        self.stop_monitor()
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        self.root.destroy()


def run_gui():
    setup_console_encoding()
    root = tk.Tk()
    ConfigApp(root)
    root.mainloop()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        setup_console_encoding()
        monitor_browser_url()
    else:
        run_gui()

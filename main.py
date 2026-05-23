# -*- coding: utf-8 -*-

import json
import os
import re
import sys
import time
import webbrowser

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


def load_config():
    default = {
        "search_template": "https://osu.sayobot.cn/?search={id}",
        "keep_original": False,
        "auto_download": False,
    }
    if not os.path.exists(CONFIG_PATH):
        return default
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {**default, **cfg}
    except Exception:
        return default


def is_url(string: str) -> bool:
    return string.startswith(("http://", "https://"))


def auto_download(set_id: str):
    try:
        print(f"尝试自动下载谱面 {set_id}...")
        download_url = f"https://txy1.sayobot.cn/beatmaps/download/{set_id}"
        print(f"打开下载链接: {download_url}")
        webbrowser.open_new_tab(download_url)
        print("已打开下载链接，请在浏览器中确认下载。")
        return True
    except Exception as e:
        print(f"打开下载链接时出错: {e}")
        return False


def extract_set_id(url: str) -> str | None:
    match = re.search(r"/beatmapsets/(\d+)(?:[/?#]|$)", url, re.I)
    return match.group(1) if match else None


def open_search(set_id: str, cfg: dict, source_window=None, source_edit=None):
    url = cfg["search_template"].format(id=set_id)
    if cfg.get("keep_original"):
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


def monitor_browser_url(loop_interval: float = 1.0):
    cfg = load_config()
    last_url = ""
    print("开始监听当前浏览器地址栏，检测 osu 谱面链接。按 Ctrl+C 退出。")

    try:
        while True:
            try:
                url, source_window, source_edit = get_active_browser_url()
            except Exception as err:
                print("检测浏览器地址栏时发生异常:", err)
                url, source_window, source_edit = None, None, None

            if url and url != last_url:
                last_url = url
                set_id = extract_set_id(url)
                if set_id:
                    print(f"检测到 set_id: {set_id}，正在打开 sayobot。")
                    open_search(set_id, cfg, source_window, source_edit)

                    if cfg.get("auto_download"):
                        print("尝试自动下载谱面...")
                        auto_download(set_id)
            time.sleep(loop_interval)
    except KeyboardInterrupt:
        print("已退出。")


if __name__ == "__main__":
    setup_console_encoding()
    monitor_browser_url()

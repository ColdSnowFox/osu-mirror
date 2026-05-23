# osu 谱面 ID 自动搜索工具

功能：检测当前活动浏览器窗口的地址栏 URL，自动识别 osu 谱面链接并提取 `set_id`，然后在 osu.sayobot.cn 上搜索并打开结果页面。

兼容浏览器：Chrome、Edge。

兼容系统：Windows。

## 依赖

- Python 3.8+
- `pywinauto`

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

## 使用

打开 `run.bat` 运行：

```cmd
run.bat
```

在浏览器中打开 osu 谱面链接，例如：

```text
https://osu.ppy.sh/beatmapsets/12345#osu/67890
```

然后让该浏览器窗口处于活动状态，脚本会自动读取地址栏，并打开 sayobot 搜索页面。

默认搜索模板为：

```text
https://osu.sayobot.cn/?search={id}
```

## 配置

可编辑 `config.json` 中的 `search_template` 调整打开的搜索 URL 模板，使用 `{id}` 占位符代表谱面 ID。

- `keep_original`: `true` 时在当前浏览器中新标签页打开 sayobot，并保留 osu 原页面；`false` 时直接在当前页面导航到 sayobot，替换 osu 页面。
- `auto_download`: `true` 时自动在当前检测到的浏览器中新标签页打开谱面下载链接；`false` 时不自动下载。

## 中文乱码

如果在 CMD 中看到中文乱码，请使用 `run.bat` 启动。它会先执行 `chcp 65001`，并启用 Python UTF-8 模式。

## 开源协议

本项目使用 MIT License 开源。

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

打开 `run.bat` 运行图形界面：

```cmd
run.bat
```

在浏览器中打开 osu 谱面链接，例如：

```text
https://osu.ppy.sh/beatmapsets/12345#osu/67890
```

然后让该浏览器窗口处于活动状态，脚本会自动读取地址栏，并打开 sayobot 搜索页面。

界面使用白色、淡蓝色和黑色为主色，可以直接修改设置；修改后会实时自动保存到 `config.json`。运行日志可在“日志”页面查看。

如果需要旧的命令行模式，可以运行：

```cmd
python main.py --cli
```

## 打包为 exe

如果要发给没有 Python 的用户，双击运行：

```cmd
build.bat
```

打包完成后，把 `dist` 文件夹里的 `osu-sayobot-helper.exe` 和 `config.json` 一起发给别人即可。打包后的 exe 默认不显示系统 CMD 窗口。

## 配置

可直接在图形界面中调整配置，也可以编辑 `config.json`。

- `keep_original`: `true` 时在当前浏览器中新标签页打开 sayobot，并保留 osu 原页面；`false` 时直接在当前页面导航到 sayobot，替换 osu 页面。
- `auto_download`: `true` 时检测到谱面后自动下载；`false` 时不自动下载。
- `download_mode`: 自动下载时使用的 sayobot 下载类型。界面中显示为“完整谱面”或“无视频谱面”。
- `download_method`: 界面中显示为“程序内下载”或“浏览器打开”。
- `download_dir`: `download_method` 为 `direct` 时的保存目录，默认为 `downloads`。
- `open_after_download`: `true` 时直接下载完成后自动打开 `.osz` 文件；`false` 时只保存文件。
- `open_with`: 自动打开方式。界面中显示为“系统默认”、“osu! stable”或“osu! lazer”。
- `stable_path`: osu! stable 启动器路径，留空时会自动检测常见安装位置并写回配置。
- `lazer_path`: osu! lazer 启动器路径，留空时会自动检测常见安装位置并写回配置。

## 中文乱码

如果在 CMD 中看到中文乱码，请使用 `run.bat` 启动。它会先执行 `chcp 65001`，并启用 Python UTF-8 模式。

## 开源协议

本项目使用 MIT License 开源。

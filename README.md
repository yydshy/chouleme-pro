# 抽了么 Pro（ChouLeMe Pro）

批量视频抽帧 + 智能去重的 Windows 桌面工具，基于 Python + CustomTkinter 打造，自带现代化暗色界面。

> 本仓库为 **免费开源版**，无需激活即可使用全部功能。

---

## ✨ 功能特性

- **批量视频抽帧**：一次导入多个视频，按「短视频 / 中等 / 长视频」自动分级，每个视频生成多条删帧后的版本
- **智能去重**：提供基础（时间偏移、MD5 改写）、微调（帧率、亮度/对比度、色调）、重度（镜像、RGB 偏移、蒙版）三档去重策略
- **硬件加速**：自动检测并优先使用 NVIDIA NVENC / Intel QSV / AMD AMF / Apple VideoToolbox，失败自动回退 CPU
- **并行处理**：可配置 1–4 个任务同时处理，提升效率
- **现代化 UI**：剪映风格暗色界面、实时日志、进度条、抽帧规则预览

---

## 🖥️ 环境要求

- Windows 10/11（程序调用了 Windows 专属 API，目前仅支持 Windows）
- Python 3.10+
- [FFmpeg](https://ffmpeg.org/)（`ffmpeg.exe` 与 `ffprobe.exe`）

---

## 📦 安装与运行

### 1. 克隆仓库

```bash
git clone https://github.com/<你的用户名>/chouleme-pro.git
cd chouleme-pro
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 准备 FFmpeg（二选一）

- **方式 A（推荐）**：把 `ffmpeg.exe` 和 `ffprobe.exe` 所在目录加入系统 `PATH`，程序会优先使用；
- **方式 B**：运行下载脚本，自动放到 `ffmpeg/` 目录：

```bash
python scripts/download_ffmpeg.py
```

> 也可以手动从 https://ffmpeg.org/download.html 下载 Windows 版，解压后将 `ffmpeg.exe`、`ffprobe.exe` 放入项目根目录的 `ffmpeg/` 文件夹。

### 4. 启动

```bash
python chouleme_pro.py
```

---

## 🛠️ 打包为独立 exe（可选）

如需分发给无 Python 环境的用户，可用 PyInstaller 打包：

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --add-data "ffmpeg;ffmpeg" chouleme_pro.py
```

打包后 `dist/chouleme_pro.exe` 即为免安装版本（需将 ffmpeg 一并携带）。

---

## 📁 项目结构

```
chouleme-pro/
├── chouleme_pro.py            # 主程序（单文件，全部逻辑）
├── requirements.txt           # Python 依赖
├── LICENSE                    # MIT 许可证
├── scripts/
│   └── download_ffmpeg.py     # FFmpeg 一键下载脚本
├── ffmpeg/                    # （不入库）放置 ffmpeg.exe / ffprobe.exe
└── .gitignore
```

> 注：`*.ini`、`*.json`、本地 `ffmpeg/` 二进制、`*.bak` 等均已通过 `.gitignore` 排除，不会上传你的个人配置与机器信息。

---

## ⚠️ 许可与合规

- 本项目以 [MIT License](LICENSE) 开源。
- FFmpeg 采用 LGPL/GPL 许可，若随程序分发请遵守其许可证条款（详见 https://www.ffmpeg.org/legal.html）。

---

## 📝 说明

- 配置文件（如 `video_sampler_pro_config.ini`）会在首次运行时自动生成，记录你的上次设置，已被 `.gitignore` 忽略。
- 本工具仅用于你拥有版权的视频素材处理，请遵守相关平台规则与法律法规。

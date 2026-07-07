"""
下载 FFmpeg / FFprobe 到项目 ffmpeg/ 目录（Windows 版）。

用法：
    python scripts/download_ffmpeg.py

下载来源：https://www.gyan.dev/ffmpeg/builds/ （ffmpeg-release-essentials.zip）
解压后仅取 ffmpeg.exe 与 ffprobe.exe 放入 ./ffmpeg/。
"""
import io
import os
import sys
import urllib.request
import zipfile

FFMPEG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ffmpeg")
URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def main():
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    target_ffmpeg = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    target_ffprobe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
    if os.path.exists(target_ffmpeg) and os.path.exists(target_ffprobe):
        print("ffmpeg/ffprobe 已存在，跳过下载。")
        return

    print(f"正在下载 FFmpeg: {URL}")
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
    except Exception as e:
        print(f"下载失败：{e}\n请手动从 https://ffmpeg.org/download.html 下载并解压 ffmpeg.exe / ffprobe.exe 到 ffmpeg/ 目录。")
        sys.exit(1)

    print("下载完成，正在解压...")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            base = os.path.basename(name)
            if base in ("ffmpeg.exe", "ffprobe.exe"):
                with open(os.path.join(FFMPEG_DIR, base), "wb") as f:
                    f.write(z.read(name))
                print(f"  已提取 {base}")

    if os.path.exists(target_ffmpeg) and os.path.exists(target_ffprobe):
        print("完成！ffmpeg/ 目录已就绪。")
    else:
        print("解压后未找到 ffmpeg.exe / ffprobe.exe，请检查压缩包结构。")


if __name__ == "__main__":
    main()

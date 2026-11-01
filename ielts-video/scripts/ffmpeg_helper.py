#!/usr/bin/env python3
"""
ffmpeg 定位 helper：优先用 imageio-ffmpeg 自带的静态 ffmpeg（带 libass），
否则回退到系统 PATH 里的 ffmpeg。

所有脚本 import 本模块后，用 get_ffmpeg() / get_ffprobe() 获取可执行路径。
"""

import os
import shutil
import subprocess


def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    # 最后兜底：conda
    for cand in [
        "/Users/sofia/libo/local/miniconda3/bin/ffmpeg",
    ]:
        if os.path.exists(cand):
            return cand
    raise RuntimeError("找不到 ffmpeg，请安装 imageio-ffmpeg 或系统 ffmpeg")


def get_ffprobe():
    # imageio-ffmpeg 不提供 ffprobe，尝试从系统/conda 找
    for p in [
        shutil.which("ffprobe"),
        "/Users/sofia/libo/local/miniconda3/bin/ffprobe",
    ]:
        if p and os.path.exists(p):
            return p
    # 退而求其次：用 ffmpeg 读时长（-i 会输出 duration）
    return None


def get_duration(media):
    """获取媒体时长（秒），优先 ffprobe，回退 ffmpeg 解析。"""
    ffprobe = get_ffprobe()
    if ffprobe:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", media],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    # 回退：ffmpeg -i 解析 stderr 里的 Duration
    ffmpeg = get_ffmpeg()
    r = subprocess.run([ffmpeg, "-i", media], capture_output=True, text=True)
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if m:
        h, mi, s = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(s)
    raise RuntimeError(f"无法读取媒体时长：{media}")


if __name__ == "__main__":
    print("ffmpeg:", get_ffmpeg())
    print("ffprobe:", get_ffprobe())

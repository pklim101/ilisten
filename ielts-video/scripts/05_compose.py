#!/usr/bin/env python3
"""
步骤5：合成出片（背景图 + 三态字幕，无片头片尾）

把重组后的音频 + 三态 ASS 字幕烧录到背景画面上，输出最终 MP4。

背景：使用清新自然的雅思主题背景图（assets/background.png）。
为保证字幕在浅色背景上清晰可读，字幕加半透明描边/底色。

输出：out/<name>.mp4（1920x1080, 16:9, H.264 + AAC）
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ffmpeg_helper import get_ffmpeg, get_duration

FFMPEG = get_ffmpeg()

AUDIO = os.environ.get("AUDIO_REBUILT", "ielts-video/data/rebuilt_audio.mp3")
ASS = os.environ.get("ASS", "ielts-video/data/subtitles.ass")
BACKGROUND = os.environ.get("BACKGROUND", "ielts-video/assets/background.png")
OUT = os.environ.get("OUT", "ielts-video/out/c19_test1_part2.mp4")

VIDEO_W = int(os.environ.get("VIDEO_W", "1920"))
VIDEO_H = int(os.environ.get("VIDEO_H", "1080"))
FPS = 30

# 背景压暗系数（0~1，越小越暗）。浅色背景需轻微压暗以突出白色字幕。
BG_DARKEN = float(os.environ.get("BG_DARKEN", "0.82"))


def main():
    if not os.path.exists(AUDIO):
        print(f"[错误] 找不到重组音频 {AUDIO}，请先运行 03_rebuild_audio.py")
        sys.exit(1)
    if not os.path.exists(ASS):
        print(f"[错误] 找不到字幕 {ASS}，请先运行 04_subtitles.py")
        sys.exit(1)

    duration = get_duration(AUDIO)
    print(f"音频时长 {duration:.1f}s")

    # 背景输入
    if BACKGROUND and os.path.exists(BACKGROUND):
        bg_input = ["-loop", "1", "-i", BACKGROUND]
        # 背景铺满整个画面：先放大到覆盖目标尺寸，再居中裁切。
        # 避免 force_original_aspect_ratio=decrease + pad 产生左右黑边，
        # 导致字幕看起来「超出背景图范围」。
        bg_filter = (
            f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H},setsar=1"
        )
    else:
        bg_input = ["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={VIDEO_W}x{VIDEO_H}:r={FPS}"]
        bg_filter = "null"

    ass_filter = f"ass={ASS}"

    # 单视频流：背景 -> 字幕（无片头片尾）
    vf = f"[0:v]{bg_filter}[bg];[bg]{ass_filter}[v]"

    cmd = [
        FFMPEG, "-y", "-loglevel", "warning",
        *bg_input,
        "-i", AUDIO,
        "-filter_complex", vf,
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.3f}",
        OUT,
    ]

    print("合成中...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[错误] 合成失败：")
        print(r.stderr)
        sys.exit(1)

    size = os.path.getsize(OUT) / (1024 * 1024)
    print(f"[完成] 成片 {OUT}（{size:.1f} MB）")


if __name__ == "__main__":
    main()

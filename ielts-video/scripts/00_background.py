#!/usr/bin/env python3
"""
步骤0（可选）：生成柔和的渐变背景图。

用纯 Python 生成一张 1920x1080 的竖向渐变背景（深蓝 → 深紫），
作为成片默认背景，避免依赖外部图片素材。也支持生成片头/片尾纯色卡片。

输出：
- ielts-video/assets/background.png
- ielts-video/assets/intro_bg.png（片头卡片底色，略深）
"""
import os
from pathlib import Path

W = int(os.environ.get("VIDEO_W", "1920"))
H = int(os.environ.get("VIDEO_H", "1080"))
OUT = os.environ.get("BG_OUT", "ielts-video/assets/background.png")
INTRO_OUT = os.environ.get("INTRO_OUT", "ielts-video/assets/intro_bg.png")

# 渐变配色（自上而下）：深蓝 -> 深靛 -> 深紫
TOP = (26, 26, 46)      # 0x1a1a2e
MID = (40, 40, 80)      # 0x282850
BOT = (20, 20, 40)      # 0x141428


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_gradient(path, top=TOP, mid=MID, bot=BOT):
    from PIL import Image
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        if t < 0.5:
            c = lerp(top, mid, t * 2)
        else:
            c = lerp(mid, bot, (t - 0.5) * 2)
        for x in range(W):
            px[x, y] = c
    img.save(path)
    return path


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("[提示] 未安装 Pillow，改用纯色背景（ffmpeg color 源）")
        print("       如需渐变背景，请 pip install pillow")
        return
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    make_gradient(OUT)
    print(f"[完成] 背景图 {OUT}")
    make_gradient(INTRO_OUT, top=(12, 12, 28), mid=(20, 20, 44), bot=(10, 10, 24))
    print(f"[完成] 片头底图 {INTRO_OUT}")


if __name__ == "__main__":
    main()

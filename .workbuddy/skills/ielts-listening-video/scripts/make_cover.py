#!/usr/bin/env python3
"""按 docs/05 模板生成 B站封面 PNG（1146×717），背景沿用成片一直用的浅色风景图。

2026-11-08 用户定稿：**配色用 A 学术蓝，背景用 `ielts-video/assets/background.png`**
（清新浅色风景，1536×1024，已手工去水印；docs/03 标注「务必备份，禁止覆盖」）。
浅色背景与 A 方案的白字天然冲突，因此加一层**从左到右的深蓝渐变蒙版**托住文字区，
右侧风景完整露出；主标题/副标题再加同色系深蓝描边，保证缩略图尺寸下依然可读。

两种入参（文案唯一真值源是 make_publish_kit.cover_spec）：
  --stem c19_test2_part2     出片阶段独立调用，不需要投稿物料
  --kit /tmp/ielts_publish_<stem>.json   投稿阶段调用，读物料里的 cover 字段

用法：
  make_cover.py --stem c19_test2_part2 --out ielts-video/out/c19_test2_part2_cover.png
  make_cover.py --kit /tmp/ielts_publish_c19_test2_part2.json --out <abs.png>
  make_cover.py --stem ... --out ... --flat       # 回退：纯色渐变（调试用，非正式）
  make_cover.py --stem ... --out ... --background assets/backgrounds/bg_sky_academic_blue.png --no-mask

配色真值源：assets/cover_palettes.json（A 学术蓝 / B 剑桥红），缺失或损坏时回退内置值；
素材库底板由 make_asset_plates.py 生成，用底板当背景时必须加 --no-mask 防止二次叠蒙版。

版式铁律：
  - 一个封面只传递一个信息点，全系列同模板（视觉锤）
  - 关键文字避开右下角时长标签遮挡区
  - 中文用系统字体渲染，绝不靠 AI 生图写中文（易错字）
  - **绝不写回 assets/background.png**，本脚本只读背景图
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1146, 717  # B站封面标准尺寸（docs/05 第二部分）
DEFAULT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BACKGROUND = DEFAULT_ROOT / "ielts-video" / "assets" / "background.png"

# 本机可用中文字体，按优先级回退
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

BUILTIN_PALETTES = {
    "blue": {                          # A 学术蓝（用户 2026-11-08 定稿）
        "name": "学术蓝 A",
        "bg_top": (27, 42, 74),       # #1B2A4A 深海军蓝，用作蒙版与描边
        "bg_bottom": (9, 17, 34),
        "glow": (58, 104, 168),
        "title": (255, 255, 255),
        "accent": (255, 215, 0),      # #FFD700
        "sub": (214, 228, 248),
        "bar_bg": (27, 42, 74),
        "bar_text": (240, 246, 255),
        "badge_bg": (255, 215, 0),
        "badge_text": (20, 34, 60),
        "stroke": (12, 24, 48),
    },
    "red": {                           # B 剑桥红（A/B 对照备选，默认不用）
        "name": "剑桥红 B",
        "bg_top": (139, 26, 26),
        "bg_bottom": (58, 10, 14),
        "glow": (190, 62, 46),
        "title": (255, 248, 220),
        "accent": (255, 215, 0),
        "sub": (250, 226, 210),
        "bar_bg": (104, 26, 28),
        "bar_text": (255, 244, 226),
        "badge_bg": (255, 248, 220),
        "badge_text": (110, 20, 20),
        "stroke": (58, 10, 14),
    },
}

DEFAULT_PALETTES_JSON = DEFAULT_ROOT / "ielts-video" / "assets" / "cover_palettes.json"


def load_palettes(path: Path | None = None) -> dict:
    """配色真值源优先读 assets/cover_palettes.json；缺失/损坏时回退内置值。

    改配色只改色卡 JSON，不要改代码；改完跑 make_asset_plates.py 重出底板。
    """
    p = path or DEFAULT_PALETTES_JSON
    if not p.is_file():
        return dict(BUILTIN_PALETTES)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        required = ("bg_top", "bg_bottom", "glow", "title", "accent", "sub",
                    "bar_bg", "bar_text", "badge_bg", "badge_text", "stroke")
        out = {}
        for key in ("blue", "red"):
            raw = data["palettes"][key]
            c = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()}
            if not all(k in c for k in required):
                raise ValueError(f"配色 {key} 缺少必要色键")
            out[key] = c
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 配色色卡读取失败，回退内置：{e}", file=sys.stderr)
        return dict(BUILTIN_PALETTES)


PALETTES = load_palettes()


def cover_spec_from_stem(stem: str) -> dict:
    """出片阶段没有物料 JSON 时，复用投稿物料的封面文案公式（唯一真值源）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from make_publish_kit import cover_spec  # noqa: PLC0415
    return cover_spec(stem)


def pick_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            return ImageFont.truetype(str(p), size, index=0)
        except (OSError, ValueError):
            continue
    raise RuntimeError("找不到可用的中文字体，请检查 FONT_CANDIDATES")


def gradient_bg(c: dict) -> Image.Image:
    """--flat 回退模式：旧的纯色渐变 + 右上柔光晕。"""
    img = Image.new("RGB", (W, H))
    px = img.load()
    top, bot = c["bg_top"], c["bg_bottom"]
    for y in range(H):
        t = y / (H - 1)
        row = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((920 - 330, 190 - 330, 920 + 330, 190 + 330),
                                 fill=(*c["glow"], 70))
    return Image.alpha_composite(img.convert("RGBA"),
                                 glow.filter(ImageFilter.GaussianBlur(90)))


def photo_bg(c: dict, bg_path: Path, mask: bool = True) -> Image.Image:
    """成片同款背景图铺满（increase + crop），叠左侧深蓝渐变蒙版托住白字。

    与 05_compose.py 的 `force_original_aspect_ratio=increase + crop` 同一套逻辑，
    保证封面与视频内画面的背景同源、无黑边。
    """
    src = Image.open(bg_path).convert("RGB")
    sw, sh = src.size
    scale = max(W / sw, H / sh)
    src = src.resize((max(W, int(sw * scale)), max(H, int(sh * scale))), Image.LANCZOS)
    nw, nh = src.size
    x, y = (nw - W) // 2, (nh - H) // 2
    base = src.crop((x, y, x + W, y + H)).convert("RGBA")
    if not mask:
        # 素材库里已合成好的底板：只做等比铺满裁切，绝不二次叠加蒙版
        return base

    # 极轻冷调统一（不掩盖风景），再用横向渐变蒙版保证白字对比度
    base = Image.alpha_composite(base, Image.new("RGBA", (W, H), (8, 20, 44, 20)))
    row = Image.new("L", (W, 1))
    rp = row.load()
    for xx in range(W):
        t = max(0.0, min(1.0, (940 - xx) / 940))
        rp[xx, 0] = int(226 * (t ** 1.05))
    mask = row.resize((W, H))
    overlay = Image.new("RGBA", (W, H), (*c["bg_top"], 0))
    overlay.putalpha(mask)
    return Image.alpha_composite(base, overlay)


def wave_decoration(img: Image.Image, c: dict) -> None:
    """仅 --flat 模式使用：右侧声波竖条。照片模式让风景呼吸，不叠加装饰。"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(22):
        x = 886 + i * 10
        amp = 18 + 62 * abs(math.sin(i * 0.42))
        alpha = int(30 + 46 * abs(math.sin(i * 0.31)))
        d.line((x, 300 - amp, x, 300 + amp), fill=(*c["accent"], alpha), width=4)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1)))


def render(kit: dict, key: str, bg_path: Path | None, flat: bool,
           no_mask: bool = False) -> Image.Image:
    c = PALETTES[key]
    cover = kit.get("cover", {})
    main_text = cover.get("main_text") or kit.get("title", "")
    hook = cover.get("sub_text", "")
    flow = cover.get("flow_bar", "挖空回忆 → 考点高亮 → 双语理解")

    if flat or bg_path is None or not bg_path.is_file():
        img = gradient_bg(c)
        wave_decoration(img, c)
        photo = False
    else:
        img = photo_bg(c, bg_path, mask=not no_mask)
        photo = True
    d = ImageDraw.Draw(img, "RGBA")

    f_badge = pick_font(25)
    f_title = pick_font(92)
    f_sub = pick_font(46)
    f_hook = pick_font(29)
    f_flow = pick_font(29)
    stroke = (*c["stroke"], 255)

    # 左上角小标：系列识别
    bx1, by1, bx2, by2 = 64, 54, 384, 106
    d.rounded_rectangle((bx1, by1, bx2, by2), radius=10, fill=(*c["badge_bg"], 255))
    d.text((bx1 + 22, by1 + 11), "雅思听力 · 三遍精听", font=f_badge,
           fill=(*c["badge_text"], 255))

    # 主标题：照片模式下加深蓝描边，右端即使落到浅色天空也清晰
    sw = 3 if photo else 1
    d.text((62, 182), main_text, font=f_title, fill=(*c["title"], 255),
           stroke_width=sw, stroke_fill=stroke if photo else (*c["title"], 255))
    bbox = d.textbbox((62, 182), main_text, font=f_title)

    # 荧光黄分隔线
    line_y = bbox[3] + 28
    d.rounded_rectangle((64, line_y, 470, line_y + 6), radius=3,
                        fill=(*c["accent"], 255))

    # 副标题 + 本篇钩子
    d.text((64, line_y + 34), "逐句精听 × 3 遍", font=f_sub,
           fill=(*c["accent"], 255),
           stroke_width=2 if photo else 0, stroke_fill=stroke)
    d.text((66, line_y + 100), hook, font=f_hook, fill=(*c["sub"], 255),
           stroke_width=2 if photo else 0, stroke_fill=stroke)

    # 底部流程条：右端留出 260px，避开时长标签；深蓝底不依赖背景明暗
    bar = (64, 566, 886, 646)
    d.rounded_rectangle(bar, radius=16, fill=(*c["bar_bg"], 235),
                        outline=(*c["accent"], 90), width=1)
    parts = [p.strip() for p in flow.replace("→", "|").split("|") if p.strip()]
    if len(parts) == 3:
        arrow = "→"
        gap = 46
        widths = [d.textlength(t, font=f_flow) for t in parts]
        arrow_w = d.textlength(arrow, font=f_flow)
        total = sum(widths) + arrow_w * 2 + gap * 4
        x = bar[0] + ((bar[2] - bar[0]) - total) / 2
        for i, text in enumerate(parts):
            if i:
                d.text((x, 587), arrow, font=f_flow, fill=(*c["accent"], 255))
                x += arrow_w + gap
            d.text((x, 587), text, font=f_flow, fill=(*c["bar_text"], 255))
            x += widths[i] + gap
    else:
        d.text((96, 587), flow, font=f_flow, fill=(*c["bar_text"], 255))

    return img.convert("RGB")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 B站精听系列封面 PNG（docs/05 模板 + 成片同款背景）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--stem", help="如 c19_test2_part2，出片阶段独立调用")
    src.add_argument("--kit", help="/tmp/ielts_publish_<stem>.json，投稿阶段调用")
    ap.add_argument("--out", required=True, help="输出 PNG 绝对路径")
    ap.add_argument("--background", default=str(DEFAULT_BACKGROUND),
                    help="背景图（默认 assets/background.png，只读绝不覆盖）")
    ap.add_argument("--palette", choices=["blue", "red"], default="blue",
                    help="默认 blue = 用户定稿的 A 学术蓝")
    ap.add_argument("--flat", action="store_true",
                    help="回退到纯色渐变（调试/对照用，非正式封面）")
    ap.add_argument("--no-mask", action="store_true",
                    help="--background 传入素材库里已合成好的底板时启用：只等比裁切铺满，不再叠加渐变蒙版")
    args = ap.parse_args()

    if args.kit:
        kit_path = Path(args.kit)
        if not kit_path.is_file():
            print(f"[错误] 物料不存在：{kit_path}", file=sys.stderr)
            return 3
        kit = json.loads(kit_path.read_text(encoding="utf-8"))
    else:
        stem = args.stem.strip().lower()
        try:
            spec = cover_spec_from_stem(stem)
        except Exception as e:                      # noqa: BLE001
            print(f"[错误] stem 不合规或文案生成失败：{e}", file=sys.stderr)
            return 3
        kit = {"stem": stem, "cover": spec}

    bg_path = Path(args.background).resolve()
    if not args.flat and not bg_path.is_file():
        print(f"[错误] 背景图不存在：{bg_path}（绝不自动替换/生成背景）", file=sys.stderr)
        return 3

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    img = render(kit, args.palette, bg_path, args.flat, no_mask=args.no_mask)
    img.save(out, "PNG", optimize=True)
    mode = "flat" if args.flat else f"photo:{bg_path.name}{' / no-mask' if args.no_mask else ''}"
    print(f"{PALETTES[args.palette]['name']} / {mode}: {out}  {W}x{H}  {out.stat().st_size // 1024}KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

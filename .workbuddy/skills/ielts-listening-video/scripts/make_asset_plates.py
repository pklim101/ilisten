#!/usr/bin/env python3
"""归档 / 重建封面与背景素材库（幂等；绝不写回 assets/background.png）。

复用 make_cover.py 的同一套渲染函数，保证素材库底板与正式封面版式不脱节。
配色真值源是 assets/cover_palettes.json；改完色卡重跑本脚本即可重出全部底板。

用法：
  make_asset_plates.py [--root /abs/repo] [--stem c19_test2_part2] [--force]

产出（均在 ielts-video/assets/ 内）：
  backgrounds/bg_sky_original.png          蓝天白云原图副本（与 background.png 同 md5）
  backgrounds/bg_sky_academic_blue.png    风景 + 左侧深蓝渐变蒙版（2026-11-08 定稿底板）
  backgrounds/bg_sky_cambridge_red.png    风景 + 深红渐变蒙版（B 对照备用）
  backgrounds/bg_academic_blue_flat.png   A 学术蓝纯色渐变底板（无文字）
  backgrounds/bg_cambridge_red_flat.png   B 剑桥红纯色渐变底板（无文字）
  covers/sample_*.png                      三张含文字版式样张（保真复制 out/，缺失时现场渲染）

安全边界：
  - 所有写入目标必须落在 assets/backgrounds/ 或 assets/covers/ 内；
  - 目标路径不得等于 assets/background.png；
  - 默认跳过已存在文件，只有 --force 才覆盖。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_cover import (  # noqa: E402
    DEFAULT_ROOT,
    cover_spec_from_stem,
    gradient_bg,
    photo_bg,
    render,
)

SAMPLES = [
    # (out/ 里的原始样张, 归档名, 配色, 是否纯色渐变)
    ("c19_test2_part2_cover_blue.png", "sample_c19_test2_part2_academic_blue_flat.png", "blue", True),
    ("c19_test2_part2_cover_red.png", "sample_c19_test2_part2_cambridge_red_flat.png", "red", True),
    ("c19_test2_part2_cover.png", "sample_c19_test2_part2_final_photo_blue.png", "blue", False),
]

PALETTE_SLUG = {"blue": "academic_blue", "red": "cambridge_red"}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_palette(json_path: Path, key: str) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    raw = data["palettes"][key]
    return {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()}


def guard(path: Path, assets: Path, bg: Path) -> None:
    p = path.resolve()
    if p == bg.resolve():
        raise RuntimeError(f"拒绝写入生产背景图：{p}")
    if not (p.is_relative_to((assets / "backgrounds").resolve())
            or p.is_relative_to((assets / "covers").resolve())):
        raise RuntimeError(f"拒绝写入素材库之外的路径：{p}")


def write_png(img, path: Path, assets: Path, bg: Path, force: bool) -> None:
    guard(path, assets, bg)
    if path.is_file() and not force:
        print(f"  skip  {path.relative_to(assets)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "PNG", optimize=True)
    print(f"  write {path.relative_to(assets)}  {path.stat().st_size // 1024}KB")


def main() -> int:
    ap = argparse.ArgumentParser(description="归档 / 重建 B站封面与背景素材库")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="仓库根目录绝对路径")
    ap.add_argument("--stem", default="c19_test2_part2", help="现场渲染样张时用的 stem")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的素材（默认跳过）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    assets = root / "ielts-video" / "assets"
    bg = assets / "background.png"
    out = root / "ielts-video" / "out"
    palette_json = assets / "cover_palettes.json"
    bgs, covs = assets / "backgrounds", assets / "covers"

    if not bg.is_file():
        print(f"[错误] 生产背景图缺失：{bg}", file=sys.stderr)
        return 3
    if not palette_json.is_file():
        print(f"[错误] 配色色卡缺失：{palette_json}", file=sys.stderr)
        return 3
    bgs.mkdir(parents=True, exist_ok=True)
    covs.mkdir(parents=True, exist_ok=True)

    print(f"素材库：{assets}")
    print("[1/4] 蓝天白云原图归档副本")
    original = bgs / "bg_sky_original.png"
    guard(original, assets, bg)
    if not original.is_file() or args.force:
        shutil.copy2(bg, original)
        print(f"  write {original.relative_to(assets)}  {original.stat().st_size // 1024}KB")
    else:
        print(f"  skip  {original.relative_to(assets)}")
    h1, h2 = md5(bg), md5(original)
    print(f"  md5 校验：{'一致' if h1 == h2 else '不一致！'}  {h1}")
    if h1 != h2:
        print("[错误] 归档副本与生产背景图不一致，请人工检查", file=sys.stderr)
        return 4

    print("[2/4] A 学术蓝 / B 剑桥红纯色渐变底板")
    for key, slug in PALETTE_SLUG.items():
        c = load_palette(palette_json, key)
        write_png(gradient_bg(c), bgs / f"bg_{slug}_flat.png", assets, bg, args.force)

    print("[3/4] 风景 + 渐变蒙版底板（与正式封面同源）")
    for key, slug in PALETTE_SLUG.items():
        c = load_palette(palette_json, key)
        write_png(photo_bg(c, bg), bgs / f"bg_sky_{slug}.png", assets, bg, args.force)

    print("[4/4] 含文字版式样张")
    try:
        kit = {"stem": args.stem, "cover": cover_spec_from_stem(args.stem)}
    except Exception as e:  # noqa: BLE001
        kit = None
        print(f"  [警告] 文案生成失败，缺失样张无法现场补渲染：{e}", file=sys.stderr)
    for src_name, dst_name, key, flat in SAMPLES:
        dst = covs / dst_name
        guard(dst, assets, bg)
        src = out / src_name
        if src.is_file() and (not dst.is_file() or args.force):
            shutil.copy2(src, dst)
            print(f"  write {dst.relative_to(assets)}  <- out/{src_name}  {dst.stat().st_size // 1024}KB")
        elif not dst.is_file() and kit is not None:
            write_png(render(kit, key, bg, flat), dst, assets, bg, True)
        elif not dst.is_file():
            print(f"  [警告] 跳过 {dst_name}（out/ 无原件且无法现场渲染）", file=sys.stderr)
        else:
            print(f"  skip  {dst.relative_to(assets)}")

    plates = sorted(bgs.glob("*.png"))
    samples = sorted(covs.glob("*.png"))
    print(f"\n完成：背景底板 {len(plates)} 张，版式样张 {len(samples)} 张；"
          f"配色真值源 {palette_json.name}")
    print("提示：backgrounds/ 是给人工设计工具用的无文字底板；传给 make_cover.py --background"
          " 时请加 --no-mask，避免重复叠加蒙版。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

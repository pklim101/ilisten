#!/usr/bin/env python3
"""按 timeline.json 选 3 个时间点抽帧，供对话中的 agent 用 ViewImage 做视觉确认。

抽帧只写 /tmp/ielts_qa_<stem>_<tag>.png，绝不落仓库、绝不写进 resource/ 或 out/。
规则见 references/03-frames-and-viewimage.md。

用法：
  qa_frames.py --root <项目根> --stem c19_test2_part1 [--outdir /tmp] [--with-highlight]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[4]
GAP_SEC = 4.0

QUERIES = {
    "1_blank": ("这是一张雅思听力精听视频的抽帧。请只回答看到的事实：1) 画面上是否有英文字幕且含下划线填空；"
                "2) 下划线是否与相邻单词或标点重叠；3) 字幕是否垂直居中、左右是否留有边距（不贴边、不被裁切）；"
                "4) 背景是否铺满整幅画面、有没有黑边。"),
    "2_bilingual": ("这是一张雅思听力精听视频的抽帧。请只回答看到的事实：画面上是否同时出现一行英文原文和一行中文翻译，"
                    "两行是否都在画面内、居中、不重叠、不被裁切。"),
    "3_instruction": ("这是一张雅思听力精听视频读题阶段的抽帧。请只回答看到的事实：1) 画面上是否显示一行完整的英文字幕"
                      "（题目说明文字），而不是空白背景；2) 英文字幕里有没有下划线填空或橙色高亮词（应都没有）；"
                      "3) 字幕是否垂直居中、左右留有边距、不贴边不被裁切；4) 背景是否铺满整幅画面无黑边。"
                      "若画面完全无字幕，请明确指出。"),
    "4_highlight": ("这是一张雅思听力精听视频的抽帧。请只回答看到的事实：画面上的英文字幕里是否有若干词被标成橙色高亮，"
                    "高亮是否清晰、字幕是否居中不越界。"),
}


def t_in(e: dict) -> float:
    s, en = float(e["start"]), float(e["end"])
    return s + 2.0 if (en - s) > 4.5 else (s + en) / 2


def median_by_duration(events: list[dict]) -> dict | None:
    if not events:
        return None
    return sorted(events, key=lambda e: float(e["end"]) - float(e["start"]))[len(events) // 2]


def uncovered_gaps(events: list[dict], total: float) -> list[tuple[float, float]]:
    gaps, cur = [], 0.0
    for e in sorted(events, key=lambda x: x["start"]):
        s, en = float(e["start"]), float(e["end"])
        if s - cur >= GAP_SEC:
            gaps.append((cur, s))
        cur = max(cur, en)
    if total - cur >= GAP_SEC:
        gaps.append((cur, total))
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description="抽取 QA 帧供 ViewImage 确认")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--stem", required=True)
    ap.add_argument("--outdir", default="/tmp")
    ap.add_argument("--with-highlight", action="store_true", help="追加第 4 帧（橙色高亮态）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    stem = args.stem
    tl_path = root / "ielts-video" / "data" / "timeline.json"
    mp4 = root / "ielts-video" / "out" / f"{stem}.mp4"
    outdir = Path(args.outdir).resolve()

    if not tl_path.is_file() or not mp4.is_file():
        print(f"[错误] 缺少 {tl_path} 或 {mp4}（是否刚跑完本篇？data/ 会被下一篇覆盖）", file=sys.stderr)
        return 1

    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    events = tl.get("events", [])
    repeats = int(tl.get("repeats", 3))
    total = float(tl.get("total_duration", 0.0))
    if not events:
        print("[错误] timeline.json 无事件", file=sys.stderr)
        return 1

    frames: list[tuple[str, float, str]] = []

    # 优先从成片中段（25%~75%）选帧，避免总抽到开头/结尾的特殊句；中段无合适事件再退回全量
    def in_middle(e: dict) -> bool:
        return total * 0.25 < float(e["start"]) < total * 0.75

    all_blank = [e for e in events if e.get("repeat") == 0 and float(e["end"]) - float(e["start"]) >= 4.0]
    mid_blank = [e for e in all_blank if in_middle(e)]
    blank_pool = mid_blank or all_blank or [e for e in events if e.get("repeat") == 0]
    blank = median_by_duration(blank_pool)
    if blank:
        frames.append(("1_blank", t_in(blank), f"sentence_index={blank.get('sentence_index')} 挖空态"))

    bil_pool = [e for e in events if e.get("repeat") == repeats - 1]
    bil = None
    if blank:
        bil = next((e for e in bil_pool if e.get("sentence_index") == blank.get("sentence_index")), None)
    bil = bil or median_by_duration(bil_pool)
    if bil:
        frames.append(("2_bilingual", t_in(bil), f"sentence_index={bil.get('sentence_index')} 双语态"))

    # 2026-11-17：读题段现在有完整英文字幕（instruction_events），第 3 帧改为验证它。
    # 取时长最长的读题段中点（字幕最可能完整显示），无该字段的旧留档降级为原「无字幕间隙」检查。
    instr = tl.get("instruction_events", [])
    if instr:
        ins = max(instr, key=lambda e: float(e["end"]) - float(e["start"]))
        it = (float(ins["start"]) + float(ins["end"])) / 2
        frames.append(("3_instruction", it,
                       f"读题段字幕 {float(ins['start']):.1f}~{float(ins['end']):.1f}s（共 {len(instr)} 段）"))
    else:
        gaps = uncovered_gaps(events, total)
        if gaps:
            lo, hi = max(gaps, key=lambda g: g[1] - g[0])
            frames.append(("3_instruction", (lo + hi) / 2,
                           f"[旧版]说明段间隙 {lo:.1f}~{hi:.1f}s（共 {len(gaps)} 段）"))
        else:
            print("[WARN] 未找到读题段也未找到 >=4.0s 间隙，跳过说明段帧", file=sys.stderr)

    if args.with_highlight:
        hl_pool = [e for e in events if e.get("repeat") == 1 and e.get("highlights")]
        hl = median_by_duration(hl_pool)
        if hl:
            frames.append(("4_highlight", t_in(hl), f"sentence_index={hl.get('sentence_index')} 高亮态"))

    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        print(f"[错误] 无法定位 ffmpeg：{e}", file=sys.stderr)
        return 1

    outdir.mkdir(parents=True, exist_ok=True)
    print(f"成片: {mp4}")
    print(f"抽帧目录: {outdir}（仅 /tmp，不落仓库）\n")
    results = []
    for tag, t, note in frames:
        png = outdir / f"ielts_qa_{stem}_{tag}.png"
        r = subprocess.run([ff, "-y", "-ss", f"{t:.2f}", "-i", str(mp4),
                            "-frames:v", "1", str(png), "-loglevel", "error"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not png.is_file():
            print(f"[错误] 抽帧失败 {tag} @ {t:.2f}s: {r.stderr[-300:]}", file=sys.stderr)
            return 1
        results.append((tag, t, png, note))
        print(f"[{tag}]  t={t:8.2f}s  {note}")
        print(f"   路径: {png}")
        print(f"   ViewImage query: {QUERIES[tag]}\n")

    print("—" * 60)
    print("由对话中的 agent 对以上每个路径各调用一次 ViewImage（query 自包含）。")
    print("任一帧不符 = BLOCK：不得交付、不得开始下一篇，按 references/04 处置。")
    print("3 帧全部通过后，再进入下一篇或宣布交付。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

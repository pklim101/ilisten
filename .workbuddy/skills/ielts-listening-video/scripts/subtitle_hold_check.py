#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""句尾字幕停留验收（v2.9.9，17 项 QA + tail_check 之外的第三道独立验收）。

背景：2026-11-17 用户验收发现「每句话末尾还有半个音节没读完时字幕就消失了」。
实测排除音频丢音（tail_check 已 PASS、词级 end 全 <= play_end），真因是字幕 end
精确取音频右边界、尾音渐弱最后一刻被硬切，与「停顿期间字幕保持显示」的设计注释脱节。
04_subtitles.py 的 SUBTITLE_HOLD_SEC 让字幕在音频边界后多停留（默认 0.8s），本脚本独立复验：
  1. 每条 Dialogue 的 end >= 对应音频边界（确有停留），且中位停留 > 0.5s；
  2. 字幕 end 不越过下一事件 start（不重叠、不吞下一句）；
  3. 字幕不侵入 >=4s 的说明段间隙（与 qa_check C6 同源）；
  4. 抽样：音频边界前仍有尾音能量、停留末端已静音（音落幕与字幕撤走对齐）。

用法：
  subhold_check.py --root <项目根> --stem <stem> [--data <中间产物目录>] [--mp4 <成片>]
退出码：0=PASS / 2=BLOCK / 1=脚本异常。依赖 numpy，用 run.sh 同款 Python。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[4]
GAP_SEC = 4.0            # 与 qa_check.GAP_SEC 同源
MIN_MEDIAN_HOLD = 0.5    # 中位停留低于此值 → BLOCK（默认 0.8s，留足余量）


def ass_seconds(t: str) -> float:
    h, m, rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def main() -> int:
    ap = argparse.ArgumentParser(description="句尾字幕停留验收")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--stem", required=True)
    ap.add_argument("--data", default=None, help="中间产物目录（默认 ielts-video/data）")
    ap.add_argument("--mp4", default=None, help="成片（默认 ielts-video/out/<stem>.mp4）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "ielts-video" / "scripts"))
    from ffmpeg_helper import get_ffmpeg
    ffmpeg = get_ffmpeg()

    data = Path(args.data) if args.data else root / "ielts-video" / "data"
    mp4 = Path(args.mp4) if args.mp4 else root / "ielts-video" / "out" / f"{args.stem}.mp4"
    if not mp4.is_absolute():
        mp4 = root / mp4
    for p in (data / "timeline.json", data / "subtitles.ass", mp4):
        if not p.is_file():
            print(f"[错误] 缺少文件：{p}", file=sys.stderr)
            return 1
    try:
        import numpy as np
    except ImportError:
        print("[错误] 需要 numpy（用 run.sh 指定的 Python）", file=sys.stderr)
        return 1

    tl = json.loads((data / "timeline.json").read_text(encoding="utf-8"))
    dlg_all = []
    for ln in (data / "subtitles.ass").read_text(encoding="utf-8").splitlines():
        if ln.startswith("Dialogue:"):
            p = ln.split(",", 9)
            dlg_all.append((ass_seconds(p[1]), ass_seconds(p[2]), p[3].strip()))
    # 2026-11-17：读题段也出字幕（EN_INSTR 样式），只验正文字幕的停留；
    # 读题字幕显示区间=音频播放区间、无 hold，不参与「句尾停留」判定。
    dlg = [(s, e) for s, e, style in dlg_all if style != "EN_INSTR"]
    n_instr_sub = sum(1 for _s, _e, style in dlg_all if style == "EN_INSTR")
    ev = sorted(tl["events"], key=lambda e: float(e["start"]))
    total = float(tl["total_duration"])
    if len(dlg) != len(ev):
        print(f"[错误] ASS 正文字幕 {len(dlg)} != timeline 事件 {len(ev)}"
              f"（读题字幕 {n_instr_sub} 段已排除）", file=sys.stderr)
        return 1

    # [1][2] 停留时长 + 不重叠
    gains, overlaps = [], 0
    for i, (e, d) in enumerate(zip(ev, dlg)):
        ae, se = float(e["end"]), d[1]
        gains.append(se - ae)
        nxt = float(ev[i + 1]["start"]) if i + 1 < len(ev) else total
        if se > nxt + 0.001:
            overlaps += 1

    # [3] 说明段侵入
    gaps, cur = [], 0.0
    for e in ev:
        s, en = float(e["start"]), float(e["end"])
        if s - cur >= GAP_SEC:
            gaps.append((cur, s))
        cur = max(cur, en)
    if total - cur >= GAP_SEC:
        gaps.append((cur, total))
    intr = 0
    for ds, de in dlg:
        for gs, ge in gaps:
            if ds < ge - 0.05 and de > gs + 0.05:
                intr += 1
                break

    print("=" * 64)
    print(f" 句尾字幕停留验收  {args.stem}")
    print(f" 中间产物: {data}")
    print(f" 成片:     {mp4}")
    print("-" * 64)
    print(f" 事件 {len(ev)}（另读题字幕 {n_instr_sub} 段不参与停留判定）；"
          f"字幕相对音频边界停留：中位 {float(np.median(gains)):.3f}s，"
          f"范围 {min(gains):.3f}~{max(gains):.3f}s")
    print(f" 停留 >0.5s 的事件: {sum(1 for g in gains if g > 0.5)}/{len(gains)}")
    print(f" [1][2] 字幕重叠下一句: {overlaps} 句（BLOCK 阈值 >0）")
    print(f" [3] 说明段间隙 {len(gaps)} 段，字幕侵入: {intr} 条（BLOCK 阈值 >0）")

    # [4] 抽样：边界前有尾音能量、停留末端已静音
    sr = 16000
    raw = subprocess.check_output(
        [ffmpeg, "-v", "error", "-i", str(mp4), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        stderr=subprocess.DEVNULL)
    pcm = np.frombuffer(raw, dtype="<f4")

    def rms(t0, t1):
        a, b = max(0, int(t0 * sr)), min(len(pcm), int(t1 * sr))
        return float(np.sqrt(np.mean(pcm[a:b] ** 2))) if b > a else 0.0

    print(" [4] 抽样（前 3 句 rep0）：边界前 40ms RMS / 停留末端 RMS")
    for i in range(min(3, len(ev) // 3)):
        e, d = ev[i * 3], dlg[i * 3]
        ae, se = float(e["end"]), d[1]
        print(f"   idx={e['sentence_index']} 边界={ae:.2f}s 字幕end={se:.2f}s "
              f"停留={se - ae:.2f}s | {rms(ae - 0.04, ae):.4f} / {rms(se - 0.04, se):.4f}")
    print("-" * 64)

    reasons = []
    if float(np.median(gains)) <= MIN_MEDIAN_HOLD:
        reasons.append(f"中位停留 {float(np.median(gains)):.2f}s 过低")
    if overlaps:
        reasons.append(f"{overlaps} 句字幕重叠下一句")
    if intr:
        reasons.append(f"{intr} 条字幕侵入说明段")
    verdict = "BLOCK" if reasons else "PASS"
    print(f" 结论: {verdict}" + (f"（{'；'.join(reasons)}）" if reasons else " — 字幕停留覆盖句尾，不重叠/不侵入说明段"))
    print("=" * 64)
    return 2 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())

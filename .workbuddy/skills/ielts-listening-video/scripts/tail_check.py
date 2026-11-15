#!/usr/bin/env python3
"""句尾尾音验收（出片后音轨质检，17 项 qa_check.py 之外的补充项）。

背景：qa_check.py 只看时间轴/字幕/成片规格，看不出音频片段末尾被硬切。
2026-11-14 用户验收发现「每句末尾丢半个音节」，根因是 faster-whisper
词级 end 偏早；03_rebuild_audio.py 用 TailFitter 按能量补尾音。本脚本
独立复算，避免「改了切片逻辑但 QA 全 PASS」的漏网。

检查四项（见 references/02-qa-rules.md「句尾尾音验收」）：
  1. 原音频残留：从每句实际播放右边界向后扫 20ms 窗 RMS，双阈值
     （10% 句内 RMS 判「还有声音」、25% 判「实质人声」），连续 6 窗
     低于低阈值视为语音结束；统计残留 >100ms 的句子占比。
  2. 片段越界重叠：play_end 越过下一句 start >10ms。next_start 取 sentences.json
     里「所有正文句」（含 <0.5s 被过滤、无 timeline 事件的短句），与 03 的 clamp 同源。
  3. 成片硬切：解码 mp4 音轨，对每个 repeat=0 事件算
     tail_ratio = RMS(末 30ms) / RMS(句中段)，并用
     fade_ratio = RMS(末 10ms) / RMS(末 40~30ms) 判断 40ms 淡出是否生效。
     只有 tail_ratio >0.8 且 fade_ratio >0.35（无淡出）才 BLOCK；
     tail_ratio 高但淡出生效的句子打 WARN「淡出豁免」，不拦。
     健康基线：tail_ratio 中位 <0.10、fade_ratio 中位 <0.10。
  4. 补尾音触顶截断：tail_pad_sec 达到 TAIL_PAD_MAX_SEC（1.50s）且上限后
     仍有 >100ms 实质人声、又明显早于下一句 start → 本句尾音被硬上限砍掉。
     必须查：40ms 淡出会把成片 tail_ratio 压成「淡出豁免」，第 3 项看不出来
     （whisper 把 computerised 误拆时 end 可偏早 1.14s）。
     play_end 落在 instructions.json 的说明段区间内时排除（读题/结束语人声，
     不是本句尾音），只记 WARN。

BLOCK 判据：无淡出的硬切 >0 句 / 重叠 >0 句 / 残留占比 >20% / 触顶截断 >0 句。
残留明细一律以 WARN 打印——对话体句间空隙常 <0.35s，whisper 下一句
start 又常标晚，扫描会看到下一句起音，那不是本句被切掉的尾音。

用法：
  tail_check.py --root <项目根> --stem c18_test4_part3
  tail_check.py --root <项目根> --stem c18_test4_part3 \
      --data /tmp/ielts_p3_data --audio resource/c18_test4_part3.mp3

退出码：0 = PASS（可交付）、2 = BLOCK（不得交付）、1 = 脚本异常。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[4]

TAIL_THR_RATIO = 0.10
TAIL_WINDOW_SEC = 0.02
# 与 03_rebuild_audio.py 保持一致：词间辅音闭塞/引号停顿可有 60~100ms 静音，
# 3 窗会把「后面还有本句的词」误判成语音结束，验收口径必须与切片口径同源。
TAIL_SILENCE_WINDOWS = 6
RESIDUAL_BLOCK_MS = 100       # 残留超过该毫秒数才计入统计
MIN_HEADROOM_SEC = 0.10       # 与下一句至少还有这么多空隙才算「有空间」
SCAN_CAP_SEC = 1.80           # 与 03_rebuild_audio.py TAIL_SCAN_MAX_SEC 同源
PAD_MAX_SEC = 1.50            # 与 03_rebuild_audio.py TAIL_PAD_MAX_SEC 同源
# 「补到上限却还有本句人声」是最隐蔽的漏音：40ms 淡出会把成片末尾 tail_ratio
# 压成淡出豁免，成片硬切检查看不出来。仅当残留明显早于下一句起点时才判，
# 避免把下一句起音误判成上限截断。
CAP_RESIDUAL_MS = 100
CAP_NEXT_GUARD_SEC = 0.10
HARD_CUT_RATIO = 0.8
# 40ms 线性淡出会让「末 30ms」窗口包含淡出斜坡：句中段偏轻的短句（如
# "Yes, dairy cows."，与下一句零空隙、补不了尾音）算出的 tail_ratio 可 >1，
# 但并非硬切。用「淡出检测」区分：淡出起点前 10ms 仍满幅，末尾 10ms 应
# 接近静音（比值 <0.35）；真硬切没有淡出，两者相当（≈1）。
FADE_RATIO_MAX = 0.35
# 残留扫描无法百分之百区分「本句被切掉的尾音」与「下一句起音」：
# 对话体句间空隙常只有 0.07~0.35s，whisper 的下一句 start 又常标晚，
# 6 窗(120ms)容忍会扫到下一句满幅人声；紧邻说明段时也会扫到读题人声。
# 因此 BLOCK 以「成片音轨实测硬切」为主判据（最贴近听感），
# 残留扫描作为大面积漏补的兜底：占比 >20% 才 BLOCK。
# 实测：缺陷版 22/33=67% 且 16 句硬切；修复版 0/33 且 0 句硬切；
# 剑18 Test4 Part3（旧口径成片）5/54=9%、0 句硬切 → PASS + WARN 明细。
RESIDUAL_BLOCK_RATIO = 0.20


def load_pcm(path, ffmpeg, sr=16000):
    import numpy as np
    raw = subprocess.check_output(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "f32le",
         "-ac", "1", "-ar", str(sr), "-"],
        stderr=subprocess.DEVNULL,
    )
    return np.frombuffer(raw, dtype="<f4")


def rms(x, sr, t0, t1):
    import numpy as np
    i0, i1 = max(0, int(t0 * sr)), min(len(x), int(t1 * sr))
    if i1 <= i0:
        return 0.0
    s = x[i0:i1]
    return float(np.sqrt(np.mean(s * s)))


def residual_ms(x, sr, play_end, body_rms, scan_cap):
    """返回播放右边界之后仍检测到的「实质人声」毫秒数。

    双阈值：thr_low 只用于判定「是否还有声音」（重置静音计数），
    thr_speech 才用于累计「被切掉的响亮音节」。句尾常有一段 10~20%
    句内能量的房间混响/辅音摩擦余晖，它不是用户听感里丢掉的那半个音节；
    若用单一 10% 阈值，混响会把验收误判成 BLOCK（剑18 Test4 Part4
    index 3/22/31 实测 RMS 0.010~0.025，随后立刻归零）。
    而真正被硬切的音节能量远高于此（1855 尾音 0.13~0.28、
    'art.' 0.14~0.37），仍会被稳定抓到。
    """
    thr_low = max(0.002, body_rms * TAIL_THR_RATIO)
    thr_speech = max(0.004, body_rms * 0.25)
    last, low = 0.0, 0
    for k in range(int(scan_cap / TAIL_WINDOW_SEC)):
        w = rms(x, sr, play_end + k * TAIL_WINDOW_SEC,
                play_end + (k + 1) * TAIL_WINDOW_SEC)
        if w >= thr_low:
            low = 0
            if w >= thr_speech:
                last = (k + 1) * TAIL_WINDOW_SEC
        else:
            low += 1
            if low >= TAIL_SILENCE_WINDOWS:
                break
    return last * 1000


def main() -> int:
    ap = argparse.ArgumentParser(description="雅思精听成片句尾尾音验收")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--stem", required=True)
    ap.add_argument("--data", default=None, help="中间产物目录（默认 ielts-video/data）")
    ap.add_argument("--audio", default=None, help="原音频（默认 resource/<stem>.mp3）")
    ap.add_argument("--mp4", default=None, help="成片（默认 ielts-video/out/<stem>.mp4）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "ielts-video" / "scripts"))
    from ffmpeg_helper import get_ffmpeg
    ffmpeg = get_ffmpeg()

    data = Path(args.data) if args.data else root / "ielts-video" / "data"
    audio = Path(args.audio) if args.audio else root / "resource" / f"{args.stem}.mp3"
    mp4 = Path(args.mp4) if args.mp4 else root / "ielts-video" / "out" / f"{args.stem}.mp4"
    if not audio.is_absolute():
        audio = root / audio
    if not mp4.is_absolute():
        mp4 = root / mp4

    for p in (data / "sentences.json", data / "timeline.json", audio, mp4):
        if not p.is_file():
            print(f"[错误] 缺少文件：{p}", file=sys.stderr)
            return 1

    try:
        import numpy as np  # noqa: F401
    except ImportError:
        print("[错误] 需要 numpy（用 run.sh 指定的 Python 运行）", file=sys.stderr)
        return 1

    sentences = json.loads((data / "sentences.json").read_text(encoding="utf-8"))
    timeline = json.loads((data / "timeline.json").read_text(encoding="utf-8"))
    # 说明段（读题/结束语）人声不是正文句尾音。whisper 偶尔把结束语错配成
    # 最后一句正文（剑17 T4 P2 的 idx41 实为 "You now have half a minute to
    # check your answers to part 2."），补尾音会扫进说明段，第 [4] 项必须排除。
    instr_ranges = []
    ipath = data / "instructions.json"
    if ipath.is_file():
        try:
            instr_ranges = [(float(x["start"]), float(x["end"]))
                            for x in json.loads(ipath.read_text(encoding="utf-8"))]
        except (OSError, ValueError, KeyError, TypeError):
            instr_ranges = []


    def in_instruction(t):
        return any(a - 0.05 <= t <= b + 0.05 for a, b in instr_ranges)
    by_index = {s["index"]: s for s in sentences if s.get("start") is not None}
    events = timeline.get("events", [])
    first = {}
    for e in events:
        if int(e.get("repeat", 0)) == 0 and e["sentence_index"] not in first:
            first[e["sentence_index"]] = e

    sr = 16000
    src = load_pcm(audio, ffmpeg, sr)
    mix = load_pcm(mp4, ffmpeg, sr)

    ordered = sorted((i, by_index[i]) for i in first if i in by_index)
    # next_start 与 03_rebuild_audio.py 的 clamp 同源：必须用「所有正文句」的起点，
    # 不能用只有 timeline 事件（已过滤 <0.5s 短句）的 ordered。否则既漏掉
    # 「补尾音越过短句」的重叠，又会残留扫描把短句人声误当成本句尾音。
    all_ordered = sorted(by_index.items())
    next_start = {}
    for k, (i, _) in enumerate(all_ordered):
        next_start[i] = (all_ordered[k + 1][1]["start"]
                         if k + 1 < len(all_ordered) else len(src) / sr)

    residuals, overlaps, ratios, capped, cap_skipped = [], [], [], [], []
    for i, s in ordered:
        e = first[i]
        play_end = s["start"] + (float(e["end"]) - float(e["start"]))
        body = rms(src, sr, s["start"], s["end"])
        nxt = next_start[i]
        # timeline 的 start/end 经过 round(...,3)，重建 play_end 会带 ±1ms 误差；
        # 句间零空隙（play_end == 下一句 start）是文档允许的正常情况，
        # 只有实质越过下一句 >10ms 才算重叠，避免把舍入误差误判为 BLOCK。
        if play_end > nxt + 0.01:
            overlaps.append((i, round(play_end - nxt, 3)))
        headroom = nxt - play_end - MIN_HEADROOM_SEC
        pad = float(e.get("tail_pad_sec", 0))
        if headroom > 0:
            r = residual_ms(src, sr, play_end, body, min(SCAN_CAP_SEC, headroom))
            if r > 0:
                residuals.append((i, round(r)))
            # 补到硬上限且上限后仍有实质人声、又明显早于下一句起点
            # → 本句尾音被上限砍掉（whisper end 可能偏早 1s 以上）
            if (pad >= PAD_MAX_SEC - 0.01 and r > CAP_RESIDUAL_MS
                    and play_end + r / 1000 < nxt - CAP_NEXT_GUARD_SEC):
                if in_instruction(play_end):
                    cap_skipped.append((i, round(r)))   # 说明段人声，不是本句尾音
                else:
                    capped.append((i, round(r)))
        # 成片音轨末尾硬切
        dur = float(e["end"]) - float(e["start"])
        if dur > 0.5:
            en = float(e["end"])
            tail = rms(mix, sr, en - 0.03, en)
            mid = rms(mix, sr, float(e["start"]) + 0.25 * dur, en - 0.15 * dur)
            ratio = tail / mid if mid > 1e-6 else 0.0
            head = rms(mix, sr, en - 0.04, en - 0.03)   # 淡出起点前 10ms
            last10 = rms(mix, sr, en - 0.01, en)         # 片段最后 10ms
            fade_ratio = last10 / head if head > 1e-6 else 0.0
            ratios.append((i, ratio, fade_ratio))

    bad = [(i, ms) for i, ms in residuals if ms > RESIDUAL_BLOCK_MS]
    ratio_values = [r for _, r, _ in ratios]
    ratios_sorted = sorted(ratio_values)
    median = ratios_sorted[len(ratios_sorted) // 2] if ratios_sorted else 0.0
    fade_sorted = sorted(f for _, _, f in ratios)
    fade_median = fade_sorted[len(fade_sorted) // 2] if fade_sorted else 0.0
    # tail_ratio 高但淡出生效 → 斜坡被误读为满幅人声，记 WARN，不 BLOCK
    hard = [(i, r, f) for i, r, f in ratios
            if r > HARD_CUT_RATIO and f > FADE_RATIO_MAX]
    faded = [(i, r, f) for i, r, f in ratios
             if r > HARD_CUT_RATIO and f <= FADE_RATIO_MAX]
    n_hard = len(hard)
    pads = [float(e.get("tail_pad_sec", 0)) for e in first.values()]

    print("=" * 64)
    print(f" 句尾尾音验收  {args.stem}")
    print(f" 原音频: {audio}")
    print(f" 成片:   {mp4}")
    print("-" * 64)
    print(f" 参与句数 {len(ordered)}，补尾音 {sum(1 for p in pads if p > 0)} 句，"
          f"平均 +{sum(pads)/len(pads)*1000:.0f}ms / 最大 +{max(pads)*1000:.0f}ms")
    ratio_bad = len(bad) / max(1, len(ordered))
    print(f" [1] 残留 >50ms: {sum(1 for _, ms in residuals if ms > 50)} 句；"
          f">100ms: {len(bad)} 句 ({ratio_bad*100:.1f}%，BLOCK 阈值 >{RESIDUAL_BLOCK_RATIO*100:.0f}%)")
    if bad:
        print(f"     WARN 明细（句子 index, 残留 ms）: {bad[:12]}")
        print("     多为下一句起音 / 说明段人声，非本句尾音；如听感有异请抽听对应句")
    print(f" [2] 片段越界重叠: {len(overlaps)} 句 {overlaps[:5]}")
    print(f" [3] 成片末尾衰减 tail_ratio: 中位 {median:.3f}，>0.8 且无淡出的硬切 {n_hard} 句；"
          f"淡出比 fade_ratio 中位 {fade_median:.3f}")
    if faded:
        print(f"     WARN 淡出豁免（tail_ratio>0.8 但 40ms 淡出生效）: {len(faded)} 句 "
              f"{[(i, round(r, 2), round(f, 2)) for i, r, f in faded[:6]]}")
    print(f" [4] 补到硬上限 {PAD_MAX_SEC:.2f}s 且上限后仍有本句人声: {len(capped)} 句 {capped[:8]}")
    if cap_skipped:
        print(f"     说明段人声已排除（非本句尾音）: {len(cap_skipped)} 句 {cap_skipped[:6]}")
    print("-" * 64)

    reasons = []
    if n_hard:
        reasons.append(f"{n_hard} 句成片末尾硬切")
    if overlaps:
        reasons.append(f"{len(overlaps)} 句片段重叠")
    if ratio_bad > RESIDUAL_BLOCK_RATIO:
        reasons.append(f"{len(bad)}/{len(ordered)} 句残留 >100ms")
    if capped:
        reasons.append(f"{len(capped)} 句被补尾音硬上限截断")
    verdict = "BLOCK" if reasons else "PASS"
    print(f" 结论: {verdict}" + (f"（{'；'.join(reasons)}）" if reasons else ""))
    if reasons:
        print(" —— 不得交付；检查 03_rebuild_audio.py 的 TAIL_PAD_MAX_SEC / "
              "TAIL_SCAN_MAX_SEC 是否被硬上限截断，或句间空隙确实为 0")
    print("=" * 64)
    return 2 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())

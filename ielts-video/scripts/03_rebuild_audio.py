#!/usr/bin/env python3
"""
步骤3：音频重组（题目说明一遍 + 正文逐句 ×3 遍）

根据 instructions.json（题目说明段）和 annotated.json（正文句）：
- 题目说明/考试指令段 → 原样播放一遍，不循环，不显示字幕
- 正文句 → 逐句循环 3 遍（第1遍挖空、第2遍高亮、第3遍双语）
  - 三遍之间停顿 1 秒（停顿期间字幕保持显示）
  - 每句三遍结束后停顿 3 秒，再进入下一句

产出：
- data/rebuilt_audio.mp3（重组后的音频）
- data/timeline.json（每个字幕事件的重组时间轴）
"""
import json
import subprocess
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ffmpeg_helper import get_ffmpeg

FFMPEG = get_ffmpeg()

AUDIO = os.environ.get("AUDIO", "resource/Test1 Part2.mp3")
ANNOTATED = os.environ.get("ANNOTATED", "ielts-video/data/annotated.json")
INSTRUCTIONS = os.environ.get("INSTRUCTIONS", "ielts-video/data/instructions.json")
OUT_AUDIO = os.environ.get("OUT_AUDIO", "ielts-video/data/rebuilt_audio.mp3")
OUT_TIMELINE = os.environ.get("OUT_TIMELINE", "ielts-video/data/timeline.json")

REPEAT_GAP_SEC = float(os.environ.get("REPEAT_GAP_SEC", "1.0"))  # 三遍之间的停顿
SENT_GAP_SEC = float(os.environ.get("SENT_GAP_SEC", "3.0"))      # 每句三遍后的停顿
REPEATS = int(os.environ.get("REPEATS", "3"))                    # 每句遍数
MIN_SENT_SEC = float(os.environ.get("MIN_SENT_SEC", "0.5"))      # 最小句长过滤

# —— 句尾尾音保护（2026-11-14 验收缺陷修复）——
# 01_align.py 直接把 faster-whisper 的词级 end 当作句子右边界，而词级 end
# 系统性偏早：剑18 Test4 Part3 实测 77.4% 的句子在 end 之后仍有人声，
# 残留中位数 140ms / P90 340ms / 最大 400ms，切片时最后一音节的尾音被硬切
# （听感即「每句末尾丢半个音节」）。这里按原音频能量把右边界延到真实语音
# 结束点，并同步给 timeline / 字幕时长，保证音画一致。
#
# 2026-11-14 二次校准（用户补充的实例）：剑18 Test4 Part4
# "…he landed there in 1855."，small 模型把 "1855" 词尾标在 184.160s，
# 而原音频高能量人声一直延续到 184.52s、低电平尾音到 184.62s；
# medium 模型对同一片段给出 184.580s。旧上限 0.45s 会把真实结束点
# (约 +490ms) 硬截断，正是用户听到「1855 丢半个音节」的那一刀。
# 因此上限/扫描范围放宽到 0.60/0.80s，由能量检测决定补偿量；
# 仍用「下一正文句 start - guard」兜底，绝不越过下一句造成片段重叠。
#
# 2026-11-14 三次校准（剑17 全量回归时发现）：c17_test2_part1
# "In fact, there's Mrs Carroll."，whisper end=222.180，能量检测算出真实
# 结束点约 +740ms，却被 0.60s 硬上限截断；播放右边界之后仍有约 140ms
# 强人声（RMS 0.16，接近句内 0.14）被硬切，成片 tail_ratio 0.826 且无淡出，
# 尾音验收正确判 BLOCK。
#
# 2026-11-14 四次校准（同一篇另一句）：c17_test2_part1
# "I'm used to working on a computer - I presume they're computerised?"，
# whisper 把 computerised 误拆成 "computer eyes"，词级 end 只标到 151.780，
# 而实际语音到 152.920（偏早 1.14s）；下一句 "Oh yes." 从 153.320 开始。
# 0.90s 上限仍会把 "computerised?" 后半砍掉，且 40ms 淡出会把成片末尾
# tail_ratio 压成「淡出豁免」，尾音验收反而看不出来。故上限/扫描定到
# 1.50/1.80s，并在 tail_check.py 增加「补到上限后仍有实质人声」检查项。
# 两道兜底不变：连续 6 窗静音 break + hard_cap = 下一正文句 start - guard，
# 因此不会越过下一句、不会造成片段重叠。
TAIL_PAD_MIN_SEC = float(os.environ.get("TAIL_PAD_MIN_SEC", "0.10"))  # 至少补这么多
TAIL_PAD_MAX_SEC = float(os.environ.get("TAIL_PAD_MAX_SEC", "1.50"))  # 最多补这么多
TAIL_SCAN_MAX_SEC = 1.80      # 向后扫描的最大范围
TAIL_WINDOW_SEC = 0.02        # 能量窗宽
TAIL_THR_RATIO = 0.10         # 活跃阈值 = 句内 RMS × 该比例
# 连续低能量窗达到该数量才判定「语音结束」。3 窗(60ms)会被词间辅音闭塞 /
# 引号停顿骗到：剑18 Test4 Part4 实测两句因此提前收尾——
#   index 5  "…Romantic movement." 末尾 60ms 静音后又有人声，尾音被切 140ms；
#   index 15 "…to be a 'work of art'." 引号让 whisper 漏匹配 work/art，
#            end 落在 242.780，而 "art." 从 242.88 响到 243.36，整词被砍。
# 放宽到 6 窗(120ms)可跨过正常词内停顿，又不会越过句间通常 >200ms 的停顿；
# 仍由 hard_cap = 下一正文句 start - guard 兜底，绝不造成片段重叠。
TAIL_SILENCE_WINDOWS = 6
TAIL_GUARD_SEC = 0.02         # 与下一片段之间保留的最小空隙


class TailFitter:
    """按原音频能量定位每句真实语音结束点；numpy/解码不可用时降级为固定余量。"""

    def __init__(self, audio, ffmpeg):
        self.ok = False
        self.samples = None
        self.sr = 16000
        self.duration = None
        try:
            import numpy as np
            raw = subprocess.check_output(
                [ffmpeg, "-v", "error", "-i", audio,
                 "-f", "f32le", "-ac", "1", "-ar", str(self.sr), "-"],
                stderr=subprocess.DEVNULL,
            )
            self.samples = np.frombuffer(raw, dtype="<f4").copy()
            self.duration = len(self.samples) / self.sr
            self.np = np
            self.ok = True
        except Exception as e:
            print(f"[warn] 句尾能量检测不可用，降级为固定余量 {TAIL_PAD_MIN_SEC:.2f}s：{e}")

    def rms(self, t0, t1):
        if not self.ok or t1 <= t0:
            return 0.0
        sr = self.sr
        i0, i1 = max(0, int(t0 * sr)), min(len(self.samples), int(t1 * sr))
        if i1 <= i0:
            return 0.0
        seg = self.samples[i0:i1]
        return float(self.np.sqrt(self.np.mean(seg * seg)))

    def play_end(self, start, end, next_start):
        """返回该句实际用于播放（切片/字幕/时间轴）的右边界。"""
        if next_start is None:
            next_start = self.duration if self.ok else end + TAIL_SCAN_MAX_SEC
        hard_cap = min(end + TAIL_PAD_MAX_SEC, next_start - TAIL_GUARD_SEC)
        if hard_cap <= end:
            return end
        if not self.ok:
            return min(end + TAIL_PAD_MIN_SEC, hard_cap)
        # 扫描范围不超过下一片段起点，避免把下一句开头误判为本句尾音
        scan_cap = max(0.0, min(TAIL_SCAN_MAX_SEC, next_start - end - 0.05))
        body = self.rms(start, end)
        thr = max(0.002, body * TAIL_THR_RATIO)
        last = 0.0
        low = 0
        for k in range(int(scan_cap / TAIL_WINDOW_SEC)):
            w = self.rms(end + k * TAIL_WINDOW_SEC,
                         end + (k + 1) * TAIL_WINDOW_SEC)
            if w >= thr:
                last = (k + 1) * TAIL_WINDOW_SEC
                low = 0
            else:
                low += 1
                if low >= TAIL_SILENCE_WINDOWS:
                    break
        return min(max(end + TAIL_PAD_MIN_SEC, end + last + 0.03), hard_cap)


def cut_segment(audio, start, end, out_path, fade=False):
    """用 ffmpeg 精确切出一段音频。"""
    dur = max(0.05, end - start)
    filters = []
    # 句尾 40ms 线性淡出：whisper 词级边界偶尔正好落在响亮音节上（句间零空隙、
    # 补不了尾音时尤其明显），硬切会留下「咔」的突兀感。只作用于片段末尾 40ms，
    # 不改时长、不丢内容，也不影响 timeline / 字幕。说明段不淡出（末尾是静音）。
    if fade and dur > 0.12:
        filters.append(f"afade=t=out:st={dur - 0.04:.3f}:d=0.04")
    # ffmpeg 7.1 的 libmp3lame 对 mono/fltp 输入会报
    # "inadequate AVFrame plane padding"（剑18 音频全部是 44100Hz mono）。
    # 统一转成 s16 packed 再编码；stereo 输入时长不变，已做 16+4 篇回归。
    filters.append("aformat=sample_fmts=s16")
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", audio,
        "-af", ",".join(filters),
        "-acodec", "libmp3lame", "-q:a", "2",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def make_silence(duration, out_path):
    """生成一段静音。"""
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{duration:.3f}", "-acodec", "libmp3lame", "-q:a", "2",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def concat_audio(segments, out_path):
    """用 ffmpeg concat 拼接多段音频。"""
    list_file = Path(out_path).with_suffix(".txt")
    abs_segs = [str(Path(p).resolve()) for p in segments]
    list_file.write_text("".join(f"file '{p}'\n" for p in abs_segs), encoding="utf-8")
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-af", "aformat=sample_fmts=s16",
        "-acodec", "libmp3lame", "-q:a", "2", out_path,
    ]
    subprocess.run(cmd, check=True)
    list_file.unlink(missing_ok=True)


def build(instructions, annotated, work_dir):
    """
    重组：指令段 + 正文逐句×3遍。
    返回 (timeline_events, total_duration, audio_pieces)。
    """
    # 正文句（有匹配时间戳、且不过短）
    sents = []
    for s in annotated:
        if s.get("start") is None or s.get("end") is None:
            continue
        if (s["end"] - s["start"]) < MIN_SENT_SEC:
            continue
        sents.append(s)

    # 指令段（按时间排序）
    instrs = sorted(instructions, key=lambda x: x["start"])

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    audio_pieces = []
    timeline = []
    # 读题段在重组时间轴上的区间（2026-11-17 用户要求：读题时也显示完整英文字幕）。
    # instructions 原始 start/end 是原音频时间；读题音频切片后 cursor 恰好落在它
    # 在成片里的播放区间，故在此记录 cursor 前的位置。04 据此生成一条无挖空的字幕。
    instruction_events = []
    cursor = 0.0

    # 合并指令段和正文句，按时间顺序交织
    # 构造一个"段落"列表：每个元素是 ('instr', 段) 或 ('sent', 句)
    segments = [("instr", i) for i in instrs] + [("sent", s) for s in sents]
    segments.sort(key=lambda x: x[1]["start"])

    fitter = TailFitter(AUDIO, FFMPEG)

    # 预计算每句播放右边界。clamp 用「下一正文句 start」而不是「下一片段 start」：
    # extract_instructions() 把说明段起点设成上一句的 end（同样偏早），若拿它当上限，
    # 紧邻说明段之前的句子会补不到尾音。说明段本质是大空隙，改为让位即可。
    #
    # 2026-11-14 五次校准：clamp 必须用「所有正文句」的起点，不能只用参与重组的
    # sent_items。c17_test1_part1 的 idx67 "It's on the 17th, from 10 a.m." 之后
    # 紧接短句 idx68 "until 3."（0.46s < MIN_SENT_SEC 被过滤），两段语音几乎连续；
    # 若 clamp 跳过短句直接用 idx69 的 start，补尾音会跨过 358.74 一路吞到 359.58，
    # 把「until 3.」整句混进上一句、下一句又从 360.20 才开始播。
    sent_items = [(i, it) for i, (k, it) in enumerate(segments) if k == "sent"]
    all_starts = sorted(s["start"] for s in annotated
                        if s.get("start") is not None and s.get("end") is not None)
    play_ends = {}
    for k, (i, it) in enumerate(sent_items):
        next_start = next((t for t in all_starts if t > it["start"] + 1e-6), None)
        if next_start is None:
            next_start = fitter.duration or (it["end"] + TAIL_SCAN_MAX_SEC)
        play_ends[i] = fitter.play_end(it["start"], it["end"], next_start)

    # 说明段起点后移到前一句补出来的尾音之后，避免重复播放同一段音频
    instr_start = {}
    for i, (kind, it) in enumerate(segments):
        if kind != "instr":
            continue
        prev = next((j for j in range(i - 1, -1, -1) if segments[j][0] == "sent"), None)
        s = it["start"]
        if prev is not None and s < play_ends[prev]:
            s = play_ends[prev]
        instr_start[i] = s

    sent_counter = 0
    for idx, (kind, item) in enumerate(segments):
        if kind == "instr":
            # 题目说明段：原样播一遍，不挖空。起点后移到前句补出的尾音之后，
            # 避免与前一句 TailFitter 补出来的尾音重复播放同一段原音频。
            start = instr_start[idx]
            end = item["end"]
            seg_path = work / f"instr_{start:.1f}.mp3"
        else:
            # 正文句：右边界延到真实语音结束点，避免最后一音节被硬切
            start = item["start"]
            end = play_ends[idx]
            seg_path = work / f"seg_{sent_counter:03d}.mp3"
        cut_segment(AUDIO, start, end, str(seg_path), fade=(kind == "sent"))
        dur = end - start

        if kind == "instr":
            # 读题段：音频原样播一遍；并生成一条「完整英文、不挖空」字幕。
            # 区间用重组时间轴的 cursor（音频实际播放位置），不是原音频 start。
            # 2026-11-17 用户要求「读题时也要显示读题字幕，只读一遍、不挖空」。
            seg_start = cursor
            audio_pieces.append(str(seg_path))
            cursor += dur
            instruction_events.append({
                "start": round(seg_start, 3),
                "end": round(cursor, 3),
                "text": item["text"],
            })
            continue

        # 正文句：逐句 ×3 遍
        sent_dur = dur
        tail_pad = round(end - item["end"], 3)
        for rep in range(REPEATS):
            event = {
                "sentence_index": item["index"],
                "repeat": rep,  # 0=第1遍 1=第2遍 2=第3遍
                "start": round(cursor, 3),
                "end": round(cursor + sent_dur, 3),
                "tail_pad_sec": tail_pad,
                "text": item["text"],
                "translation": item.get("translation", ""),
                "highlights": item.get("highlights", []),
                "words": item.get("words", []),
            }
            timeline.append(event)
            cursor += sent_dur
            audio_pieces.append(str(seg_path))

            # 三遍之间停顿 1 秒（最后一遍后不加）
            if rep < REPEATS - 1:
                gap_path = work / f"rgap_{sent_counter:03d}_{rep}.mp3"
                make_silence(REPEAT_GAP_SEC, str(gap_path))
                audio_pieces.append(str(gap_path))
                cursor += REPEAT_GAP_SEC

        # 每句三遍后停顿 3 秒（最后一句后不加）
        if sent_counter < len(sents) - 1:
            gap_path = work / f"sgap_{sent_counter:03d}.mp3"
            make_silence(SENT_GAP_SEC, str(gap_path))
            audio_pieces.append(str(gap_path))
            cursor += SENT_GAP_SEC

        sent_counter += 1

    return timeline, cursor, audio_pieces, instruction_events


def main():
    annotated = json.loads(Path(ANNOTATED).read_text(encoding="utf-8"))
    instructions = json.loads(Path(INSTRUCTIONS).read_text(encoding="utf-8"))
    n_sent = sum(1 for s in annotated if s.get("start") is not None)
    print(f"题目说明段 {len(instructions)} 个，正文句 {n_sent} 句参与重组")

    work_dir = "ielts-video/data/tmp_audio"
    timeline, total_dur, audio_pieces, instruction_events = build(instructions, annotated, work_dir)

    pads = [ev["tail_pad_sec"] for ev in timeline if ev.get("tail_pad_sec", 0) > 0]
    if pads:
        print(f"[句尾保护] {len(pads)//REPEATS}/{n_sent} 句补尾音，"
              f"平均 +{sum(pads)/len(pads)*1000:.0f}ms / 最大 +{max(pads)*1000:.0f}ms，"
              f"成片共增加 {sum(pads):.1f}s")
    print(f"[拼合] 共 {len(audio_pieces)} 段音频，总时长 {total_dur:.1f}s")
    concat_audio(audio_pieces, OUT_AUDIO)

    result = {
        "repeat_gap_sec": REPEAT_GAP_SEC,
        "sent_gap_sec": SENT_GAP_SEC,
        "repeats": REPEATS,
        "total_duration": round(total_dur, 3),
        "events": timeline,
        "instruction_events": instruction_events,
    }
    Path(OUT_TIMELINE).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 重组音频 {OUT_AUDIO}")
    print(f"[完成] 时间轴 {OUT_TIMELINE}（{len(timeline)} 个字幕事件 + {len(instruction_events)} 段读题字幕）")


if __name__ == "__main__":
    main()

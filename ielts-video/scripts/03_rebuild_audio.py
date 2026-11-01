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


def cut_segment(audio, start, end, out_path):
    """用 ffmpeg 精确切出一段音频。"""
    dur = max(0.05, end - start)
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", audio, "-acodec", "libmp3lame", "-q:a", "2",
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
    cursor = 0.0

    # 合并指令段和正文句，按时间顺序交织
    # 构造一个"段落"列表：每个元素是 ('instr', 段) 或 ('sent', 句)
    segments = [("instr", i) for i in instrs] + [("sent", s) for s in sents]
    segments.sort(key=lambda x: x[1]["start"])

    sent_counter = 0
    for kind, item in segments:
        if kind == "instr":
            # 题目说明段：原样播一遍，无字幕
            start, end = item["start"], item["end"]
            seg_path = work / f"instr_{start:.1f}.mp3"
            cut_segment(AUDIO, start, end, str(seg_path))
            audio_pieces.append(str(seg_path))
            cursor += (end - start)
        else:
            # 正文句：逐句 ×3 遍
            start, end = item["start"], item["end"]
            seg_path = work / f"seg_{sent_counter:03d}.mp3"
            cut_segment(AUDIO, start, end, str(seg_path))
            sent_dur = end - start

            for rep in range(REPEATS):
                event = {
                    "sentence_index": item["index"],
                    "repeat": rep,  # 0=第1遍 1=第2遍 2=第3遍
                    "start": round(cursor, 3),
                    "end": round(cursor + sent_dur, 3),
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

    return timeline, cursor, audio_pieces


def main():
    annotated = json.loads(Path(ANNOTATED).read_text(encoding="utf-8"))
    instructions = json.loads(Path(INSTRUCTIONS).read_text(encoding="utf-8"))
    n_sent = sum(1 for s in annotated if s.get("start") is not None)
    print(f"题目说明段 {len(instructions)} 个，正文句 {n_sent} 句参与重组")

    work_dir = "ielts-video/data/tmp_audio"
    timeline, total_dur, audio_pieces = build(instructions, annotated, work_dir)

    print(f"[拼合] 共 {len(audio_pieces)} 段音频，总时长 {total_dur:.1f}s")
    concat_audio(audio_pieces, OUT_AUDIO)

    result = {
        "repeat_gap_sec": REPEAT_GAP_SEC,
        "sent_gap_sec": SENT_GAP_SEC,
        "repeats": REPEATS,
        "total_duration": round(total_dur, 3),
        "events": timeline,
    }
    Path(OUT_TIMELINE).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 重组音频 {OUT_AUDIO}")
    print(f"[完成] 时间轴 {OUT_TIMELINE}（{len(timeline)} 个字幕事件）")


if __name__ == "__main__":
    main()

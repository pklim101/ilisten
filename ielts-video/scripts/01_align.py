#!/usr/bin/env python3
"""
步骤1：音频对齐（区分「题目说明段」与「正文句」）

用 faster-whisper 对雅思听力音频做转写，得到词级时间戳。
再结合用户提供的「原文文本」（纯正文，不含题目说明）做序列对齐，
把每句正文切成独立时间片段；正文之外的时间空隙识别为「题目说明/考试指令」段。

输出：
- data/sentences.json   正文句（每句：文本、start、end、words）
- data/instructions.json 题目说明段（每段：start、end、text[whisper转写]）

关键约定（本版本核心）：
- 题目说明/指令段 → 原样播放一遍，不循环，不显示字幕
- 正文句 → 循环 3 遍（由后续脚本处理）
"""
import json
import re
import os
from pathlib import Path

AUDIO = os.environ.get("AUDIO", "resource/Test1 Part2.mp3")
TEXT = os.environ.get("TEXT", "resource/c19_test1_part2.md")
OUT = os.environ.get("OUT", "ielts-video/data/sentences.json")
OUT_INSTR = os.environ.get("OUT_INSTR", "ielts-video/data/instructions.json")

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")
LANG = "en"

# 判定"题目说明段"的最小空隙（秒）：正文句之间的空隙大于此值视为题目说明。
# 正常句间停顿 < 3s，题目说明段（含读题静音）都 > 5s，取 4.0 安全区分。
GAP_THRESHOLD = float(os.environ.get("GAP_THRESHOLD", "4.0"))

# 对齐合理性校验阈值（防"锚点错位"缺陷）：
# 高频词（school/the/a 等）会被 SequenceMatcher 错配到几十秒外的同名位置，
# 把句子边界错误拉长，导致成片出现"有字幕、无人声"的静音区段。
MAX_SENT_SEC = float(os.environ.get("MAX_SENT_SEC", "15.0"))   # 单句最大合理时长
MAX_WORD_GAP_SEC = float(os.environ.get("MAX_WORD_GAP_SEC", "2.0"))  # 句内相邻词最大间隔


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9'\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_original_sentences(text: str) -> list[str]:
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def load_original_text(path: str) -> str:
    content = Path(path).read_text(encoding="utf-8")
    lines = content.splitlines()
    body = [l for l in lines if not re.match(r"^\s*(PART|SECTION|Test)\b", l, re.IGNORECASE)]
    return "\n".join(body)


def transcribe(audio: str):
    from faster_whisper import WhisperModel
    import multiprocessing
    cpu = multiprocessing.cpu_count()
    n_threads = max(1, min(cpu, 4))
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=n_threads)
    segments, info = model.transcribe(audio, language=LANG, word_timestamps=True)
    segs = []
    for seg in segments:
        segs.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "words": [
                {"w": w.word, "start": w.start, "end": w.end}
                for w in (seg.words or [])
            ],
        })
    return segs, info.duration


def align(orig_sents, whisper_segs):
    """整篇序列对齐，返回正文句列表（含时间戳）。"""
    import difflib
    from collections import defaultdict

    w_words = []
    for seg in whisper_segs:
        for w in seg["words"]:
            w_words.append((normalize(w["w"]), w["start"], w["end"]))

    orig_tokens = []
    for sent_idx, s in enumerate(orig_sents):
        for w in normalize(s).split():
            orig_tokens.append((w, sent_idx))

    orig_seq = [t[0] for t in orig_tokens]
    w_seq = [w[0] for w in w_words]

    sm = difflib.SequenceMatcher(None, orig_seq, w_seq, autojunk=False)
    orig_to_w = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                orig_to_w[i1 + k] = j1 + k

    sent_words = defaultdict(list)
    for op, wp in orig_to_w.items():
        si = orig_tokens[op][1]
        sent_words[si].append((op, wp))

    results = []
    for sent_idx, s in enumerate(orig_sents):
        pairs = sent_words.get(sent_idx, [])
        if not pairs:
            results.append({"index": sent_idx, "text": s, "start": None,
                            "end": None, "words": [], "matched": False})
            continue
        first_wp = min(p[1] for p in pairs)
        last_wp = max(p[1] for p in pairs)
        start = w_words[first_wp][1]
        end = w_words[last_wp][2]
        wpos_sorted = sorted(set(p[1] for p in pairs))
        words = [{"w": w_words[i][0], "start": w_words[i][1], "end": w_words[i][2]}
                 for i in wpos_sorted]

        # —— 对齐合理性校验（防锚点错位）——
        # 从末词往前回溯，遇到时间跳变即截断，只保留"时间连续"的词段。
        # 否则高频词错配会把句子边界错误拉长（详见 docs/07）。
        cut = len(words)
        for i in range(1, len(words)):
            gap = words[i]["start"] - words[i - 1]["end"]
            if gap > MAX_WORD_GAP_SEC:
                cut = i  # 在此处断开，丢弃错配的尾部词
                break
        if cut < len(words):
            words = words[:cut]
            end = words[-1]["end"]

        # 截断后仍超长（例如首词就错配到很远），整句判为未匹配，走插值兜底
        if (end - start) > MAX_SENT_SEC:
            results.append({"index": sent_idx, "text": s, "start": None,
                            "end": None, "words": [], "matched": False})
            continue

        results.append({
            "index": sent_idx, "text": s,
            "start": round(start, 3), "end": round(end, 3),
            "words": words, "matched": True,
        })

    # 插值兜底：夹在相邻已匹配句之间的短空隙，用相邻时间估计
    for i, r in enumerate(results):
        if r["matched"]:
            continue
        prev = results[i - 1] if i > 0 else None
        nxt = results[i + 1] if i + 1 < len(results) else None
        if prev and prev["matched"] and nxt and nxt["matched"]:
            gap = nxt["start"] - prev["end"]
            if 0.5 < gap < 6.0:  # 只对短空隙插值（正文句间正常停顿）
                r["start"] = round(prev["end"] + 0.1, 3)
                r["end"] = round(nxt["start"] - 0.1, 3)
                r["matched"] = True
                r["interpolated"] = True
    return results


def extract_instructions(whisper_segs, sentences, total_duration):
    """
    识别题目说明段：正文句时间范围之外的空隙。
    返回 [{start, end, text}]，按时间排序。
    """
    body_ranges = sorted(
        [(s["start"], s["end"]) for s in sentences if s["matched"]],
        key=lambda x: x[0],
    )

    instructions = []
    cursor = 0.0
    for bs, be in body_ranges:
        if bs - cursor >= GAP_THRESHOLD:
            instructions.append((cursor, bs))
        cursor = max(cursor, be)
    if total_duration - cursor >= GAP_THRESHOLD:
        instructions.append((cursor, total_duration))

    result = []
    for is_start, is_end in instructions:
        text_parts = []
        for seg in whisper_segs:
            if seg["end"] > is_start and seg["start"] < is_end:
                text_parts.append(seg["text"])
        result.append({
            "start": round(is_start, 3),
            "end": round(is_end, 3),
            "text": " ".join(text_parts).strip(),
        })
    return result


def main():
    print(f"[1/4] 加载原文：{TEXT}")
    orig_text = load_original_text(TEXT)
    orig_sents = split_original_sentences(orig_text)
    print(f"      原文切分出 {len(orig_sents)} 句")

    print(f"[2/4] faster-whisper 转写（模型 {MODEL_SIZE}）")
    whisper_segs, total_dur = transcribe(AUDIO)
    total_words = sum(len(s["words"]) for s in whisper_segs)
    print(f"      whisper 转写出 {len(whisper_segs)} 段 / {total_words} 词，总时长 {total_dur:.1f}s")

    print("[3/4] 对齐正文与音频时间轴")
    sentences = align(orig_sents, whisper_segs)
    matched = sum(1 for s in sentences if s["matched"])
    print(f"      对齐成功 {matched}/{len(sentences)} 句")

    print("[4/4] 识别题目说明段")
    instructions = extract_instructions(whisper_segs, sentences, total_dur)
    print(f"      识别出 {len(instructions)} 个题目说明段")
    for i, ins in enumerate(instructions):
        print(f"        [{i}] {ins['start']:.1f}s~{ins['end']:.1f}s ({ins['end']-ins['start']:.1f}s) :: {ins['text'][:50]}")

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps(sentences, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(OUT_INSTR).write_text(json.dumps(instructions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      已写入 {OUT} 和 {OUT_INSTR}")


if __name__ == "__main__":
    main()

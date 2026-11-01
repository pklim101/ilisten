#!/usr/bin/env python3
"""
步骤4：生成三态 ASS 字幕

根据 timeline.json，为每个字幕事件生成三态字幕：
- 第1遍（repeat=0）：挖空态 —— 考点词用下划线填空（_____），其余词正常显示
- 第2遍（repeat=1）：全文态 —— 完整原文，考点词高亮（黄色）
- 第3遍（repeat=2）：双语态 —— 完整原文 + 中文翻译（下方）

使用 ASS 格式（支持样式、颜色、下划线、多行），直接烧录到画面。

版式（仿参考视频）：
- 英文字幕居中，大字号，白色 + 黑色描边
- 考点词第2遍用黄色高亮
- 第1遍挖空处用下划线占位
- 第3遍在英文下方显示中文翻译（较小字号）
"""

import json
import re
import os
from pathlib import Path

TIMELINE = os.environ.get("TIMELINE", "ielts-video/data/timeline.json")
OUT_ASS = os.environ.get("OUT_ASS", "ielts-video/data/subtitles.ass")

VIDEO_W = int(os.environ.get("VIDEO_W", "1920"))
VIDEO_H = int(os.environ.get("VIDEO_H", "1080"))


def escape_ass(text):
    """转义 ASS 特殊字符。"""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def estimate_width(text, fontsize):
    """粗略估算文本渲染宽度（像素），忽略 ASS 标签 {...}。
    英文字符约 0.5×字号，中文/全角约 1.0×字号，空格约 0.28×字号。"""
    w = 0.0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{" and "}" in text[i:]:
            # 跳过 ASS 标签块
            i = text.index("}", i) + 1
            continue
        if ch == " ":
            w += fontsize * 0.28
        elif ord(ch) > 0x2E80:  # CJK 等全角字符
            w += fontsize * 1.0
        else:
            w += fontsize * 0.52
        i += 1
    return w


def wrap_text(text, fontsize, max_width):
    """按可用宽度手动折行，返回用 \\N 连接的多行文本。
    在空格处折行（英文），忽略 ASS 标签估算宽度。"""
    if estimate_width(text, fontsize) <= max_width:
        return text

    # 按空格切分（ASS 标签内无空格，安全）
    words = text.split(" ")
    lines = []
    cur = ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if estimate_width(trial, fontsize) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return "\\N".join(lines)


def fmt_time(sec):
    """秒 → ASS 时间 h:mm:ss.cc"""
    sec = max(0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def apply_highlights(text, highlights, mode):
    """
    在文本中应用高亮/挖空。
    mode: 'blank'（挖空）| 'highlight'（黄色高亮）
    返回带 ASS 标签的文本。
    """
    if not highlights:
        return escape_ass(text)

    # 按文本顺序，用 ASS 标签包裹高亮片段
    # 先按在文本中的出现位置排序
    positions = []
    for h in highlights:
        # 大小写不敏感查找
        idx = text.lower().find(h.lower())
        if idx >= 0:
            positions.append((idx, idx + len(h), h))

    if not positions:
        return escape_ass(text)

    # 去重叠、按位置排序
    positions.sort(key=lambda x: (x[0], -x[1]))
    merged = []
    for p in positions:
        if merged and p[0] < merged[-1][1]:
            # 重叠，合并
            merged[-1] = (merged[-1][0], max(merged[-1][1], p[1]), text[merged[-1][0]:max(merged[-1][1], p[1])])
        else:
            merged.append(p)

    # 组装
    result = []
    cursor = 0
    for start, end, _ in merged:
        result.append(escape_ass(text[cursor:start]))
        seg = text[start:end]
        if mode == "blank":
            # 挖空：每个挖空词用「一条连续下划线」占位。
            # 关键修复：不用全角空格(em space)分隔，避免下划线在折行时
            # 与相邻词/标点重叠。下划线长度按单词数取固定值（每词约 6 个下划线），
            # 控制整体字幕宽度，避免长短语的下划线过长导致字幕顶到左右边缘。
            n_words = max(1, len(seg.split()))
            underline_len = min(6 * n_words + 2, 14)  # 短则约6~8，长短语封顶14
            underline = "_" * underline_len
            # 红色下划线 + 深色（正文恢复色）标定作用范围
            result.append(r"{\u1\c&H000000C0&}" + underline + r"{\u0\c&H00202020&}")
        else:  # highlight
            # 考点词用醒目的橙红色（浅色背景上清晰），其余恢复深色
            result.append(r"{\c&H000080FF&}" + escape_ass(seg) + r"{\c&H00202020&}")
        cursor = end
    result.append(escape_ass(text[cursor:]))
    return "".join(result)


def build_ass(timeline):
    events = timeline["events"]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: EN,Arial,48,&H00202020,&H00202020,&H00FFFFFF,&H00000000,1,0,0,0,100,100,0,0,1,4,0,5,120,120,40,1
Style: ZH,Arial,36,&H00404040,&H00404040,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,3,0,5,120,120,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # 可用折行宽度：画面宽减去左右各 120px 内边距
    MAX_W_EN = VIDEO_W - 2 * 120
    MAX_W_ZH = VIDEO_W - 2 * 120

    lines = [header]
    for ev in events:
        rep = ev["repeat"]
        start = fmt_time(ev["start"])
        end = fmt_time(ev["end"])

        # 方案：先对纯文本折行，再逐行应用高亮标签，避免标签跨行断裂。
        # \an5 居中 + \N 硬换行，不依赖 libass 的 \q2。
        # 手动折行保证每行宽度 ≤ 1680px，字幕距离左右边缘至少 120px。
        if rep == 0:
            # 第1遍：挖空
            en_lines = wrap_text(ev["text"], 48, MAX_W_EN).split("\\N")
            rendered = "\\N".join(apply_highlights(l, ev["highlights"], "blank") for l in en_lines)
            lines.append(f"Dialogue: 0,{start},{end},EN,,0,0,0,,{{\\an5}}{rendered}")
        elif rep == 1:
            # 第2遍：全文 + 考点词高亮
            en_lines = wrap_text(ev["text"], 48, MAX_W_EN).split("\\N")
            rendered = "\\N".join(apply_highlights(l, ev["highlights"], "highlight") for l in en_lines)
            lines.append(f"Dialogue: 0,{start},{end},EN,,0,0,0,,{{\\an5}}{rendered}")
        else:
            # 第3遍：英文全文 + 中文翻译（英文在上、中文在下，整体居中）
            en_lines = wrap_text(ev["text"], 48, MAX_W_EN).split("\\N")
            en_text = "\\N".join(escape_ass(l) for l in en_lines)
            if ev.get("translation"):
                zh_lines = wrap_text(ev["translation"], 36, MAX_W_ZH).split("\\N")
                zh_text = "\\N".join(escape_ass(l) for l in zh_lines)
                combined = en_text + r"{\rZH}" + r"\N" + zh_text
                lines.append(f"Dialogue: 0,{start},{end},EN,,0,0,0,,{{\\an5}}{combined}")
            else:
                lines.append(f"Dialogue: 0,{start},{end},EN,,0,0,0,,{{\\an5}}{en_text}")

    return "\n".join(lines)


def main():
    timeline = json.loads(Path(TIMELINE).read_text(encoding="utf-8"))
    ass = build_ass(timeline)
    Path(OUT_ASS).write_text(ass, encoding="utf-8")
    n = len(timeline["events"])
    print(f"[完成] 生成 {n} 个字幕事件 → {OUT_ASS}")
    print(f"  三态：挖空({n//3}句) / 全文高亮({n//3}句) / 中英双语({n//3}句)")


if __name__ == "__main__":
    main()

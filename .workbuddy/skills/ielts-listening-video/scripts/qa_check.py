#!/usr/bin/env python3
"""出片后自动质检（只读）。规则见 references/02-qa-rules.md。

退出码：0 = PASS/WARN（可交付）、2 = BLOCK（不得交付）、1 = 脚本异常（按 BLOCK 处理）。

用法：
  qa_check.py --root <项目根> --stem c19_test2_part1 [--baseline <json>] [--skip-volume]
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BASELINE = Path(__file__).resolve().parents[1] / "references" / "qa_baseline.json"

INSTR_RE = re.compile(r"^\s*(ANNOUNCER|NARRATOR)\s*:|^\s*(PART|SECTION|Test)\b", re.IGNORECASE)
SPEAKER_RE = re.compile(r"^\s*([A-Z][A-Z0-9 ]{1,20}):", re.MULTILINE)
INSTR_LABELS = {"ANNOUNCER", "NARRATOR", "PART", "SECTION", "TEST"}
INSTR_KEYWORDS = ("you will hear", "part ", "section", "listen", "questions", "answers",
                  "now ", "pause", "end of", "the ielts listening test", "check your work")

MAX_EVENT_SEC = 20.0          # 短句绝对上限：锚点错位把短句拉到 20s 以上照样 BLOCK
ALIGN_WPS = 2.2               # 与 01_align.py 一致的估计语速（词/秒）
EVENT_HEADROOM = 1.5          # 与 01_align.py 的 1.5 系数同源
EVENT_SLACK_SEC = 3.0
MIN_ALIGN_RATE = 0.90
MAX_DURATION_DELTA_SEC = 1.0
GAP_SEC = 4.0
MIN_MARGIN = 50
MP4_MIN_BYTES = 1_000_000
PDF_MIN_BYTES = 10_000
WARN_LONG_VIDEO_SEC = 20 * 60


def event_max_sec(n_words: int) -> float:
    """按原文词数估算单句合理时长上限，与 01_align.py 的 sentence_max_sec 同源。

    2026-11-09 c19_test2_part4 实测：Part4 学术讲座 45 词长句实际 22.2s，
    固定 MAX_EVENT_SEC=20 会误伤成「锚点错位」（但 39/39 全对齐、人声兜底 -10.8dB）。
    改用「词数 / 2.2 × 1.5 + 3s」并保留 20s 下限：短句仍被 20s 卡死，
    真正的锚点错位（短句被拉到 25s+）依然 BLOCK，绝不靠放宽阈值放行。
    取不到原文词数时 n_words=0 → 退化为固定 20s，安全兜底。
    """
    return max(MAX_EVENT_SEC, n_words / ALIGN_WPS * EVENT_HEADROOM + EVENT_SLACK_SEC)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def ass_time_to_sec(t: str) -> float:
    h, m, rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def uncovered_gaps(events: list[dict], total: float, gap_sec: float = GAP_SEC) -> list[tuple[float, float]]:
    """未被任何 event 覆盖、且长度 >= gap_sec 的区间（即题目说明段）。"""
    gaps, cur = [], 0.0
    for e in sorted(events, key=lambda x: x["start"]):
        s, en = float(e["start"]), float(e["end"])
        if s - cur >= gap_sec:
            gaps.append((cur, s))
        cur = max(cur, en)
    if total - cur >= gap_sec:
        gaps.append((cur, total))
    return gaps


def parse_ass(text: str):
    lines = text.splitlines()
    dialogues = []
    margins = {}
    fmt = None
    for line in lines:
        if line.startswith("Format:"):
            fmt = [x.strip() for x in line.split(":", 1)[1].split(",")]
        elif line.startswith("Style:"):
            vals = [x.strip() for x in line.split(":", 1)[1].split(",")]
            if fmt:
                for k in ("MarginL", "MarginR"):
                    if k in fmt and fmt.index(k) < len(vals):
                        try:
                            margins[k] = int(float(vals[fmt.index(k)]))
                        except ValueError:
                            pass
        elif line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) >= 10:
                try:
                    dialogues.append((ass_time_to_sec(parts[1]), ass_time_to_sec(parts[2]), parts[9]))
                except (ValueError, IndexError):
                    pass
    return dialogues, margins


def probe_media(mp4: Path) -> dict:
    """优先 ffprobe，缺失时解析 ffmpeg -i 的 stderr。"""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    candidates = [shutil.which("ffprobe"),
                  "/Users/sofia/libo/local/miniconda3/bin/ffprobe",
                  str(Path(ff).parent / "ffprobe")]
    for exe in candidates:
        if not exe or not Path(exe).exists():
            continue
        r = subprocess.run([exe, "-v", "error", "-show_entries",
                            "format=duration:stream=codec_name,codec_type,width,height",
                            "-of", "json", str(mp4)], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            continue
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            continue
        res = {"duration": None, "width": None, "height": None, "vcodec": None, "acodec": None}
        try:
            res["duration"] = float(d["format"]["duration"])
        except (KeyError, TypeError, ValueError):
            pass
        for st in d.get("streams", []):
            if st.get("codec_type") == "video":
                res["vcodec"] = st.get("codec_name")
                res["width"], res["height"] = st.get("width"), st.get("height")
            elif st.get("codec_type") == "audio":
                res["acodec"] = st.get("codec_name")
        if res["duration"] and res["width"]:
            res["source"] = "ffprobe"
            return res

    r = subprocess.run([ff, "-i", str(mp4)], capture_output=True, text=True)
    s = r.stderr
    res = {"duration": None, "width": None, "height": None, "vcodec": None, "acodec": None,
           "source": "ffmpeg -i"}
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", s)
    if m:
        res["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:\s*(\w+)[^\n]*?(\d{3,4})x(\d{3,4})", s)
    if m:
        res["vcodec"] = m.group(1)
        res["width"], res["height"] = int(m.group(2)), int(m.group(3))
    m = re.search(r"Audio:\s*(\w+)", s)
    if m:
        res["acodec"] = m.group(1)
    return res


def max_volume_of_segment(mp4: Path, start: float, dur: float) -> float | None:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([ff, "-y", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
                        "-i", str(mp4), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", r.stderr)
    return float(m.group(1)) if m else None


class Report:
    def __init__(self):
        self.items: list[tuple[str, str, str, str]] = []

    def add(self, level: str, code: str, label: str, detail: str):
        self.items.append((level, code, label, detail))

    @property
    def n_block(self):
        return sum(1 for x in self.items if x[0] == "BLOCK")

    @property
    def n_warn(self):
        return sum(1 for x in self.items if x[0] == "WARN")

    @property
    def n_pass(self):
        return sum(1 for x in self.items if x[0] == "PASS")


def main() -> int:
    ap = argparse.ArgumentParser(description="雅思精听成片自动质检（只读）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--stem", required=True)
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--skip-volume", action="store_true", help="跳过 C7 volumedetect 兜底")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    stem = args.stem
    data = root / "ielts-video" / "data"
    out = root / "ielts-video" / "out"
    mp4 = out / f"{stem}.mp4"
    pdf = out / f"{stem}_vocab.pdf"
    src_md = root / "resource" / f"{stem}.md"

    required = [data / "sentences.json", data / "instructions.json", data / "annotated.json",
                data / "timeline.json", data / "subtitles.ass", mp4]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        print("[错误] 缺少质检所需文件（是否刚跑完本篇？data/ 会被下一篇覆盖）：", file=sys.stderr)
        for p in missing:
            print("   ", p, file=sys.stderr)
        return 1

    try:
        sentences = json.loads((data / "sentences.json").read_text(encoding="utf-8"))
        instructions = json.loads((data / "instructions.json").read_text(encoding="utf-8"))
        annotated = json.loads((data / "annotated.json").read_text(encoding="utf-8"))
        timeline = json.loads((data / "timeline.json").read_text(encoding="utf-8"))
        ass_text = (data / "subtitles.ass").read_text(encoding="utf-8")
        baseline_all = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if Path(args.baseline).is_file() else {}
    except Exception as e:
        print(f"[错误] 读取质检数据失败：{e}", file=sys.stderr)
        return 1

    events = timeline.get("events", [])
    repeats = int(timeline.get("repeats", 3))
    total = float(timeline.get("total_duration", 0.0))
    dialogues, margins = parse_ass(ass_text)
    rep = Report()

    # ---- A 素材格式 ----
    bad_instr = [s.get("text", "") for s in sentences if INSTR_RE.match(s.get("text", ""))]
    if bad_instr:
        rep.add("BLOCK", "A1", "指令行过滤", f"仍有 {len(bad_instr)} 句指令行被当正文：{bad_instr[0][:50]}")
    else:
        rep.add("PASS", "A1", "指令行过滤", "ANNOUNCER/NARRATOR/PART 行均已剔除")

    src_labels = set()
    if src_md.is_file():
        src_labels = {m.strip() for m in SPEAKER_RE.findall(src_md.read_text(encoding="utf-8"))
                      if m.strip().upper() not in INSTR_LABELS}
    res_labels = {m.group(1).strip() for s in sentences
                  for m in [SPEAKER_RE.search(s.get("text", ""))]
                  if m and m.group(1).strip().upper() not in INSTR_LABELS}
    if not src_labels:
        rep.add("PASS", "A2", "说话人标签", "源文无说话人标签（独白类）")
    elif src_labels & (res_labels or set()):
        rep.add("PASS", "A2", "说话人标签", f"保留 {len(src_labels & (res_labels or set()))} 种：{', '.join(sorted(src_labels)[:3])}")
    else:
        rep.add("BLOCK", "A2", "说话人标签", f"源文有 {sorted(src_labels)} 但结果全无，正文可能被误删")

    # ---- B 对齐与说明段 ----
    matched = sum(1 for s in sentences if s.get("matched"))
    rate = matched / len(sentences) if sentences else 0.0
    if rate < MIN_ALIGN_RATE:
        rep.add("BLOCK", "B1", "对齐率", f"{matched}/{len(sentences)} = {rate:.1%} (< 90%)")
    else:
        rep.add("PASS", "B1", "对齐率", f"{matched}/{len(sentences)} = {rate:.1%}  (>= 90%)")

    unmatched = [s.get("text", "") for s in sentences if not s.get("matched")]
    def is_residual(t: str) -> bool:
        return not re.sub(r"[\W_]+", "", t) or len(t.split()) <= 1
    bad_unmatched = [t for t in unmatched if not is_residual(t)]
    if bad_unmatched:
        rep.add("BLOCK", "B2", "未匹配句", f"{len(bad_unmatched)} 句是完整内容（静默丢失）：{bad_unmatched[0][:55]}")
    else:
        rep.add("PASS", "B2", "未匹配句", f"{len(unmatched)} 句，均为孤立残片 {unmatched[:4]}")

    base = baseline_all.get(stem, baseline_all.get("_default", {}))
    typical = int(base.get("instruction_typical", 4))
    ins_max = int(base.get("instruction_max", 6))
    n_ins = len(instructions)
    if n_ins > ins_max:
        rep.add("BLOCK", "B3", "说明段数量", f"{n_ins} 段 (上限 {ins_max})，疑似正文被误判为说明段")
    elif n_ins > typical:
        rep.add("WARN", "B3", "说明段数量", f"{n_ins} 段 (基线 {typical})，请人工确认")
    else:
        rep.add("PASS", "B3", "说明段数量", f"{n_ins} 段 (基线 {typical})")

    sents_norm = [norm(s.get("text", "")) for s in sentences if norm(s.get("text", ""))]
    max_ratio, worst = 0.0, ""
    for ins in instructions:
        ni = norm(ins.get("text", ""))
        if not ni:
            continue
        for sn in sents_norm:
            r = difflib.SequenceMatcher(None, ni, sn).ratio()
            if r > max_ratio:
                max_ratio, worst = r, ni[:50]
    long_no_keyword = [ins for ins in instructions
                       if len(ins.get("text", "").split()) > 120
                       and not any(k in ins.get("text", "").lower() for k in INSTR_KEYWORDS)]
    if max_ratio > 0.80:
        rep.add("BLOCK", "B4", "说明段文本", f"与正文句相似度 {max_ratio:.2f} > 0.80：{worst}")
    elif long_no_keyword:
        rep.add("WARN", "B4", "说明段文本", f"{len(long_no_keyword)} 段超长且无指令关键词，请人工确认")
    else:
        rep.add("PASS", "B4", "说明段文本", f"无正文句混入（最高相似度 {max_ratio:.2f}）")

    # ---- C 时间轴与字幕 ----
    durs = [float(e["end"]) - float(e["start"]) for e in events]
    max_dur = max(durs) if durs else 0.0
    over: list[tuple[float, float, int, int]] = []
    longest_words, longest_limit = 0, MAX_EVENT_SEC
    for e in events:
        dur = float(e["end"]) - float(e["start"])
        si = e.get("sentence_index")
        text = ""
        if isinstance(si, int) and 0 <= si < len(annotated):
            text = str(annotated[si].get("text", "") or "")
        n_words = len(text.split())
        limit = event_max_sec(n_words)
        if dur > limit:
            over.append((dur, limit, n_words, si))
        if dur == max_dur:
            longest_words, longest_limit = n_words, limit
    if over:
        worst = max(over, key=lambda x: x[0] - x[1])
        rep.add("BLOCK", "C1", "超长事件",
                f"{len(over)} 个超出按词数自适应上限，最长 {max_dur:.1f}s；"
                f"最严重 si{worst[3]} {worst[0]:.1f}s / {worst[2]}词 上限 {worst[1]:.1f}s（疑似锚点错位）")
    else:
        rep.add("PASS", "C1", "超长事件",
                f"最长 {max_dur:.1f}s（{longest_words}词 → 自适应上限 {longest_limit:.1f}s，"
                f"固定下限 {MAX_EVENT_SEC:.0f}s），0 个超限")

    eligible = sum(1 for a in annotated
                   if a.get("start") is not None and a.get("end") is not None
                   and float(a["end"]) - float(a["start"]) >= 0.5)
    expected = eligible * repeats
    n_dlg = len(dialogues)
    if not (expected == len(events) == n_dlg):
        rep.add("BLOCK", "C2", "事件数口径",
                f"{eligible}句 x {repeats} = {expected}，timeline={len(events)}，ASS={n_dlg}（三者须相等）")
    else:
        rep.add("PASS", "C2", "事件数口径", f"{expected} = {eligible}句 x {repeats} = ASS {n_dlg} 行")

    media = probe_media(mp4)
    if media.get("duration") is None or abs(total - float(media["duration"])) > MAX_DURATION_DELTA_SEC:
        rep.add("BLOCK", "C3", "时长一致",
                f"timeline {total:.1f}s vs 成片 {media.get('duration')} (Δ {abs(total - float(media.get('duration') or 0)):.2f}s)")
    else:
        rep.add("PASS", "C3", "时长一致",
                f"timeline {total:.1f}s ≈ 成片 {float(media['duration']):.1f}s (Δ {abs(total - float(media['duration'])):.2f}s)")

    all_dlg_text = "\n".join(d[2] for d in dialogues)
    n_u1 = len(re.findall(r"\\u1(?!\d)", all_dlg_text))
    n_orange = len(re.findall(r"\\c&H000080FF&", all_dlg_text, re.IGNORECASE))
    n_zh = len(re.findall(r"\\rZH", all_dlg_text, re.IGNORECASE))
    exp_zh = sum(1 for e in events if e.get("repeat") == repeats - 1 and e.get("translation"))
    if not (n_u1 > 0 and n_orange > 0 and n_zh == exp_zh and n_u1 == n_orange):
        rep.add("BLOCK", "C4", "三态字幕",
                f"挖空 {n_u1} / 橙高亮 {n_orange} / 双语 {n_zh}（期望双语 {exp_zh}，挖空=橙高亮）")
    else:
        rep.add("PASS", "C4", "三态字幕", f"挖空 {n_u1} / 橙高亮 {n_orange} / 双语 {n_zh}")

    ml, mr = margins.get("MarginL"), margins.get("MarginR")
    n_an5 = len(re.findall(r"\\an5", all_dlg_text))
    if (ml is None or mr is None or min(ml, mr) < MIN_MARGIN or n_an5 != n_dlg):
        rep.add("BLOCK", "C5", "版式", f"MarginL/R={ml}/{mr}，\\an5 {n_an5} 行 vs Dialogue {n_dlg} 行")
    else:
        rep.add("PASS", "C5", "版式", f"MarginL/R={ml}/{mr}px, \\an5 居中({n_an5})")

    gaps = uncovered_gaps(events, total)
    overlap = 0
    for ds, de, _ in dialogues:
        for gs, ge in gaps:
            if ds < ge - 0.05 and de > gs + 0.05:
                overlap += 1
                break
    if overlap:
        rep.add("BLOCK", "C6", "说明段字幕", f"{overlap} 条 Dialogue 落在 {len(gaps)} 段说明段间隙内")
    else:
        rep.add("PASS", "C6", "说明段字幕", f"{len(gaps)} 段大间隙无 Dialogue 覆盖")

    if args.skip_volume or max_dur <= 12.0 or not events:
        rep.add("PASS", "C7", "人声兜底", "跳过（无 >12s 事件或 --skip-volume）")
    else:
        longest = max(events, key=lambda e: float(e["end"]) - float(e["start"]))
        seg_start = float(longest["start"]) + 1.0
        seg_dur = max(1.0, float(longest["end"]) - float(longest["start"]) - 2.0)
        mv = max_volume_of_segment(mp4, seg_start, seg_dur)
        if mv is None:
            rep.add("WARN", "C7", "人声兜底", "volumedetect 未取到 max_volume")
        elif mv < -35.0:
            rep.add("WARN", "C7", "人声兜底", f"最长事件区段 max_volume {mv:.1f}dB < -35dB，请抽听")
        else:
            rep.add("PASS", "C7", "人声兜底", f"最长 {max_dur:.1f}s 事件区段 max_volume {mv:.1f}dB")

    # ---- D/E 成片与交付物 ----
    ok_spec = (media.get("width") == 1920 and media.get("height") == 1080
               and media.get("vcodec") == "h264" and media.get("acodec") == "aac"
               and media.get("duration") is not None)
    if not ok_spec:
        rep.add("BLOCK", "D1", "成片规格",
                f"{media.get('width')}x{media.get('height')} / {media.get('vcodec')} / {media.get('acodec')} "
                f"(来源 {media.get('source')})")
    else:
        rep.add("PASS", "D1", "成片规格",
                f"1920x1080 / h264 / aac / {int(media['duration'])//60}:{int(media['duration'])%60:02d} "
                f"/ {mp4.stat().st_size/1048576:.1f}MB")

    if mp4.stat().st_size <= MP4_MIN_BYTES:
        rep.add("BLOCK", "D2", "成片非空", f"{mp4.stat().st_size} 字节 ≤ 1MB")
    else:
        rep.add("PASS", "D2", "成片非空", f"{mp4.stat().st_size/1048576:.1f}MB")

    if not pdf.is_file() or pdf.stat().st_size <= PDF_MIN_BYTES:
        rep.add("BLOCK", "E1", "考点词PDF", "缺失或过小（06 步失败不阻断成片，需补跑）")
    else:
        rep.add("PASS", "E1", "考点词PDF", f"{pdf.name} ({pdf.stat().st_size/1024:.1f}KB)")

    bg_sha = base.get("background_sha256")
    bg = root / "ielts-video" / "assets" / "background.png"
    if bg_sha and bg.is_file():
        actual = hashlib.sha256(bg.read_bytes()).hexdigest()
        if actual != bg_sha:
            rep.add("WARN", "E2", "背景图", "sha256 与基线不符，可能被 00_background.py 覆盖")
        else:
            rep.add("PASS", "E2", "背景图", "与基线一致")
    else:
        rep.add("PASS", "E2", "背景图", "无基线，跳过（勿运行 00_background.py）")

    if media.get("duration") and float(media["duration"]) > WARN_LONG_VIDEO_SEC:
        rep.add("WARN", "长视频", "完播率", "超过 20 分钟，建议简介给分段练习时间轴")

    # ---- 输出 ----
    title = re.sub(r"c(\d+)_test(\d+)_part(\d+)", r"剑\1 Test\2 Part\3", stem, flags=re.IGNORECASE)
    print("=" * 64)
    print(f" 质检摘要  {title}")
    print(f" 成片: {mp4}")
    print("-" * 64)
    for level, code, label, detail in rep.items:
        print(f" [{level:<5}] {label:<10} {detail}")
    print("-" * 64)
    verdict = "BLOCK" if rep.n_block else "PASS"
    print(f" 结论: {verdict}  ({rep.n_pass} PASS / {rep.n_warn} WARN / {rep.n_block} BLOCK)")
    if rep.n_block:
        print(" —— 不得交付、不得开始下一篇；成片已保留，请按 references/04 处置")
    else:
        print(" → 可交付；下一步 qa_frames.py 抽 3 帧 → 逐张 ViewImage 确认 → 全部通过才进入下一篇")
    print("=" * 64)
    return 2 if rep.n_block else 0


if __name__ == "__main__":
    sys.exit(main())

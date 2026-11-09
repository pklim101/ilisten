#!/usr/bin/env python3
"""只读扫描 resource/ 里成对的 mp3+md 素材，对比 out/ 判定出片状态。

本脚本【永不】调用 run.sh、永不写任何文件。上层 agent 依据输出的绝对路径逐篇驱动出片。

用法：
  scan_materials.py --root <项目根> [--only "test2 part1"] [--force] [--dry-run] [--json]

退出码：0 正常；3 = --only 未匹配到任何素材（绝不拼造路径）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# scripts -> ielts-listening-video -> skills -> .workbuddy -> 项目根
DEFAULT_ROOT = Path(__file__).resolve().parents[4]

STEM_RE = re.compile(r"^c(\d+)_test(\d+)_part(\d+)$", re.IGNORECASE)
MP4_MIN_BYTES = 1_000_000
PDF_MIN_BYTES = 10_000
STALE_TOLERANCE_SEC = 2.0


def parse_stem(stem: str):
    m = STEM_RE.fullmatch(stem.lower())
    if not m:
        return None
    return int(m.group(2)), int(m.group(3))  # (test, part)


def stem_matches(token: str, stem: str) -> bool:
    """把用户点名的各种写法匹配到真实 stem，只认已扫到的素材，绝不拼造路径。"""
    want = parse_stem(stem)
    if want is None:
        return False

    t = token.strip().lower().replace(" ", "_")
    if t == stem.lower() or t == stem.lower().split("_", 1)[-1]:
        return True                       # c19_test2_part1 / test2_part1

    mt = re.search(r"test_?(\d+)", t)
    mp = re.search(r"part_?(\d+)", t)
    if mt and mp:
        return (int(mt.group(1)), int(mp.group(1))) == want

    nums = re.findall(r"\d+", t)
    if len(nums) >= 2:
        return (int(nums[-2]), int(nums[-1])) == want   # "2 1" / "c19_2_1"
    return False


def scan(root: Path, only: str | None, force: bool) -> tuple[list[dict], list[str]]:
    res = root / "resource"
    out = root / "ielts-video" / "out"
    warnings: list[str] = []

    if not res.is_dir():
        raise FileNotFoundError(f"素材目录不存在：{res}")

    pairs: dict[str, dict[str, Path]] = {}
    for p in sorted(res.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext not in (".mp3", ".md"):
            continue
        stem = p.stem.lower()
        if not STEM_RE.fullmatch(stem):
            continue                      # 天然排除 qa_*.png / *_v1 / 其他杂项
        pairs.setdefault(stem, {})[ext] = p

    tasks: list[dict] = []
    for stem, files in sorted(pairs.items()):
        if ".mp3" not in files:
            # 只有 md 没音频：无法做 whisper 交叉校验，也无法出片，仍跳过。
            warnings.append(f"孤立素材 {stem}：缺 .mp3，跳过（无音频无法出片/校验原文）")
            continue
        # 只有 mp3 没 md：新流程，先由 fetch_transcript.py 联网检索 + 音频校验补齐原文。
        need_transcript = ".md" not in files
        text_path = files[".md"] if ".md" in files else (res / f"{stem}.md")

        mp4 = out / f"{stem}.mp4"
        pdf = out / f"{stem}_vocab.pdf"
        has_mp4 = mp4.is_file() and mp4.stat().st_size > MP4_MIN_BYTES
        has_pdf = pdf.is_file() and pdf.stat().st_size > PDF_MIN_BYTES
        newest_src = files[".mp3"].stat().st_mtime
        if ".md" in files:
            newest_src = max(newest_src, files[".md"].stat().st_mtime)
        stale = has_mp4 and newest_src > mp4.stat().st_mtime + STALE_TOLERANCE_SEC

        if only:
            if not stem_matches(only, stem):
                continue
            status, reason = "todo", "用户点名（强制重跑/覆盖）"
        elif force:
            status, reason = "todo", "--force 强制重跑"
        elif not has_mp4:
            status, reason = "todo", "无成片"
        elif not has_pdf:
            status, reason = "partial", "成片在但 _vocab.pdf 缺失/为空（按整篇重跑处理）"
        elif stale:
            status, reason = "rerun", "源素材 mtime 晚于成片"
        else:
            status, reason = "done", "成片与 PDF 齐全且素材未更新"

        # 需要补原文且本篇确实要跑时，前置为 needs_transcript；
        # 已成片且齐全（status=done）时不需要原文，保持 done。
        if need_transcript and status != "done":
            status = "needs_transcript"
            reason = "缺 .md，须先联网检索 + 音频交叉校验补齐原文，再出片"

        tasks.append({
            "stem": stem,
            "audio": str(files[".mp3"].resolve()),
            "text": str(text_path.resolve()),
            "text_exists": ".md" in files,
            "needs_transcript": status == "needs_transcript",
            "out_mp4": str(mp4.resolve()),
            "out_pdf": str(pdf.resolve()),
            "status": status,
            "reason": reason,
        })

    return tasks, warnings


def print_table(tasks: list[dict]) -> None:
    if not tasks:
        print("（无匹配素材）")
        return
    print(f"{'STATUS':<18} {'STEM':<20} 原因")
    print("-" * 88)
    for t in tasks:
        print(f"{t['status']:<18} {t['stem']:<20} {t['reason']}")
    print("-" * 88)
    print("待办（status != done）：")
    pend = [t for t in tasks if t["status"] != "done"]
    if not pend:
        print("  无，全部已完成")
    for t in pend:
        print(f"  {t['stem']}  ->  {t['out_mp4']}")
    print("\n逐篇执行（串行，绝对路径）：")
    for t in pend:
        if t.get("needs_transcript"):
            print(f"  # {t['stem']}：先执行阶段 1.5 补原文；返回 BLOCK 立即停止，不得出片")
            print(f'  $PY "$SKILL/scripts/fetch_transcript.py" --root "$ROOT" --stem {t["stem"]}')
        print(f"  bash \"$SKILL/scripts/run_one.sh\" \"{t['audio']}\" \"{t['text']}\" \"{t['out_mp4']}\"")


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描雅思精听素材并判定出片状态（只读）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="项目根目录")
    ap.add_argument("--only", default=None, help="只处理点名篇，如 'test2 part1' / c19_test2_part1")
    ap.add_argument("--force", action="store_true", help="全部强制重跑")
    ap.add_argument("--dry-run", action="store_true", help="只打印清单（本脚本本就不执行出片）")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 数组")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        tasks, warnings = scan(root, args.only, args.force)
    except Exception as e:
        print(f"[错误] 扫描失败：{e}", file=sys.stderr)
        return 1

    for w in warnings:
        print(f"[WARN] {w}", file=sys.stderr)

    if args.only and not tasks:
        print(f"[错误] --only \"{args.only}\" 未匹配到任何成对素材，已停止（不拼造路径）", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    else:
        print(f"项目根：{root}")
        print(f"模式：{'点名 ' + args.only if args.only else ('强制全量' if args.force else '默认全量扫描')}"
              f"{' [dry-run]' if args.dry_run else ''}")
        print()
        print_table(tasks)
        if args.dry_run:
            print("\n[dry-run] 以上仅为清单，未执行任何出片操作。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""阶段 1.5：只有音频时，自动取得「可用于 01_align.py 对齐」的听力原文 md。

设计要点（务必读 references/05-transcript-fetch.md）：
  阶段 A  DeepSeek deepseek-v4-pro + 思考模式(high) + 内置 web_search 工具
          → 联网检索 tapescript（用户给的原话作为提示词）
  阶段 B  deepseek-chat + json_object
          → 把长文本结构化抽取成「按录音顺序排列的 narrator / body 段」，
            剔除试卷题目、选项、答案表、解析、来源说明、markdown 标记
  阶段 C  faster-whisper 转写音频作为真值，与清洗后的正文做词级交叉校验
          → coverage / asr_coverage 双指标置信度闸门
            PASS  自动放行并落盘
            WARN  落盘但必须向用户展示差异
            BLOCK 不落盘、不得进入出片，等待人工提供权威原文

音频是唯一真值源：联网文本是「多数来源整理版」，个别虚词可能与官方
tapescript 有出入；直接落盘会触发 docs/07 的对齐锚点错位缺陷。

用法：
  fetch_transcript.py --root <ROOT> --stem c19_test2_part2 [--force] [--dry-run]
                      [--json] [--skip-asr] [--reuse-raw] [--effort high]
  fetch_transcript.py --root <ROOT> --stem c19_test2_part4 \
      --segments-file <已核过的结构化原文.json> --source-url <URL> [--source-url <URL> ...]

兜底入口（2026-11-09 新增）：当 DeepSeek 因版权策略拒答、阶段 A 返回 0 来源时，
可由 agent 用外部联网来源（多站交叉）整理出结构化 segments JSON 再注入。
**阶段 C 音频 ASR 双源校验与 PASS/WARN/BLOCK 闸门完全不跳过**；
`searched` 由 --source-url 判定，无来源一律 BLOCK；禁止与 --skip-asr 同用。
注入的 segments 不经过 LLM 改写，避免二次引入错词。

退出码：0 = PASS/WARN（已落盘或 dry-run）；2 = BLOCK（不可出片）；1 = 运行错误；
        3 = 参数/素材问题。密钥只从 DEEPSEEK_API_KEY 环境变量读取，绝不回显或落盘。
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# scripts -> ielts-listening-video -> skills -> .workbuddy -> 项目根
DEFAULT_ROOT = Path(__file__).resolve().parents[4]
STEM_RE = re.compile(r"^c(\d+)_test(\d+)_part(\d+)$", re.IGNORECASE)

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
SEARCH_MODEL = os.environ.get("TRANSCRIPT_SEARCH_MODEL", "deepseek-v4-pro")
EXTRACT_MODEL = os.environ.get("TRANSCRIPT_EXTRACT_MODEL", "deepseek-chat")

# 置信度闸门（可用环境变量覆盖，阈值依据见 references/05）
MIN_COVERAGE = float(os.environ.get("TRANSCRIPT_MIN_COVERAGE", "0.90"))
MIN_ASR_COVERAGE = float(os.environ.get("TRANSCRIPT_MIN_ASR_COVERAGE", "0.80"))
WARN_COVERAGE = float(os.environ.get("TRANSCRIPT_WARN_COVERAGE", "0.82"))
WARN_ASR_COVERAGE = float(os.environ.get("TRANSCRIPT_WARN_ASR_COVERAGE", "0.70"))

HTTP_TIMEOUT = int(os.environ.get("TRANSCRIPT_HTTP_TIMEOUT", "300"))

SYSTEM_HINT = """你是雅思听力资料整理助手。请给出完整、逐词的听力录音文本（tapescript），
包含录音里念出的读题/考试指令（例如 "You will hear..."、"First you have some time to look at..."、
"Before you hear the rest of the talk..."、"That is the end of Part..."）。
要求来源权威、正确：优先官方/主流培训机构整理稿，多来源交叉核对；
若无法确认逐词全文，明确说明并只给出可确认的部分，不要编造，不要凭记忆补全。
试卷上印刷的题目与选项、答案表、题目解析不属于录音文本，请不要输出。
"""

EXTRACT_SYSTEM = """你是雅思听力原文结构化抽取器。输入是一段 AI 联网检索得到的雅思听力资料，
里面通常混有：试卷题目与 A/B/C 选项、答案表、题目解析、来源说明、markdown 标题/加粗/表格。

你的任务：只抽出「录音里真正会说出来的文字」，按录音出现的先后顺序输出 JSON。

严格输出 JSON（不要任何解释文字），格式：
{"segments": [{"type": "narrator", "text": "..."}, {"type": "body", "text": "..."}]}

规则：
1. type="narrator"：录音里念出的考试指令/读题提示/结尾核对提示（如 "Part 2."、
   "You will hear a man called David..."、"Now listen carefully and answer questions 11 to 16."、
   "That is the end of Part 2. You now have 30 seconds to check your answers to Part 2."）。
   ANNOUNCER / NARRATOR 说的话都归此类。
2. type="body"：正文（讲座/对话）逐句。保留说话人标签并写在同一行，形如
   "DAVID: I never really planned to be a lifeboat volunteer."
   说话人标签必须大写英文 + 冒号。若原文没有标签，直接给句子。
3. **必须剔除**：试卷上印刷的题目题干、A/B/C/D 选项、题号列表、答案表、
   "Questions 11-16" 类版面文字、题目解析、来源/版权说明、"以下是…" 类导语、
   所有 markdown 标记（#、**、---、| 表格、粗体星号）。
4. text 里不要换行、不要 markdown、不要多余空格；每段一句或一句自然长句。
5. segments 必须严格按录音时间顺序排列：开头的 narrator 段在前，
   中间读题提示（若有）插在它实际出现的正文位置，结尾 narrator 段在最后。
   不要把全部指令堆到文件开头。
6. 若某段文本明显不是录音内容（如题目选项），宁可丢弃也不要保留。
"""

USER_PROMPT_TEMPLATE = "发我雅思剑{cam}听力部分的 test{t} part{p}的原文内容，包含读题内容。注意：要求来源权威、正确。"

SPEAKER_RE = re.compile(r"^\s*[A-Z][A-Z0-9 .'_/-]{0,24}:\s*")
NARRATOR_PREFIX_RE = re.compile(r"^\s*(ANNOUNCER|NARRATOR)\s*:\s*", re.IGNORECASE)


# --------------------------------------------------------------------------
# DeepSeek 调用
# --------------------------------------------------------------------------
def _headers() -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "未设置 DEEPSEEK_API_KEY。请先 source ~/.bash_profile（密钥绝不写入脚本/日志）。"
        )
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _post(path: str, payload: dict, timeout: int = HTTP_TIMEOUT) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    last_err = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code < 500 and e.code not in (429,):
                break
        except Exception as e:                       # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt == 0:
            time.sleep(3)
    raise RuntimeError(f"DeepSeek 请求失败（{path}）：{last_err}")


def build_prompt(cam: int, test: int, part: int) -> str:
    return USER_PROMPT_TEMPLATE.format(cam=cam, t=test, p=part)


def stage_a_search(prompt: str, effort: str) -> dict:
    """深度思考 + 服务端联网搜索，返回 {answer, sources, tool_use, raw}。"""
    payload = {
        "model": SEARCH_MODEL,
        "input": prompt,
        "instructions": SYSTEM_HINT,
        "reasoning": {"effort": effort},
        "tools": [{"type": "web_search"}],
        "max_output_tokens": 12000,
    }
    body = _post("/responses", payload)
    if body.get("status") not in (None, "completed", "incomplete"):
        raise RuntimeError(f"Responses API 状态异常：{body.get('status')} {body.get('error')}")

    answer_parts, sources, queries = [], [], []
    for item in body.get("output", []):
        typ = item.get("type")
        if typ == "message":
            answer_parts.append("".join(c.get("text", "") for c in item.get("content", [])))
        elif typ == "web_search_call":
            action = item.get("action") or {}
            if action.get("type") == "open_page" and action.get("url"):
                url = action["url"].split("#ws_call_id")[0].strip()
                if url:
                    sources.append(url)
            for q in action.get("queries", []) or []:
                if not str(q).startswith("ws_call_id="):
                    queries.append(q)

    answer = "\n".join(answer_parts).strip()
    if not answer:
        raise RuntimeError("联网检索未返回任何正文，禁止继续（不可凭空生成原文）")

    seen, uniq = set(), []
    for u in sources:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return {
        "answer": answer,
        "sources": uniq,
        "queries": queries,
        "tool_use": (body.get("usage") or {}).get("server_tool_use") or {},
        "searched": bool(uniq) or bool(queries),
        "raw": body,
    }


def stage_b_extract(answer: str) -> list[dict]:
    """把混杂的检索结果结构化成按录音顺序排列的 narrator/body 段。"""
    payload = {
        "model": EXTRACT_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": "请结构化抽取以下资料：\n\n" + answer},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    body = _post("/chat/completions", payload)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"结构化抽取未返回合法 JSON：{e} / {content[:200]}")
    segs = data.get("segments", [])
    clean = []
    for s in segs:
        typ = str(s.get("type", "")).lower()
        text = re.sub(r"\s+", " ", str(s.get("text", ""))).strip()
        text = text.replace("**", "").strip()
        if typ not in ("narrator", "body") or not text:
            continue
        clean.append({"type": typ, "text": text})
    if not clean:
        raise RuntimeError("结构化抽取结果为空，禁止继续")
    if not any(s["type"] == "body" for s in clean):
        raise RuntimeError("抽取结果没有任何正文段，禁止继续")
    return clean


def fallback_chat(prompt: str) -> dict:
    """兼容回退：v4-pro/web_search 不可用时用旧 chat 模型。
    注意：此路径【没有联网】，结果只可能是模型记忆，一律判 BLOCK 不落盘。
    """
    payload = {
        "model": EXTRACT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_HINT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    body = _post("/chat/completions", payload)
    answer = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if not answer:
        raise RuntimeError("回退模型也未返回内容")
    return {"answer": answer, "sources": [], "queries": [],
            "tool_use": {}, "searched": False, "raw": body}


# --------------------------------------------------------------------------
# 清洗与落盘
# --------------------------------------------------------------------------
def compose_md(segments: list[dict]) -> str:
    """narrator 段统一写成 NARRATOR: 前缀（会被 01_align.py 过滤，但保留供人核对）；
    body 段原样保留（含说话人标签）。顺序严格保持录音顺序。"""
    lines = []
    for s in segments:
        text = s["text"]
        if s["type"] == "narrator":
            text = NARRATOR_PREFIX_RE.sub("", text)
            lines.append(f"NARRATOR: {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + "\n"


def normalize(text: str) -> list[str]:
    """与 01_align.py 一致：小写、只留 a-z0-9' 和空格；并去掉行首说话人/指令标签。"""
    out = []
    for line in text.splitlines():
        line = NARRATOR_PREFIX_RE.sub("", line)
        line = SPEAKER_RE.sub("", line)
        out.append(line)
    # 先做 Unicode 分解去掉重音（Céide -> Ceide），否则 é 会被当成标点劈成 "c ide"，
    # 与 whisper 转写比对时刷出一片假差异。
    s = unicodedata.normalize("NFKD", " ".join(out))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9'\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().split()


def transcribe_asr(audio: Path) -> str:
    """用与 01_align.py 完全相同的 faster-whisper 配置转写音频，作为唯一真值源。"""
    from faster_whisper import WhisperModel
    import multiprocessing

    model_size = os.environ.get("WHISPER_MODEL", "small")
    n_threads = max(1, min(multiprocessing.cpu_count(), 4))
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=n_threads)
    segments, _ = model.transcribe(str(audio), language="en", word_timestamps=False)
    return " ".join(seg.text.strip() for seg in segments)


def cross_check(md: str, asr: str) -> dict:
    """双指标：md 词在音频里的覆盖率（抓混入的试卷题目）、音频词被 md 覆盖率（抓漏段）。"""
    md_words, asr_words = normalize(md), normalize(asr)
    if not md_words or not asr_words:
        return {"coverage": 0.0, "asr_coverage": 0.0, "verdict": "BLOCK",
                "md_words": len(md_words), "asr_words": len(asr_words), "diff": []}
    sm = difflib.SequenceMatcher(None, md_words, asr_words, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    coverage = matched / len(md_words)
    asr_coverage = matched / len(asr_words)

    diff, seen = [], set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (i2 - i1) + (j2 - j1) < 3:
            continue                       # 1~2 词差异多为 ASR 专有名词识别误差，不刷屏
        md_part = " ".join(md_words[i1:i2])[:120]
        key = (tag, md_part)
        if key in seen:
            continue                       # 同一处差异只报一次
        seen.add(key)
        diff.append({
            "kind": tag,
            "md": md_part,
            "audio": " ".join(asr_words[j1:j2])[:120],
        })
        if len(diff) >= 6:
            break

    if coverage >= MIN_COVERAGE and asr_coverage >= MIN_ASR_COVERAGE:
        verdict = "PASS"
    elif coverage >= WARN_COVERAGE and asr_coverage >= WARN_ASR_COVERAGE:
        verdict = "WARN"
    else:
        verdict = "BLOCK"
    return {"coverage": round(coverage, 4), "asr_coverage": round(asr_coverage, 4),
            "verdict": verdict, "md_words": len(md_words),
            "asr_words": len(asr_words), "diff": diff}


# --------------------------------------------------------------------------
# 缓存（调试/省钱用，一律写 /tmp，绝不进仓库）
# --------------------------------------------------------------------------
def cache_path(stem: str, suffix: str) -> Path:
    return Path("/tmp") / f"ielts_transcript_{stem}{suffix}"


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="音频 → 权威听力原文 md（联网检索 + 音频交叉校验）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="项目根目录")
    ap.add_argument("--stem", default=None, help="如 c19_test2_part2")
    ap.add_argument("--audio", default=None, help="音频绝对路径（自动解析 stem）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 md")
    ap.add_argument("--dry-run", action="store_true", help="打印预览与校验，不落盘")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    ap.add_argument("--skip-asr", action="store_true", help="跳过音频校验（仅调试，生产禁用）")
    ap.add_argument("--reuse-raw", action="store_true", help="复用 /tmp 里的检索与 ASR 缓存")
    ap.add_argument("--effort", default="high", choices=["low", "high", "max"])
    ap.add_argument("--segments-file", default=None,
                    help="外部联网来源已核过的结构化 segments JSON（阶段 A/B 兜底入口）；"
                         "仍强制跑阶段 C 音频交叉校验，禁止与 --skip-asr 同用")
    ap.add_argument("--source-url", action="append", default=[],
                    help="外部来源 URL，可重复；--segments-file 时至少 1 个，否则判 BLOCK")
    args = ap.parse_args()

    root = Path(args.root).resolve()

    stem = (args.stem or "").strip().lower()
    audio = Path(args.audio).resolve() if args.audio else None
    if audio and not stem:
        stem = audio.stem.lower()
    if not stem:
        print("[错误] 需 --stem 或 --audio", file=sys.stderr)
        return 3
    m = STEM_RE.fullmatch(stem)
    if not m:
        print(f"[错误] stem 不合规：{stem}（须 c19_test2_part2 形式）", file=sys.stderr)
        return 3
    cam, test, part = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if audio is None:
        audio = root / "resource" / f"{stem}.mp3"
    if not audio.is_file():
        print(f"[错误] 音频不存在：{audio}（无音频无法做交叉校验）", file=sys.stderr)
        return 3

    md_path = root / "resource" / f"{stem}.md"
    if md_path.is_file() and not args.force and not args.dry_run:
        print(f"[跳过] 原文已存在：{md_path}（需重取加 --force）", file=sys.stderr)
        return 0

    prompt = build_prompt(cam, test, part)
    raw_cache = cache_path(stem, "_raw.txt")
    stage_a_cache = cache_path(stem, "_stage_a.json")
    asr_cache = cache_path(stem, "_asr.txt")

    # ---- 阶段 A ----
    injected = bool(args.segments_file)
    if injected:
        if args.skip_asr:
            print("[错误] --segments-file 禁止与 --skip-asr 同用（必须做音频交叉校验）",
                  file=sys.stderr)
            return 3
        seg_path = Path(args.segments_file).expanduser().resolve()
        if not seg_path.is_file():
            print(f"[错误] 结构化原文文件不存在：{seg_path}", file=sys.stderr)
            return 3
        try:
            injected_segments = json.loads(seg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[错误] 结构化原文不是合法 JSON：{e}", file=sys.stderr)
            return 3
        injected_clean = []
        for s in injected_segments if isinstance(injected_segments, list) else []:
            typ = str(s.get("type", "")).lower()
            text = re.sub(r"\s+", " ", str(s.get("text", ""))).strip()
            if typ in ("narrator", "body") and text:
                injected_clean.append({"type": typ, "text": text})
        if not any(s["type"] == "body" for s in injected_clean):
            print("[错误] 结构化原文没有正文段，禁止继续", file=sys.stderr)
            return 3
        segments = injected_clean
        searched = bool(args.source_url)
        stage_a = {
            "answer": "\n".join(s["text"] for s in injected_clean),
            "sources": list(dict.fromkeys(args.source_url)),
            "queries": [], "tool_use": {}, "searched": searched, "raw": None,
        }
        print(f"[阶段A·兜底] 注入外部联网结构化原文 {seg_path}（{len(injected_clean)} 段，"
              f"{len(stage_a['sources'])} 个来源）", file=sys.stderr)
        print("           阶段 C 音频交叉校验照常执行，PASS 才落盘", file=sys.stderr)
    elif args.reuse_raw and stage_a_cache.is_file():
        stage_a = json.loads(stage_a_cache.read_text(encoding="utf-8"))
        searched = bool(stage_a.get("searched", True))
        print(f"[缓存] 复用检索结果 {stage_a_cache}（含来源清单）", file=sys.stderr)
    elif args.reuse_raw and raw_cache.is_file():
        print(f"[缓存] 复用检索答案 {raw_cache}（旧格式，无来源清单）", file=sys.stderr)
        stage_a = {"answer": raw_cache.read_text(encoding="utf-8"), "sources": [],
                   "queries": [], "tool_use": {}, "searched": True, "raw": None}
        searched = True
    else:
        print(f"[阶段A] 深度思考 + 联网检索（{SEARCH_MODEL}, effort={args.effort}）", file=sys.stderr)
        print(f"        提示词：{prompt}", file=sys.stderr)
        searched = True
        try:
            stage_a = stage_a_search(prompt, args.effort)
        except Exception as e:                       # noqa: BLE001
            print(f"[警告] 联网检索不可用，回退无联网模型：{e}", file=sys.stderr)
            try:
                stage_a = fallback_chat(prompt)
                searched = False
            except Exception as e2:                 # noqa: BLE001
                print(f"[错误] 回退也失败：{e2}", file=sys.stderr)
                return 1
        raw_cache.write_text(stage_a["answer"], encoding="utf-8")
        stage_a_cache.write_text(json.dumps({
            "answer": stage_a["answer"], "sources": stage_a["sources"],
            "queries": stage_a["queries"], "tool_use": stage_a["tool_use"],
            "searched": searched, "raw": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"        返回 {len(stage_a['answer'])} 字符，联网来源 {len(stage_a['sources'])} 个，"
          f"服务工具用量 {stage_a['tool_use']}", file=sys.stderr)

    # ---- 阶段 B ----
    seg_cache = cache_path(stem, "_segments.json")
    if injected:
        seg_cache.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[阶段B·兜底] 已注入的 segments 直接作为结构化结果（不做 LLM 改写）", file=sys.stderr)
    elif args.reuse_raw and seg_cache.is_file():
        segments = json.loads(seg_cache.read_text(encoding="utf-8"))
        print(f"[缓存] 复用结构化结果 {seg_cache}", file=sys.stderr)
    else:
        print("[阶段B] 结构化抽取（剔除试卷题目/答案/解析/markdown）", file=sys.stderr)
        try:
            segments = stage_b_extract(stage_a["answer"])
        except Exception as e:                   # noqa: BLE001
            print(f"[错误] 结构化抽取失败：{e}", file=sys.stderr)
            return 1
        seg_cache.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    n_narr = sum(1 for s in segments if s["type"] == "narrator")
    n_body = len(segments) - n_narr
    md = compose_md(segments)
    print(f"        得到 {n_narr} 条读题/指令段、{n_body} 条正文段", file=sys.stderr)

    # ---- 阶段 C ----
    if args.skip_asr:
        print("[警告] --skip-asr：未做音频交叉校验，结果不可信（生产禁用）", file=sys.stderr)
        check = {"coverage": 0.0, "asr_coverage": 0.0, "verdict": "WARN",
                 "md_words": 0, "asr_words": 0, "diff": [], "skipped": True}
    else:
        print(f"[阶段C] faster-whisper 转写音频做双源交叉校验（{audio.name}）", file=sys.stderr)
        try:
            if args.reuse_raw and asr_cache.is_file():
                asr = asr_cache.read_text(encoding="utf-8")
                print(f"        复用 ASR 缓存 {asr_cache}", file=sys.stderr)
            else:
                asr = transcribe_asr(audio)
                asr_cache.write_text(asr, encoding="utf-8")
            check = cross_check(md, asr)
        except Exception as e:                   # noqa: BLE001
            print(f"[错误] 音频交叉校验失败：{e}", file=sys.stderr)
            return 1
        print(f"        coverage={check['coverage']:.3f}  "
              f"asr_coverage={check['asr_coverage']:.3f}  → {check['verdict']}", file=sys.stderr)

    if not searched:
        check["verdict"] = "BLOCK"
        check.setdefault("diff", []).insert(0, {
            "kind": "no_web_search", "md": "", "audio": "",
        })
        print("[BLOCK] 本次未联网，结果仅为模型记忆，不可作为对齐原文", file=sys.stderr)

    report = {
        "stem": stem, "prompt": prompt,
        "model": "agent web_fetch (external sources)" if injected else SEARCH_MODEL,
        "extract_model": "deterministic segments injection" if injected else EXTRACT_MODEL,
        "injected": injected,
        "searched": searched, "sources": stage_a["sources"],
        "queries": stage_a["queries"], "tool_use": stage_a["tool_use"],
        "raw_chars": len(stage_a["answer"]), "narrator_lines": n_narr,
        "body_lines": n_body, "check": check,
        "md_path": str(md_path), "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    report_path = cache_path(stem, ".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = check["verdict"]
    if verdict != "BLOCK" and not args.dry_run:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
        print(f"[落盘] {md_path}", file=sys.stderr)

    if args.json:
        print(json.dumps({"stem": stem, "verdict": verdict,
                          "coverage": check["coverage"],
                          "asr_coverage": check["asr_coverage"],
                          "narrator_lines": n_narr, "body_lines": n_body,
                          "sources": len(stage_a["sources"]),
                          "md_path": str(md_path),
                          "written": verdict != "BLOCK" and not args.dry_run,
                          "report": str(report_path)}, ensure_ascii=False, indent=2))
    else:
        print("\n===== 原文预览（前 25 行）=====")
        for line in md.splitlines()[:25]:
            print("  " + line[:160])
        print(f"  ... 共 {len(md.splitlines())} 行")
        print("\n===== 联网来源 =====")
        for u in stage_a["sources"][:10]:
            print("  - " + u)
        if check.get("diff"):
            print("\n===== 主要差异（前 6 处，供人工复核）=====")
            for d in check["diff"]:
                print(f"  [{d['kind']}] md: {d.get('md','')} | 音频: {d.get('audio','')}")
        print(f"\n===== 结论：{verdict} =====")
        print(f"  报告：{report_path}")
        if verdict == "BLOCK":
            print("  不得落盘、不得进入出片。请人工提供权威原文，或调整阈值后复查。")
        elif verdict == "WARN":
            print("  已落盘但置信度偏低，出片前必须向用户展示差异并取得确认。")

    if verdict == "BLOCK":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

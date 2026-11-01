#!/usr/bin/env python3
"""
步骤2：LLM 标注（中文翻译 + 考点词标注）

对每句英文原文，调用 DeepSeek 输出：
- translation: 中文翻译
- highlights: 重点词/短语/易听错词列表（用于第1遍挖空、第2遍高亮）

输出 annotated.json，结构与 sentences.json 对齐。

考点词标注原则（写给模型）：
- 关键词汇、数字、地名、人名、连读/弱读易听错的短语
- 每个句子标注 1-5 个考点片段
"""

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

API_KEY = os.environ.get("DEEPSEEK_API_KEY")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# 安全：API Key 必须通过环境变量提供，禁止硬编码在源码中。
if not API_KEY:
    raise SystemExit(
        "[错误] 未设置 DEEPSEEK_API_KEY 环境变量。\n"
        "  请先执行：export DEEPSEEK_API_KEY=\"sk-你的密钥\"\n"
        "  （密钥属于敏感信息，请勿写入源码或提交到 Git 仓库）"
    )

SENTENCES = os.environ.get("SENTENCES", "ielts-video/data/sentences.json")
OUT = os.environ.get("OUT", "ielts-video/data/annotated.json")

SYSTEM_PROMPT = """你是雅思听力教学专家。对给定的英文句子做教学标注。

对每个句子，你需要：
1. translation：准确、自然的中文翻译。
2. highlights：从句子中挑出 1~5 个"考点片段"，这些是听力中容易听错、易考、或值得记忆的内容，包括：
   - 数字、日期、金额、地名、人名、专有名词
   - 重点实义词、固定搭配、短语动词
   - 连读/弱读/吞音导致易听错的词或短语
   每个 highlight 必须原样出现在原句中（是原句的连续子串），返回时保持原句中的大小写和形式。

严格只输出 JSON，不要输出任何解释或多余文字。JSON 格式：
{"items": [{"translation": "中文翻译", "highlights": ["考点1", "考点2"]}]}"""


def load_sentences(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def annotate(sentences):
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    annotated = []
    matched_sents = [s for s in sentences if s.get("matched")]

    # 分批处理，每批若干句
    BATCH = 10
    for i in range(0, len(matched_sents), BATCH):
        batch = matched_sents[i:i + BATCH]
        lines = "\n".join(f"{j+1}. {s['text']}" for j, s in enumerate(batch))
        user_msg = f"请标注以下 {len(batch)} 个英文句子：\n\n{lines}\n\n请按顺序返回 items 数组，与输入顺序一一对应。"

        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        try:
            data = json.loads(content)
            items = data.get("items", [])
        except json.JSONDecodeError as e:
            print(f"  [警告] 批次 {i} JSON 解析失败：{e}\n  {content[:200]}")
            items = [{"translation": "", "highlights": []} for _ in batch]

        for s, it in zip(batch, items):
            annotated.append({
                "index": s["index"],
                "text": s["text"],
                "start": s["start"],
                "end": s["end"],
                "words": s["words"],
                "translation": it.get("translation", ""),
                "highlights": it.get("highlights", []),
            })
        print(f"  [进度] 已标注 {min(i+BATCH, len(matched_sents))}/{len(matched_sents)} 句")

    # 未匹配的句子也补进去（无时间戳，不参与成片）
    matched_idx = {s["index"] for s in annotated}
    for s in sentences:
        if s["index"] not in matched_idx:
            annotated.append({
                "index": s["index"],
                "text": s["text"],
                "start": None,
                "end": None,
                "words": [],
                "translation": "",
                "highlights": [],
            })
    annotated.sort(key=lambda x: x["index"])
    return annotated


def main():
    sentences = load_sentences(SENTENCES)
    print(f"待标注句子：{sum(1 for s in sentences if s.get('matched'))} 句")
    annotated = annotate(sentences)
    out_path = Path(OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT}")


if __name__ == "__main__":
    main()

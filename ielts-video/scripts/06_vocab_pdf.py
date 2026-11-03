#!/usr/bin/env python3
"""
步骤6（可选）：考点词表 PDF 生成器

读取 annotated.json（步骤2产物），自动生成该篇的《考点词表》PDF，
作为 B 站视频简介/关注自动回复的引流资料，零边际成本随片产出。

内容结构：
- 封面头：素材名 + 关注引导
- 考点条目：考点词（自动打类型标签）+ 原句（考点用【】标出）+ 中文翻译
- 结尾：三遍法自查清单 + 7 天打卡表

用法（遵循流水线环境变量约定）：
  export ANNOTATED="ielts-video/data/annotated.json"
  export MATERIAL_NAME="剑19 Test1 Part2"
  export VIDEO_REF=""                        # 发布后回填 BV 号，可留空
  export OUT_VOCAB_PDF="ielts-video/out/c19_test1_part2_vocab.pdf"
  python3 ielts-video/scripts/06_vocab_pdf.py

依赖：reportlab（仅本步骤使用，不影响主流程）
"""

import json
import os
import re
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

# ---------- 环境变量配置 ----------
ANNOTATED = os.environ.get("ANNOTATED", "ielts-video/data/annotated.json")
OUT_PDF = os.environ.get("OUT_VOCAB_PDF", "ielts-video/out/vocab.pdf")
MATERIAL_NAME = os.environ.get("MATERIAL_NAME", "雅思听力精听素材")
VIDEO_REF = os.environ.get("VIDEO_REF", "")  # BV 号，发布后回填
CHANNEL_NAME = "雅思精听系统训练"

# ---------- 视觉规范（与封面模板「学术蓝」同体系）----------
C_PRIMARY = HexColor("#1B2A4A")   # 深海军蓝：标题/条目编号
C_ACCENT = HexColor("#B8860B")    # 暗金：考点词（打印友好，荧光黄打印看不清）
C_TEXT = HexColor("#202020")
C_GREY = HexColor("#606060")
C_LINE = HexColor("#D8D8D8")
C_TAG = HexColor("#EEF2F9")       # 标签底色

FONT_CN = "STSong-Light"          # 内置 CID 中文字体，无需字体文件
FONT_EN = "Helvetica-Bold"

pdfmetrics.registerFont(UnicodeCIDFont(FONT_CN))

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def classify(highlight: str) -> str:
    """考点类型启发式标签（不调用 LLM，保持零成本）。"""
    h = highlight.strip()
    if re.search(r"\d", h):
        return "数字/时间"
    if re.fullmatch(r"[A-Z]{2,}", h):
        return "缩写"
    words = h.split()
    if len(words) > 1:
        if all(w[:1].isupper() for w in words):
            return "专有名词"
        return "固定搭配"
    if h[:1].isupper():
        return "专有名词"
    return "核心词汇"


def load_entries(path: str):
    """从 annotated.json 提取 (考点, 原句, 译文)，去重并保持语序。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = []
    seen = set()
    for sent in data:
        if not sent.get("matched", True):
            continue
        text = sent.get("text", "").strip()
        translation = sent.get("translation", "").strip()
        for h in sent.get("highlights", []):
            h = h.strip()
            key = h.lower()
            if not h or key in seen or h.lower() not in text.lower():
                continue
            seen.add(key)
            entries.append({"highlight": h, "text": text, "translation": translation})
    return entries


def mark_in_sentence(text: str, highlight: str) -> str:
    """把考点在原句中用【】标出（大小写不敏感，只标第一处）。"""
    idx = text.lower().find(highlight.lower())
    if idx < 0:
        return text
    return text[:idx] + "【" + text[idx:idx + len(highlight)] + "】" + text[idx + len(highlight):]


class VocabPDF:
    def __init__(self, path: str):
        self.c = canvas.Canvas(path, pagesize=A4)
        self.c.setTitle(f"{MATERIAL_NAME} 考点词表")
        self.y = PAGE_H - MARGIN
        self.page = 1

    # ---------- 基础工具 ----------
    def footer(self):
        self.c.setFont(FONT_CN, 8)
        self.c.setFillColor(C_GREY)
        self.c.drawCentredString(
            PAGE_W / 2, 10 * mm,
            f"关注 B 站「{CHANNEL_NAME}」领取全套考点词表    ｜    第 {self.page} 页",
        )

    def new_page(self):
        self.footer()
        self.c.showPage()
        self.page += 1
        self.y = PAGE_H - MARGIN

    def need(self, height: float):
        if self.y - height < MARGIN:
            self.new_page()

    def wrap(self, text: str, font: str, size: float, width: float):
        """按宽度折行（中英文混排，逐字符测量）。"""
        lines, cur = [], ""
        for ch in text:
            if pdfmetrics.stringWidth(cur + ch, font, size) <= width:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [""]

    def paragraph(self, text: str, font: str, size: float, color, indent=0.0,
                  leading=None, gap_after=1.5 * mm):
        leading = leading or size * 1.45
        lines = self.wrap(text, font, size, CONTENT_W - indent)
        self.need(len(lines) * leading + gap_after)
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        for ln in lines:
            self.c.drawString(MARGIN + indent, self.y - size, ln)
            self.y -= leading
        self.y -= gap_after

    # ---------- 页面区块 ----------
    def header(self):
        # 顶部色条
        self.c.setFillColor(C_PRIMARY)
        self.c.rect(0, PAGE_H - 8 * mm, PAGE_W, 8 * mm, stroke=0, fill=1)
        self.y = PAGE_H - 20 * mm

        self.c.setFillColor(C_PRIMARY)
        self.c.setFont(FONT_CN, 20)
        self.c.drawString(MARGIN, self.y - 20, f"{MATERIAL_NAME} 考点词表")
        self.y -= 32

        self.c.setFont(FONT_CN, 9.5)
        self.c.setFillColor(C_GREY)
        ref = VIDEO_REF or "（视频发布后回填 BV 号）"
        self.c.drawString(MARGIN, self.y - 9, f"配套视频：{ref}    |    B 站搜索：{CHANNEL_NAME}")
        self.y -= 16

        self.c.setStrokeColor(C_LINE)
        self.c.setLineWidth(0.8)
        self.c.line(MARGIN, self.y, PAGE_W - MARGIN, self.y)
        self.y -= 6 * mm

    def entry(self, idx: int, e: dict):
        h = e["highlight"]
        tag = classify(h)
        line_h = 8 * mm

        # 先估算整条高度，避免条目跨页断裂
        sent_lines = self.wrap(mark_in_sentence(e["text"], h), FONT_CN, 10.5, CONTENT_W - 14 * mm)
        tran_lines = self.wrap(e["translation"], FONT_CN, 9.5, CONTENT_W - 14 * mm)
        total = line_h + len(sent_lines) * 15 + len(tran_lines) * 13.5 + 6 * mm
        self.need(total)

        # 编号
        self.c.setFillColor(C_PRIMARY)
        self.c.setFont(FONT_EN, 12)
        self.c.drawString(MARGIN, self.y - 12, f"{idx:02d}")

        # 考点词
        self.c.setFillColor(C_ACCENT)
        self.c.setFont(FONT_EN, 12)
        self.c.drawString(MARGIN + 12 * mm, self.y - 12, h)

        # 类型标签
        tw = pdfmetrics.stringWidth(tag, FONT_CN, 8) + 5 * mm
        tx = PAGE_W - MARGIN - tw
        self.c.setFillColor(C_TAG)
        self.c.roundRect(tx, self.y - 15, tw, 5.5 * mm, 2 * mm, stroke=0, fill=1)
        self.c.setFillColor(C_PRIMARY)
        self.c.setFont(FONT_CN, 8)
        self.c.drawString(tx + 2.5 * mm, self.y - 11.5, tag)
        self.y -= line_h

        # 原句（考点用【】标出）
        self.paragraph("原句  " + mark_in_sentence(e["text"], h),
                       FONT_CN, 10.5, C_TEXT, indent=12 * mm, leading=15, gap_after=1 * mm)
        # 译文
        self.paragraph("译文  " + e["translation"],
                       FONT_CN, 9.5, C_GREY, indent=12 * mm, leading=13.5, gap_after=2 * mm)

        # 分隔线
        self.need(4 * mm)
        self.c.setStrokeColor(C_LINE)
        self.c.setLineWidth(0.4)
        self.c.line(MARGIN + 12 * mm, self.y, PAGE_W - MARGIN, self.y)
        self.y -= 4 * mm

    def tail_section(self):
        """结尾：三遍法自查清单 + 7 天打卡表。"""
        self.need(90 * mm)

        self.c.setFillColor(C_PRIMARY)
        self.c.setFont(FONT_CN, 14)
        self.c.drawString(MARGIN, self.y - 14, "三遍法自查清单")
        self.y -= 24

        checklist = [
            "第 1 遍｜挖空回忆：不看原文，把空格全部写满（猜也要写）",
            "第 2 遍｜考点高亮：核对差异，分清「没听出来」还是「不认识」",
            "第 3 遍｜双语对照：确认理解语义，留意同义替换",
        ]
        self.c.setFont(FONT_CN, 10.5)
        for item in checklist:
            self.c.setFillColor(C_TEXT)
            self.c.drawString(MARGIN + 2 * mm, self.y - 10, item)
            self.c.setStrokeColor(C_PRIMARY)
            self.c.setLineWidth(0.8)
            self.c.rect(PAGE_W - MARGIN - 5 * mm, self.y - 12, 4 * mm, 4 * mm, stroke=1, fill=0)
            self.y -= 7.5 * mm
        self.y -= 6 * mm

        # 7 天打卡表
        self.c.setFillColor(C_PRIMARY)
        self.c.setFont(FONT_CN, 14)
        self.c.drawString(MARGIN, self.y - 14, "7 天打卡表（建议打印）")
        self.y -= 24

        col_w = CONTENT_W / 7
        row_h = 12 * mm
        self.c.setFont(FONT_CN, 9)
        for row in range(2):
            for col in range(7):
                x = MARGIN + col * col_w
                y0 = self.y - (row + 1) * row_h
                self.c.setStrokeColor(C_LINE)
                self.c.setLineWidth(0.6)
                self.c.rect(x, y0, col_w, row_h, stroke=1, fill=0)
                if row == 0:
                    self.c.setFillColor(C_GREY)
                    self.c.drawCentredString(x + col_w / 2, y0 + row_h - 5 * mm, f"Day {col + 1}")
        self.y -= 2 * row_h + 6 * mm

        self.c.setFont(FONT_CN, 9)
        self.c.setFillColor(C_GREY)
        self.c.drawString(MARGIN, self.y - 9,
                          "使用方法：每天 20 分钟，完成一篇精听后在对应格子打勾。同一篇材料隔 3 天回听一次。")

    def build(self, entries):
        self.header()
        self.c.setFont(FONT_CN, 10)
        self.c.setFillColor(C_GREY)
        self.c.drawString(MARGIN, self.y - 10, f"本篇共 {len(entries)} 个考点，按原文出现顺序排列。")
        self.y -= 8 * mm
        for i, e in enumerate(entries, 1):
            self.entry(i, e)
        self.tail_section()
        self.footer()
        self.c.save()


def main():
    if not Path(ANNOTATED).exists():
        raise SystemExit(f"[错误] 找不到标注文件：{ANNOTATED}\n  请先运行流水线步骤2（02_annotate.py）")

    entries = load_entries(ANNOTATED)
    if not entries:
        raise SystemExit("[错误] annotated.json 中没有可用考点（highlights 为空或均未匹配）")

    Path(OUT_PDF).parent.mkdir(parents=True, exist_ok=True)
    VocabPDF(OUT_PDF).build(entries)

    size_kb = Path(OUT_PDF).stat().st_size / 1024
    print(f"✅ 考点词表已生成：{OUT_PDF}")
    print(f"   素材：{MATERIAL_NAME} | 考点 {len(entries)} 个 | 文件 {size_kb:.1f} KB")
    if not VIDEO_REF:
        print("   ⚠️  VIDEO_REF 未设置，发布后请回填 BV 号重新生成")


if __name__ == "__main__":
    main()

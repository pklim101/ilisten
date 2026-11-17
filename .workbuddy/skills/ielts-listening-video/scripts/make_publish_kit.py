#!/usr/bin/env python3
"""阶段 6（默认）：生成【手动发布交付包】+ 投稿物料 JSON。

2026-11-09 v2.8.0 改造（用户决定）：agent 不再代填 B站表单。浏览器草稿链路
（`--adapter browser-use`）降级为**显式可选**，默认 `--adapter manual`，
只把一个可直接手动发布的目录交给用户，零浏览器、零确认闸门、零填表。

产物：
  1. `/tmp/ielts_publish_<stem>.json` —— 机器读的统一入参（唯一真值源，保留）
  2. `ielts-video/publish/<stem>/` —— 给人用的交付包：
     `01_视频.mp4`（硬链接，不额外占空间）/ `02_封面.png` / `03_考点词表.pdf` /
     `04_置顶评论.txt` / `发布清单.md`（全部字段 + 简介全文 + 发布前自检 8 项 + 上线后待办）

本脚本只「准备」，绝不执行投稿、绝不打开浏览器。发布动作由账号本人完成。

用法：
  make_publish_kit.py --root <ROOT> --stem c19_test2_part3
  make_publish_kit.py --root <ROOT> --stem c19_test2_part3 \
      --description-file <简介.txt> --comment-file <置顶评论.txt>
  make_publish_kit.py --root <ROOT> --stem c19_test2_part3 \
      --adapter browser-use --no-bundle   # 仅当用户明确要求走草稿链路

退出码：0 正常；3 = 参数/成片问题。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[4]
STEM_RE = re.compile(r"^c(\d+)_test(\d+)_part(\d+)$", re.IGNORECASE)

# docs/06 第二节：标题主公式。全系列只换编号，格式锁死。
TITLE_TEMPLATE = "【剑{cam} Test{t} Part{p}】雅思听力逐句精听｜三遍挖空训练｜考点词+双语字幕"

# docs/05 封面文案：不同 Part 用不同人群钩子
PART_HOOK = {
    1: "生活对话入门 · 数字/拼写专项",
    2: "连读重灾区 · 地图与匹配题",
    3: "多人对话易翻车 · 同义替换",
    4: "学术讲座冲7+ · 长句笔记法",
}

# 2026-11-09 part3 实测：B站把「雅思备考 / 学习打卡」判为**话题专用 tag**，
# 手动输入会提示「当前tag为话题专用，不允许自定义添加」；「雅思听力」同样受限，
# 但可从投稿页「推荐标签」里点选添加。模板改用等价且可稳定添加的词，避免每篇返工。
TAGS = [
    "雅思", "剑桥雅思", "精听", "雅思听力", "英语听力",
    "听力训练", "英语学习", "剑{cam}", "IELTS", "听力真题",
]

# B站知识区投稿建议分区（以创作中心实际分区为准，不硬编码 tid）
CATEGORY = "知识区 → 校园学习（语言学习 / 考试备考方向）"

# 仅作储备：B站「被关注回复」需 1000 粉（2026-11-09 实测保存返回「权限不足」）。
# 未达标前该关键词绝不写进简介/评论，也不作为领取入口。
AUTO_REPLY_KEYWORD_TEMPLATE = "剑{cam}T{t}P{p}"
AUTO_REPLY_GATE_NOTE = "disabled：需 1000 粉丝（2026-11-09 实测保存返回「权限不足」），未达标不得启用"

COVER_MIN = 10_000  # 封面 PNG 最小字节数，低于此视为生成失败/占位图

# —— v2.8.0：手动发布交付包（默认路径，与 out/ 隔离，绝不污染素材扫描域）——
BUNDLE_PARENT = ("ielts-video", "publish")
BUNDLE_VIDEO = "01_视频.mp4"
BUNDLE_COVER = "02_封面.png"
BUNDLE_PDF = "03_考点词表.pdf"
BUNDLE_COMMENT = "04_置顶评论.txt"
BUNDLE_MD = "发布清单.md"


def build_comment(root: Path, cam, t, p, limit: int = 8) -> tuple[str, str]:
    """通用置顶评论：只用 annotated.json 的真实 highlights，**绝不编分段时间**。

    需要带成片分段时间轴的单篇特化版，用 `--comment-file` 传入已核对过的文本
    （时间必须取 data/timeline.json 的真实边界，禁止整分整秒地编）。
    """
    path = root / "ielts-video" / "data" / "annotated.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        items = []
    seen: set[str] = set()
    picked: list[tuple[int, str]] = []
    for s in items if isinstance(items, list) else []:
        for h in (s.get("highlights") or []):
            word = str(h).strip()
            if word and word not in seen:
                seen.add(word)
                picked.append((int(s.get("index", 0)) + 1, word))
                break                      # 每句最多取 1 条，避免全挤在同一句
        if len(picked) >= limit:
            break
    lines = [
        f"【剑{cam} Test{t} Part{p} · 精听自查】每句自动循环 3 遍，不用自己拖进度条。",
        "第 1 遍挖空先自己填 → 第 2 遍对照高亮字幕标出听漏处 → 第 3 遍双语确认。",
        "",
        "【本期考点速览】完整逐句考点都在视频第 2 遍高亮字幕里",
    ]
    if picked:
        for n, word in picked:
            lines.append(f"句{n}    {word}")
    else:
        lines.append("（未读到本篇 highlights，请先核对 data/annotated.json 再发这条评论）")
    lines += [
        "",
        "说明：「句 N」是原文句子顺序，仅用于对照高亮字幕，不是跳转时间。",
        "弹幕点：你最常漏哪一类？① 数字/拼写 ② 专有名词 ③ 连读吞音 ④ 同义替换",
        "评论区告诉我错在哪一句，高频问题会单独出一期精讲。",
    ]
    return "\n".join(lines), (
        f"自动生成：取自 data/annotated.json 的真实 highlights（{len(picked)} 条，无编造）；"
        "通用版不含分段时间轴"
    )


def build_checklist(kit: dict, comment: str, comment_source: str, files: dict) -> str:
    """给人看的发布清单：字段 + 全文文案 + 发布前自检 8 项 + 上线后待办。"""
    stem, title, desc = kit["stem"], kit["title"], kit["description"]
    tags, category, collection = kit["tags"], kit["category"], kit["collection"]
    pub = kit.get("suggested_publish", {})
    bdir = files["bundle_dir"]
    L: list[str] = [
        f"# B站手动发布清单 · {stem}",
        "",
        "> 本目录由 `make_publish_kit.py` 生成。agent 不填表、不存草稿、不发布；",
        "> 上传与发布全部由账号本人完成。发布前请按下方「自检 8 项」逐条核对。",
        "",
        "## 一、目录里有什么",
        "",
        "| 文件 | 用途 |",
        "|---|---|",
    ]
    for name, use in (
        (BUNDLE_VIDEO, "上传 B站的成片（硬链接到 out/ 原片，不额外占磁盘）"),
        (BUNDLE_COVER, "**必传**的封面图，别用 B站自动抽帧"),
        (BUNDLE_PDF, "本地复盘用考点词表，**不上传 B站**、不在站内承诺分发"),
        (BUNDLE_COMMENT, "上线后发并置顶的评论（兑现简介里的「速览见置顶评论」）"),
        (BUNDLE_MD, "本清单"),
    ):
        f = files.get(name)
        L.append(f"| `{name}` | {use}{'' if f else ' — **【缺失】请先补生成**'} |")
    L += ["", "## 二、投稿页要填的字段（复制粘贴即可）", "",
          "| 字段 | 值 |", "|---|---|",
          f"| 标题（{len(title)} 字 / 上限 80） | `{title}` |",
          f"| 分区 | `{category}`（**以创作中心实际分区名为准**；实测为一级「知识」） |",
          f"| 标签（{len(tags)} 个 / 上限 10） | `{' / '.join(tags)}` |",
          f"| 简介（{len(desc)} 字 / 上限 2000） | 见第三节全文 |",
          f"| 封面 | `{files.get(BUNDLE_COVER) or '【缺失】'}` |",
          f"| 合集（可选） | `{collection}`；**电磁力 ≥ Lv2 才开通**，未开通时整项跳过，别为此卡住投稿 |",
          "| 创作声明 | **含AI生成内容**（画面/字幕由流水线生成，简介已写「AI 辅助制作」） |",
          f"| 定时发布 | 由你决定；建议 {pub.get('window', '工作日 18:00–20:00 / 周末 14:00–16:00')}，"
          f"下次 {pub.get('next', '（见物料 JSON）')}。**别误开定时开关** |",
          "| 自动回复 | 禁用：需 1000 粉，实测保存返回「权限不足」；简介/评论不得承诺自动领取 |",
          "",
          "## 三、简介全文（整段粘贴，结尾版权说明别截断）", "", "```text", desc, "```", "",
          "## 四、置顶评论全文（**上线后**发并置顶）", "",
          f"> 来源：{comment_source}", "", "```text", comment, "```", "",
          "## 五、发布前自检 8 项（2026-11-09 part3 填表实测）", "",
          "1. **分区**：系统默认预置「三农」→ 必须改成「知识」。",
          "2. **预置标签**：默认带「生活记录 / 学习 / 记录」→ 聚焦输入框按 3 次 Backspace 清空，",
          f"   再逐个填上面 {len(tags)} 个目标标签；**每次回车后焦点会丢失**，下一条要重新点输入框。",
          "3. **话题专用 tag**：「雅思听力 / 雅思备考 / 学习打卡」手动输入会被拒",
          "   （提示「不允许自定义添加」），「雅思听力」要从推荐标签点选，另两个用 `IELTS` / `听力真题` 补位。",
          f"4. **封面**：系统会自动抽帧（part3 抽到「旧鞋 真相」）→ 必须上传 `{BUNDLE_COVER}`，",
          "   上传入口在「封面制作」弹窗**底部**，要滚到底才看得见。",
          "5. **创作声明**：默认「内容无需标注」→ 改「含AI生成内容」。",
          "6. **简介**：整段粘贴后核对开头与结尾，结尾必须保留",
          "   「听力素材版权归原出版方所有；如有版权方提出异议，会第一时间下架处理」。",
          "7. **定时开关**：确认是否误开；若用定时，记下时间并**上线后再发置顶评论**。",
          f"8. **合集（可选）**：账号权益已开通才选 `{collection}`；未开通就跳过，用标题前缀 + 简介系列导航代替。",
          "   合集可事后在「内容管理 → 合集管理」补加（一次最多 50 个视频），不影响投稿；",
          f"   同时确认上传的是 `{BUNDLE_VIDEO}` 而不是旧片。",
          "",
          "## 六、发布后待办", "",
          f"1. 视频上线后**尽快**发 `{BUNDLE_COMMENT}` 并置顶（简介的承诺靠这条评论兑现）。",
          "   简介写「速览见置顶评论」，上线到评论发出之间别拖太久。",
          "2. 复核线上简介是否被折叠/截断、封面是否为真图、分区与标签是否正确；",
          "   字段有错走「稿件管理 → 编辑」修正，**不删稿重投**（丢播放/互动）。",
          ("3. 本篇 BV 已分配：`" + str(kit.get("bvid")) + "`；若 PDF 还没回填，只重跑第 6 步："
           if kit.get("bvid") else
           "3. 拿到 BV 号后回填 PDF（**只重跑第 6 步，不重跑整条流水线**）："),
          f"   先 `cp ielts-video/data/annotated.json /tmp/annotated_{stem}_backup.json`，",
          ("   再 `VIDEO_REF=" + str(kit.get("bvid")) + " OUT_VOCAB_PDF=... python3 ielts-video/scripts/06_vocab_pdf.py`。"
           if kit.get("bvid") else
           "   再 `VIDEO_REF=BVxxx OUT_VOCAB_PDF=... python3 ielts-video/scripts/06_vocab_pdf.py`。"),
          "4. 48 小时复盘：完播率、三连率、涨粉归因（docs/04 北极星指标）。",
          "",
          "## 七、红线", "",
          "- 不挂微信 / QQ群 / 公众号，不站外引流；不刷量、不互刷、不标题党。",
          "- 不写「自动领取 / 发送关键词 / 私信领取 / 关注后回复」（自动回复需 1000 粉）。",
          "- 不写「专栏 / 置顶动态 / 网盘 / 链接领取」等需另行发布的载体词；资料价值只锚定",
          "  **视频第 2 遍高亮字幕 + 置顶评论速览**，发一条评论即兑现。",
          "- 真题音频有版权风险：保留简介里的版权说明与下架承诺；版权方提出异议立即主动下架。",
          "",
          f"物料真值源：`/tmp/ielts_publish_{stem}.json`｜交付包目录：`{bdir}`",
          "",
    ]
    return "\n".join(L)


def write_bundle(root: Path, kit: dict, comment: str, comment_source: str) -> Path:
    """把成片/封面/PDF 用硬链接放进交付包（不额外占磁盘），再写文案与清单。"""
    bdir = root.joinpath(*BUNDLE_PARENT, kit["stem"])
    bdir.mkdir(parents=True, exist_ok=True)

    def place(src_value: str | None, name: str) -> Path | None:
        if not src_value:
            return None
        src = Path(src_value)
        if not src.is_file():
            return None
        dst = bdir / name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            os.link(src, dst)              # 同分区硬链接：零额外空间，Finder 可直接选
        except OSError:
            shutil.copy2(src, dst)         # 跨分区/文件系统不支持时退回复制
        return dst

    files: dict = {"bundle_dir": bdir}
    files[BUNDLE_VIDEO] = place(kit.get("video"), BUNDLE_VIDEO)
    files[BUNDLE_COVER] = place(kit.get("cover_image"), BUNDLE_COVER)
    files[BUNDLE_PDF] = place(kit.get("vocab_pdf"), BUNDLE_PDF)
    files[BUNDLE_COMMENT] = bdir / BUNDLE_COMMENT
    files[BUNDLE_MD] = bdir / BUNDLE_MD
    files[BUNDLE_COMMENT].write_text(comment.strip() + "\n", encoding="utf-8")
    files[BUNDLE_MD].write_text(
        build_checklist(kit, comment.strip(), comment_source, files), encoding="utf-8"
    )
    return bdir


def cover_spec(stem: str) -> dict:
    """docs/05 封面文案唯一真值源，出片与投稿两条链路共用。"""
    m = STEM_RE.fullmatch(stem)
    if not m:
        raise ValueError(f"stem 不合规：{stem}")
    cam, t, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return {
        "main_text": f"剑{cam} Test {t} Part {p}",
        "sub_text": PART_HOOK.get(p, "逐句精听"),
        "flow_bar": "挖空回忆 → 考点高亮 → 双语理解",
        "palette": "学术蓝 A（背景：assets/background.png 成片同款）",
    }


def ensure_cover(root: Path, stem: str) -> str | None:
    """投稿物料生成时确保有一张真封面；缺失就调用 make_cover.py 补生成。

    绝不生成占位图：生成失败返回 None，由 publish_browser_use.py 闸门①拦下。
    """
    cover_png = root / "ielts-video" / "out" / f"{stem}_cover.png"
    if cover_png.is_file() and cover_png.stat().st_size >= COVER_MIN:
        return str(cover_png)
    maker = Path(__file__).resolve().parent / "make_cover.py"
    try:
        r = subprocess.run(
            [sys.executable, str(maker), "--stem", stem, "--out", str(cover_png)],
            capture_output=True, text=True, timeout=90,
        )
        if r.returncode != 0:
            print(f"[WARN] 封面生成失败：{r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[WARN] 封面生成异常：{e}", file=sys.stderr)
    if cover_png.is_file() and cover_png.stat().st_size >= COVER_MIN:
        return str(cover_png)
    return None


def probe_duration(mp4: Path, timeline: Path) -> float | None:
    """优先 ffprobe 读成片时长；失败回退最近一次 timeline.json。"""
    candidates = ["ffprobe", "/Users/sofia/libo/local/miniconda3/bin/ffprobe"]
    for exe in candidates:
        try:
            r = subprocess.run(
                [exe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp4)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    try:
        return float(json.loads(timeline.read_text(encoding="utf-8"))["total_duration"])
    except Exception:                        # noqa: BLE001
        return None


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


# 简介首行痛点钩子：前 100 字同时完成「痛点共鸣 + SEO 长尾词」，
# 不同 Part 只换这一句，避免标题党（不承诺提分、不夸大效果）。
PART_DESC_PAIN = {
    1: "生活对话听着简单，一写就丢分：数字、拼写、专有名词漏一个就是空",
    2: "地图与匹配题跟不上？连读和方位词一带而过，答案就飞了",
    3: "一做就错？多半不是「没听懂」，是同义替换和数字被一带而过",
    4: "学术讲座长句记不住？考点藏在信号词后面，边听边翻笔记就来不及",
}


def build_description(cam, t, p, duration) -> str:
    """简介文案 v3（2026-11-09 三次修订：爆款钩子版，仍守零载体闭环）。

    结构：痛点首句（含「剑桥雅思N 听力原文 / 雅思听力精听」长尾词）→ 三遍法怎么用 →
    资料闭环 → 合集与低门槛互动 → 版权说明与下架承诺。

    2026-11-17 修订（用户要求）：删掉两段文案——①「【本期时长】MM:SS + 分次练习建议」
    整块（时长信息对用户无用、且需维护字数）；② 版权说明里的「（AI 辅助制作）」括注。
    注意：投稿页「含AI生成内容」的**创作声明仍保留**（平台合规要求，见自检第 5 项），
    本修订只删简介正文里的括注，不动投稿声明。

    红线（闸门① 会 BLOCK）：不写「自动领取 / 发送关键词 / 私信领取 / 关注后回复」；
    不写「专栏 / 置顶动态 / 网盘 / 链接领取」；不挂微信/QQ/公众号；不编考点词、不编时间点。
    资料价值只锚定在视频第 2 遍高亮字幕 + 置顶评论速览（发一条评论即兑现）。

    单篇特化文案（含真实分段时间、真实考点词、真实数字）用 `--description-file`
    覆盖本模板；特化文案必须逐项对照当前篇 data/timeline.json 与 data/annotated.json。
    """
    pain = PART_DESC_PAIN.get(p, "精听练了很久没提升？多半是练法不对")
    lines = [
        f"剑{cam} Test{t} Part{p}｜雅思听力 Part{p} {pain}。",
        f"剑桥雅思{cam} 听力原文逐句精听：每句自动循环 3 遍，逐句考点全部高亮，不用自己拖进度条。",
        "",
        "【怎么用这条视频】顺序别反，反了就练成听写了：",
        "① 挖空回忆：先自己填，练的是预测，别急着看答案",
        "② 考点高亮：对照原文，把听漏的数字 / 专名 / 连读标出来",
        "③ 双语理解：确认意思，顺手整理同义替换",
        "",
        "【配套资料】本期高频考点速览直接贴在置顶评论里，站内点开就能看，不用私信、不用加好友；",
        "完整逐句考点全在视频第 2 遍高亮字幕，配合第 3 遍双语字幕即可自查。",
        "",
        f"【合集】剑{cam} Test {t} 精听全集按真题编号持续更新，建议从 Part1 顺着练到 Part4。",
        "评论区 / 弹幕告诉我你错在哪一句：① 数字拼写 ② 专有名词 ③ 连读吞音 ④ 同义替换，",
        "高频问题会单独出一期精讲。",
        "",
        "说明：本视频仅作雅思听力学习方法演示与学习交流，",
        "听力素材版权归原出版方所有；如有版权方提出异议，会第一时间下架处理。",
    ]
    return "\n".join(lines)


def suggest_publish_time(now: datetime) -> dict:
    """B站学习区活跃时段：工作日 18:00–20:00，周末 14:00–16:00。"""
    weekday = now.weekday() < 5
    start_hour = 18 if weekday else 14
    label = "工作日 18:00–20:00" if weekday else "周末 14:00–16:00"
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
        # 跨天后重新按新日期判断时段
        weekday2 = candidate.weekday() < 5
        start_hour2 = 18 if weekday2 else 14
        label = "工作日 18:00–20:00" if weekday2 else "周末 14:00–16:00"
        candidate = candidate.replace(hour=start_hour2)
    return {"window": label, "next": candidate.isoformat(timespec="minutes")}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成手动发布交付包 + 投稿物料 JSON（只准备，绝不投稿）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="项目根目录")
    ap.add_argument("--stem", required=True, help="如 c19_test2_part2")
    ap.add_argument("--adapter", default="manual", choices=("manual", "browser-use"),
                    help="manual（v2.8.0 默认）：只交付发布包，由账号本人手动发布；"
                         "browser-use：仅当用户明确要求走草稿链路时才用")
    ap.add_argument("--description-file", default=None,
                    help="单篇特化简介纯文本（UTF-8）；优先级高于通用模板，"
                         "内容须按当前篇 timeline.json / annotated.json 逐项核对，禁止编数字编词")
    ap.add_argument("--comment-file", default=None,
                    help="单篇特化置顶评论文案（UTF-8）；用于带真实分段时间轴的特化版，"
                         "时间必须取自 timeline.json 的真实边界，禁止整分整秒地编；"
                         "不传则自动生成只用真实 highlights 的通用版")
    ap.add_argument("--no-bundle", action="store_true",
                    help="只写物料 JSON、不生成交付包（草稿链路用；默认会生成交付包）")
    ap.add_argument("--bvid", default=None,
                    help="已分配/已发布的 BV 号；写入物料 JSON 与发布清单，便于回填 PDF")
    ap.add_argument("--json", action="store_true", help="stdout 打印完整 JSON")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    stem = args.stem.strip().lower()
    m = STEM_RE.fullmatch(stem)
    if not m:
        print(f"[错误] stem 不合规：{stem}", file=sys.stderr)
        return 3
    cam, t, p = int(m.group(1)), int(m.group(2)), int(m.group(3))

    mp4 = root / "ielts-video" / "out" / f"{stem}.mp4"
    pdf = root / "ielts-video" / "out" / f"{stem}_vocab.pdf"
    if not mp4.is_file() or mp4.stat().st_size < 1_000_000:
        print(f"[错误] 成片不存在或异常：{mp4}", file=sys.stderr)
        return 3

    duration = probe_duration(mp4, root / "ielts-video" / "data" / "timeline.json")
    keyword = AUTO_REPLY_KEYWORD_TEMPLATE.format(cam=cam, t=t, p=p)
    title = TITLE_TEMPLATE.format(cam=cam, t=t, p=p)
    tags = [x.format(cam=cam, t=t, p=p) for x in TAGS]
    cover_image = ensure_cover(root, stem)

    if args.description_file:
        desc_file = Path(args.description_file).expanduser().resolve()
        if not desc_file.is_file():
            print(f"[错误] 特化简介文件不存在：{desc_file}", file=sys.stderr)
            return 3
        desc = desc_file.read_text(encoding="utf-8").strip()
        if not desc:
            print(f"[错误] 特化简介文件为空：{desc_file}", file=sys.stderr)
            return 3
        desc_source = str(desc_file)
    else:
        desc = build_description(cam, t, p, duration)
        desc_source = "make_publish_kit.build_description() v3 通用爆款模板"

    if args.comment_file:
        cmt_file = Path(args.comment_file).expanduser().resolve()
        if not cmt_file.is_file():
            print(f"[错误] 特化置顶评论文件不存在：{cmt_file}", file=sys.stderr)
            return 3
        comment = cmt_file.read_text(encoding="utf-8").strip()
        if not comment:
            print(f"[错误] 特化置顶评论文件为空：{cmt_file}", file=sys.stderr)
            return 3
        comment_source = f"单篇特化文案：{cmt_file}（分段时间须已对照 timeline.json 核过）"
    else:
        comment, comment_source = build_comment(root, cam, t, p)

    kit = {
        "schema": "ielts-publish-kit/1",
        "stem": stem,
        "adapter": args.adapter,
        "delivery_mode": "manual_bundle" if args.adapter == "manual" else "browser_draft",
        "bvid": args.bvid,
        "comment_source": comment_source,
        # —— 上传文件 ——
        "video": str(mp4),
        "vocab_pdf": str(pdf) if pdf.is_file() else None,
        "duration_sec": round(duration, 1) if duration else None,
        # —— 投稿字段 ——
        "title": title,
        "description": desc,
        "description_source": desc_source,
        "tags": tags,
        "category": CATEGORY,
        "collection": f"剑{cam} Test {t} 精听全集",
        "cover": cover_spec(stem),
        "cover_image": cover_image,
        # —— 资料闭环（2026-11-09 二次修订：零载体依赖）——
        "resource_funnel": {
            "channel": "视频内高亮字幕 + 置顶评论（零外部载体）",
            "steps": "视频简介 → 置顶评论（内含考点速览，发评论即兑现）",
            "pdf_upload_to_bilibili": False,
            "pdf_role": "本地复盘资料，不上传 B站、不在站内文案里承诺分发",
            "auto_reply": AUTO_REPLY_GATE_NOTE,
            "keyword_reserved": keyword,
            "optional_extra": "B站专栏可作长尾沉淀另行发布，但不得写进简介/评论/PDF 页脚，"
                              "也不作为领取前提（需 LV1+，发布权限未实测）",
        },
        "auto_reply_keyword": keyword,
        "auto_reply_status": AUTO_REPLY_GATE_NOTE,
        "suggested_publish": suggest_publish_time(datetime.now()),
        # —— 运营红线 ——
        "requires_human_confirm": True,
        "publish_mode": "draft_or_scheduled",
        "notes": [
            "v2.8.0 默认「手动发布交付包」：agent 不填表、不存草稿、不发布；"
            "上传与发布由账号本人完成，浏览器草稿链路只在用户明确要求时启用。",
            "真题音频存在版权风险，禁止无人值守即时批量发布；逐条人工确认、一次只发一篇。",
            "前 3 个月不要挂微信号做站外引流；资料闭环零载体依赖：只写视频内高亮字幕 + 置顶评论。",
            "自动回复需 1000 粉，未达标前简介/评论绝不承诺「自动领取」「发送关键词」，"
            "否则新粉领不到资料、直接伤信任。",
            "简介/评论/PDF 页脚一律不得出现「专栏 / 置顶动态 / 网盘」等需另行发布的载体词："
            "写进去就是第二个空承诺。专栏只作可选加分项。",
            "标题/封面按 docs/06 公式锁死；前 6 篇做完整版 vs 精简版 A/B 后再全系列统一。",
            "简介默认 build_description() v3 爆款模板；单篇特化用 --description-file 覆盖，"
            "必须对照当前篇 data/timeline.json 与 data/annotated.json 核对真实分段时间与考点词，禁止编数字编词。",
            "投稿页会预置错误默认值（分区「三农」、标签「生活记录/学习/记录」、创作声明「内容无需标注」、"
            "系统抽帧封面），填表前逐项修正；标签上限 10 个，话题专用词只能从推荐标签点选。",
        ],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    bundle_dir = None
    if not args.no_bundle:
        bundle_dir = write_bundle(root, kit, comment, comment_source)
        kit["bundle_dir"] = str(bundle_dir)
        kit["bundle_files"] = [BUNDLE_VIDEO, BUNDLE_COVER, BUNDLE_PDF,
                               BUNDLE_COMMENT, BUNDLE_MD]
        kit["comment_file"] = str(bundle_dir / BUNDLE_COMMENT)

    out_path = Path("/tmp") / f"ielts_publish_{stem}.json"
    out_path.write_text(json.dumps(kit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if len(title) > 80:
        print(f"[WARN] 标题 {len(title)} 字，超过 B站上限 80", file=sys.stderr)
    if len(tags) > 10:
        print(f"[WARN] 标签 {len(tags)} 个，超过 B站上限 10", file=sys.stderr)

    if args.json:
        print(json.dumps(kit, ensure_ascii=False, indent=2))
    else:
        print(f"物料已生成：{out_path}")
        if bundle_dir:
            print(f"交付包目录：{bundle_dir}")
            for name in kit["bundle_files"]:
                f = bundle_dir / name
                print(f"  {'✓' if f.is_file() else '✗'} {name}")
        print(f"  标题：{title}（{len(title)} 字）")
        print(f"  简介：{desc_source}（{len(desc)} 字）")
        print(f"  置顶评论：{comment_source}")
        print(f"  标签：{' / '.join(tags)}")
        print(f"  分区：{CATEGORY}")
        print(f"  合集：{kit['collection']}")
        print("  资料闭环：视频内高亮字幕 + 置顶评论速览（零载体依赖；PDF 不上传 B站）")
        print(f"  自动回复：{AUTO_REPLY_GATE_NOTE}")
        print(f"  封面：{cover_image or '【缺失】请先跑 make_cover.py'}")
        print(f"  时长：{fmt_ts(duration) if duration else '未知'}")
        print(f"  建议发布：{kit['suggested_publish']['window']}，下次 {kit['suggested_publish']['next']}")
        print(f"  模式：{kit['delivery_mode']}｜agent 不填表、不存草稿、不发布；由你手动发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())

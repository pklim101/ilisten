# 02 · 自动质检规则（qa_check.py）

退出码：`0 = PASS / WARN`（可交付，WARN 需向用户说明）、`2 = BLOCK`（不得交付）、`1 = 脚本异常`（按 BLOCK 处理）。
脚本只读，不写任何文件，摘要只输出到 stdout。

## 数据来源

| 来源 | 用途 |
|---|---|
| `ielts-video/data/sentences.json` | 对齐率、未匹配句、指令行过滤、说话人标签 |
| `ielts-video/data/instructions.json` | 说明段数量与文本 |
| `ielts-video/data/annotated.json` | eligible 句数、translation、highlights |
| `ielts-video/data/timeline.json` | events、repeats、total_duration、说明段间隙 |
| `ielts-video/data/subtitles.ass` | Dialogue 行数、三态标签、`\an5`、Margin |
| `ielts-video/out/<stem>.mp4` | ffprobe 规格、时长、大小、volumedetect |
| `ielts-video/out/<stem>_vocab.pdf` | 存在与大小 |
| `resource/<stem>.md` | 源文说话人标签与指令行核对 |
| `references/qa_baseline.json` | 说明段数量基线（写死，运行时不落盘） |

## 检查项

**B = BLOCK（阻断交付），W = WARN（提示但继续）**

| # | 检查项 | 判定 | 级别 |
|---|---|---|---|
| A1 | 指令行已过滤 | `sentences.json` 中无句以 `ANNOUNCER:/NARRATOR:/PART/SECTION/Test` 开头（正则同 `01_align.py`） | B |
| A2 | 说话人标签保留 | 源文含 `^[A-Z][A-Z0-9 ]{1,20}:` 标签但结果全无 → B；源无标签 → 记 `-` | B/W |
| B1 | 对齐率 | `matched / total >= 0.90`（历史 93% / 97% / 100% / 95.3%） | B |
| B2 | 未匹配句是孤立残片 | 去标点后为空或词数 ≤1 → 允许；若为完整句 = **内容静默丢失**（掉进说明段只播一遍无字幕） | B |
| B3 | 说明段数量 | `count > instruction_max`（基线；无基线默认 6）→ B；`count > instruction_typical` → W | B/W |
| B4 | 说明段混入正文 | 与任一正文句 `SequenceMatcher ratio > 0.80` → B；词数 >120 且无指令关键词 → W。合法说明段实测可达 ~90 词，**不能只按词数判** | B/W |
| C1 | 锚点错位 / 超长事件 | **按原文词数自适应**：`limit = max(20, n_words/2.2×1.5+3)`（与 `01_align.py` 的 `sentence_max_sec()` 同源）；任一 event `end-start > limit` → B。取不到词数退化为固定 20s。**20s 下限保留**，短句被异常拉长照样 BLOCK | B |
| C2 | 事件数口径 | `count(annotated 中 start/end 非空且 end-start>=0.5) * repeats == len(timeline.events) == ASS Dialogue 行数`。**禁止用 `matched*3`**（实测 matched=81 但 events=237=79×3） | B |
| C3 | 时长一致 | `abs(timeline.total_duration - ffprobe.duration) <= 1.0s` | B |
| C4 | 三态字幕齐备 | `\u1` >0、`\c&H000080FF&` >0、`\rZH` 数 == 有 `translation` 的 repeat=2 事件数，且 `\u1` 数 == 橙色标签数 | B |
| C5 | 版式 | `MarginL>=50` 且 `MarginR>=50`（实测 120）；`\an5` 数 == Dialogue 数 | B |
| C6 | 说明段无字幕 | 计算 `>=4.0s` 的未覆盖间隙，断言无 Dialogue 时间区间与之重叠（`03` 根本不为说明段建 event） | B |
| C7 | 有字幕无人声兜底 | 对最长且 >12s 的事件跑 `volumedetect`，`max_volume < -35dB` → W（C1 已覆盖已知根因，此项不阻断） | W |
| D1 | 成片规格 | ffprobe：`1920x1080` / `h264` / `aac` / duration 可读；ffprobe 缺失时解析 `ffmpeg -i` stderr 兜底 | B |
| D2 | 成片非空 | mp4 `size > 1_000_000` 字节 | B |
| E1 | 考点词 PDF | `out/<stem>_vocab.pdf` 存在且 `size > 10_000` 字节 | B |
| E2 | 背景图未被篡改 | 基线提供 sha256 时比对，不一致 → W（防 `00_background.py` 误跑）；无基线 → SKIP | W |

补充 WARN：成片 > 20 分钟时提示完播率风险（B站运营侧建议给分段练习时间轴），不阻断。

## 关键算法

**未覆盖间隙（C6 与抽帧帧 3 共用）**：把 `timeline.events` 的 `(start, end)` 排序合并，从 `cur=0` 扫到 `total_duration`，
凡 `s - cur >= 4.0` 记为一个间隙。1s / 3s 停顿均 <4s 被排除，实测 test2 P1 恰好只剩 3 个说明段
（91.5s / 36.4s / 12.9s）。

**ASS 时间区间**：`Dialogue: ...`,`Start`,`End`,`Text`，格式为 `H:MM:SS.cc`，转秒后与间隙求交。

**B4 相似度**：`difflib.SequenceMatcher(None, norm(instruction_text), norm(sentence_text)).ratio()`，
`norm` = 小写 + 去标点 + 压缩空白。只对说明段文本与所有正文句两两比，取最大值。

## 摘要格式（stdout，可直接照此实现）

```
========================================================
 质检摘要  剑19 Test2 Part1
 成片: /.../ielts-video/out/c19_test2_part1.mp4
--------------------------------------------------------
 [PASS] 素材格式    指令行已过滤，说话人标签保留 8 处
 [PASS] 对齐率      81/85 = 95.3%  (>= 90%)
 [PASS] 未匹配句    4 句，均为孤立残片('.', '.', '.', '.')
 [PASS] 说明段      3 段 (基线 3)，无正文句混入
 [PASS] 超长事件    最长 12.2s (阈值 20s)，0 个超限
 [PASS] 事件数      237 = 79句 x 3 = ASS 237 行
 [PASS] 时长一致    timeline 1387.7s ≈ 成片 1387.7s (Δ 0.04s)
 [PASS] 三态字幕    挖空 153 / 橙高亮 153 / 双语 79
 [PASS] 版式        MarginL/R=120px, \an5 居中(237)
 [PASS] 说明段字幕  3 段大间隙无 Dialogue 覆盖
 [PASS] 成片规格    1920x1080 / h264 / aac / 23:08 / 72.4MB
 [PASS] 考点词PDF   c19_test2_part1_vocab.pdf (60.5KB)
 [WARN] 成片偏长    23分钟，完播率风险，建议简介给分段练习时间轴
--------------------------------------------------------
 结论: PASS  (12 PASS / 1 WARN / 0 BLOCK)  → 可交付
 下一步: qa_frames.py 抽 3 帧 → 逐张 ViewImage 确认 → 全部通过才进入下一篇
========================================================
```

BLOCK 时把对应行换成 `[BLOCK]` 并在末尾打印：
`结论: BLOCK —— 不得交付、不得开始下一篇；成片已保留，请按 references/04 处置`。

## 历史踩坑（为什么有这些检查项，详见 docs/07）

1. **锚点错位**：`difflib.SequenceMatcher` 把高频词 `school` 错配到 52 秒外的同名位置，
   句子被拉长成 58 秒 → 成片出现「有字幕无人声」。修复：`01_align.py` 增加句内时间连续性校验。→ C1
2. **固定阈值误伤长句**：Part3 两句 49/37 词长对话被判「未匹配」→ 掉进说明段只播一遍、无字幕，
   **内容静默丢失**。改为自适应阈值后恢复。→ B1/B2/B4
3. **素材格式不统一**：`ANNOUNCER:` / `NARRATOR:` 指令行若未过滤，会被当正文循环 3 遍还配字幕。→ A1/A2
4. **QA 固定 20s 误伤学术长句**（2026-11-09 `c19_test2_part4`）：Part4 独白 45 词长句实际 22.2s，
   对齐率 39/39=100%、0 未匹配、人声兜底 `max_volume -10.8dB`，却因固定 `MAX_EVENT_SEC=20` 判 BLOCK。
   C1 改为按词数自适应并保留 20s 下限；回归覆盖 `resource/` 全部 8 篇原文的最长句 + 5 个反例
   （10 词/25s、20 词/30s、45 词/40s、无词数/25s 仍全部 BLOCK）。→ C1
4. **说明段异常信号**：说明段数量突然变多 / 文本是完整对话句 / 对齐率 <90%，任一出现应停下检查。→ B3/B4
5. **事件数口径**：`03` 的 `MIN_SENT_SEC=0.5` 会再过滤短句，`matched*3` 会算错。→ C2
6. 出片后还要**人工抽听** 2~3 个时间点（尤其长句、开头句），脚本质检不能替代。→ 见 03

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
| B2 | 未匹配句是孤立残片 | **先剥说话人标签** `^[A-Z][A-Za-z]+\s*:\s*`（标签是元数据、不是正文词），再去标点判空或词数 ≤1 → 允许；若为完整句 = **内容静默丢失**（掉进说明段只播一遍无字幕）。2026-11-15 校准：`SALLY: OK.` 曾被按空格算成 2 词而误报（详见「关键算法」） | B |
| B3 | 说明段数量 | `count > instruction_max`（基线；无基线默认 6）→ B；`count > instruction_typical` → W | B/W |
| B4 | 说明段混入正文 | 与任一正文句 `SequenceMatcher ratio > 0.80` → B；词数 >120 且无指令关键词 → W。合法说明段实测可达 ~90 词，**不能只按词数判**。**说明段文本 <12 字符（norm 后）视为分段边界噪声，不参与相似度比较**（2026-11-15 校准，见「关键算法」） | B/W |
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

## 句尾尾音验收（脚本 17 项之外，2026-11-14 新增；v2.9.3 起自动化）

`qa_check.py` 只看时间轴 / 字幕 / 成片规格，**看不出音频片段末尾被硬切**。凡改动 `03_rebuild_audio.py` 切片边界、`01_align.py` 时间戳或 `afade` 参数，除 17 项 QA 外**必须立即跑**（与 QA、抽帧并列，不得延后批量做）：

```
$PY .workbuddy/skills/ielts-listening-video/scripts/tail_check.py --root <项目根> --stem <stem>
```

退出码 `0=PASS` / `2=BLOCK（不得交付）` / `1=脚本异常`；依赖 numpy，用 `run.sh` 同款 Python。
`--data` 可指定备份的中间产物目录，`--audio` / `--mp4` 可复验历史成片（下一句起音误判排查时常用）。

四项检查：

1. **原音频能量残留**：原 mp3 与成片都解码为 16k mono；每句实际播放右边界由 `timeline` 中 `repeat=0` 事件的时长反推（`play_end = 原句 start + event 时长`）。向后按 20ms 窗扫 RMS，用**双阈值**：`thr_low = 句内 RMS×0.10` 只用于重置静音计数，`thr_speech = 句内 RMS×0.25` 才累计「被切掉的响亮音节」；连续 **6 窗**（120ms，与 `03_rebuild_audio.py` 同源）低于 `thr_low` 视为语音结束。扫描上限 `min(1.80s, 下一句 start − 0.10s)`（与 `TAIL_SCAN_MAX_SEC` 同源）。
2. **片段越界重叠**：`play_end` 越过下一句 `start` >10ms 记 1 句（timeline 均 `round(...,3)`，±1ms 舍入误差不算）。`next_start` 必须与 `03_rebuild_audio.py` 的 clamp 同源，取 **`sentences.json` 里所有正文句**（含时长 <`MIN_SENT_SEC` 被过滤、没有 timeline 事件的短句）；若只用 `repeat=0` 事件的句子，短句会被跳过，既漏报「补尾音吞掉下一句」，又会把短句人声误扫成本句残留。剑17 T1 P1 实测：旧 clamp 版 `tail_ratio` 全正常、第 [4] 项报触顶，改成全量口径后立刻抓到 `(67, 0.84s)` 真重叠。
3. **成片音轨末尾衰减 + 淡出检测**：对每个 `repeat=0` 事件算
   `tail_ratio = RMS(end−30ms,end) / RMS(start+0.25×dur, end−0.15×dur)`，
   以及 `fade_ratio = RMS(end−10ms,end) / RMS(end−40ms,end−30ms)`。
   健康基线：**`tail_ratio` 中位 <0.10、`fade_ratio` 中位 <0.10**（40ms 淡出生效）。
   **只有 `tail_ratio >0.8` 且 `fade_ratio >0.35` 才算硬切**：前者单独偏高时，末 30ms
   窗口取到的是淡出斜坡，句中段偏轻的短句会算出虚高比值（剑17 T1 P3 实测 1.06），
   不是缺陷。缺陷版没有淡出：`fade_ratio` 中位 0.724、16 句真硬切。
4. **补尾音触顶截断**（v2.9.5 新增）：若某句 `tail_pad_sec ≥ TAIL_PAD_MAX_SEC−0.01`（当前 1.50s），
   且播放右边界后仍扫到 >100ms 实质人声、又明显早于下一句 `start−0.10s`（不是下一句起音），
   说明本句尾音被硬上限砍掉 → BLOCK。**必须查这一项**：whisper 把 `computerised`
   误拆成 `"computer eyes"` 时词级 `end` 可偏早 **1.14s**，而 40ms 淡出会把成片末尾
   `tail_ratio` 压成「淡出豁免」，第 3 项完全看不出来（剑17 T2 P1 实测：0.90s 上限版
   17 项 QA PASS、成片 0 硬切，但 "computerised?" 后半被砍）。
   **说明段排除**：`play_end` 落在 `instructions.json` 的说明段区间内时，上限后的人声是
   读题/结束语，不是本句尾音，只记 WARN「说明段人声已排除」，不 BLOCK。剑17 T4 P2 的
   idx41 实测：whisper 把考试结束语错配成正文最后一句，440.34~442.20 全是读题人声；
   说明段为 (440.34, 472.63)。而真缺陷（剑17 T2 P1 idx15，play_end=152.68）不在任何
   说明段内，仍被正确拦下。

**BLOCK 判据（以成片实测硬切为主）**：无淡出的硬切句子 >0、片段重叠 >0、残留 >100ms 占比 **>20%**、
或补尾音触顶截断 >0 句 → BLOCK。否则 PASS；残留明细与「淡出豁免」明细一律以 WARN 打印，不拦。

为什么不要求「残留 = 0」：对话体句间空隙常只有 0.07~0.35s，whisper 的下一句 `start` 又常标晚，6 窗容忍会扫到下一句满幅人声；紧邻说明段时也会扫到读题人声。这些不是本句尾音，**硬把指标压到 0 会逼出片段重叠**（把前句尾音混进后句开头）。双阈值再把 10~20% 句内能量的房间混响 / 辅音摩擦余晖（剑18 T4 P4 的 idx 3/22/31，RMS 0.010~0.025 随后立刻归零）与真正被切掉的音节（`1855` 尾音 0.13~0.28、`'art.'` 0.14~0.37）区分开。

实测基线（脚本输出口径）：

| 成片 | 参与句 | 补尾音 | 残留 >100ms | 无淡出的硬切 | 结论 |
|---|---|---|---|---|---|
| 剑18 T4 P4 旧片（未补尾音） | 33 | 0 | 22 (66.7%)，含 `1855` 360ms、`'art.'` 660ms | 16（tail 中位 0.788 / fade 中位 0.724） | BLOCK |
| 剑18 T4 P4 新片（v2.9.3） | 33 | 33，平均 +255ms / 最大 +600ms | 0 | 0（中位 0.026） | PASS |
| 剑18 T4 P3（v2.9.2 口径） | 54 | 48，平均 +190ms / 最大 +430ms | 5 (9.3%)，均为下一句起音 | 0（tail 中位 0.040 / fade 中位 0.048） | PASS + WARN |
| 剑17 T1 P3（v2.9.4 全量重出首篇） | 60 | 47，平均 +188ms | 2 (3.3%) | 0；1 句淡出豁免 `(idx39, tail 1.06, fade 0.20)` | PASS + WARN |
| 剑17 T2 P1（0.60s 上限） | 78 | 68，最大 +600ms | 1 (1.3%) | 1（idx37 `Mrs Carroll.` tail 0.826 / fade 0.398） | BLOCK |
| 剑17 T2 P1（0.90s 上限，淡出掩盖） | 78 | 68，最大 +900ms | 1 (1.3%) | 0（第 3 项看不出） | 第 4 项 BLOCK：idx15 触顶 + 320ms 残留 |
| 剑17 T2 P1（v2.9.5 最终 1.50s 上限） | 78 | 68，平均 +211ms / 最大 +1250ms | 0 | 0（tail 中位 0.058 / fade 中位 0.095）；触顶 0 句 | PASS |
| 剑17 T1 P1（v2.9.6 旧 clamp，短句被跳过） | 62 | 61，最大 +1500ms | 0 | 0；**1 句重叠 0.84s**（idx67 吞掉 `until 3.`） | BLOCK |
| 剑17 T4 P2（v2.9.7 篇末说明段） | 42 | 41，最大 +1500ms | 1 (2.4%) | 0；触顶 0 句，1 句说明段人声排除 `(idx41, 360ms)` | PASS + WARN |

判读注意：句间零空隙（`end == 下一句 start`）的句子物理上补不了尾音，允许 `tail_pad_sec=0`，靠 40ms 淡出消除突兀感；这类句子 `tail_ratio` 可能 **>0.8**（淡出斜坡被误读成满幅人声），以 `fade_ratio` 判定，淡出生效即放行，**禁止为消 WARN 去改音频或放宽 `HARD_CUT_RATIO`**。**不要为了把指标压到 0 而让相邻片段重叠**——那会把前句尾音混进后句开头，干扰听写。

## 关键算法

**未覆盖间隙（C6 与抽帧帧 3 共用）**：把 `timeline.events` 的 `(start, end)` 排序合并，从 `cur=0` 扫到 `total_duration`，
凡 `s - cur >= 4.0` 记为一个间隙。1s / 3s 停顿均 <4s 被排除，实测 test2 P1 恰好只剩 3 个说明段
（91.5s / 36.4s / 12.9s）。

**ASS 时间区间**：`Dialogue: ...`,`Start`,`End`,`Text`，格式为 `H:MM:SS.cc`，转秒后与间隙求交。

**B4 相似度**：`difflib.SequenceMatcher(None, norm(instruction_text), norm(sentence_text)).ratio()`，
`norm` = 小写 + 去标点 + 压缩空白。只对说明段文本与所有正文句两两比，取最大值。

**B4 短文本豁免（2026-11-15 剑18 Test2 Part1 校准）**：`01_align.py::extract_instructions()`
把正文句区间之外 >4s 的空隙标为说明段，`text` 取落在该区间的 whisper 转写。转写分段边界
有微小波动时，读题空隙尾部可能抓到一个孤立的 `OK.` / `Thank you.`，与正文短应答 `OK.`
的短串相似度天然为 **1.00** → 误报 BLOCK（同篇第一次出片却 PASS，正是边界波动所致）。
这类 `len(norm(text)) < SHORT_INSTR_TEXT_CHARS(12)` 的文本不可能是「整段正文被误判为说明段」
（B4 本意），故跳过其相似度计算，并在 PASS 消息里注明跳过段数。真正的正文丢失仍由
B1/B2 未匹配句、B3 段数、C 类时间轴事件兜底，**不放宽这些项**。
回归：剑18 16 篇留档按新判据模拟，最高相似度 0.622（阈值 0.80），无一被掩盖；
该篇重出后完整 17 项 QA `17 PASS / 1 WARN / 0 BLOCK`。

**B2 说话人标签剥离（2026-11-15 剑19 Test1 Part1 校准）**：该篇按音频裁剪原文后，
首句 `SALLY: OK.` 是孤立短应答、whisper 未匹配；旧 `is_residual()` 直接 `t.split()`，
把标签词 `SALLY:` 也算作正文词（2 词）→ 误判「完整内容静默丢失」BLOCK。
说话人标签由 A2 单独校验、属元数据，故 B2 判残片前先剥
`^\s*[A-Z][A-Za-z]+\s*:\s*`，只按正文词数判定；单篇闭环闸门同步同口径。
回归：该篇完整 17 项 QA `17 PASS / 0 BLOCK`；剑19 其余 15 篇留档「完整句未匹配」为 0，
判据未掩盖任何真实内容丢失。**B1 对齐率、B3 段数、C 类时间轴兜底均未放宽。**

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
7. **B4 短串相似度误报**（2026-11-15 `c18_test2_part1`）：40s 读题空隙的说明段 `text`
   抓到边界 `OK.`，与正文短应答 `OK.` 相似度 1.00 → 误报 BLOCK；同篇第一次出片却 PASS
   （whisper 分段边界波动）。校准为 <12 字符说明段文本豁免，留档回归 16/16 最高 0.622，
   重出后 PASS。→ B4

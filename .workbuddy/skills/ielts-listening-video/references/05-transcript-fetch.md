# 05 · 只有音频时如何取得听力原文（联网检索 + 音频交叉校验）

> 适用：用户只丢 `resource/<stem>.mp3`，不再提供 `<stem>.md`。
> 脚本：`scripts/fetch_transcript.py`。本阶段编号为 **1.5**，夹在「扫描」与「出片」之间。

## 一、为什么不能把联网搜到的文本直接存成 md

2026-11-08 用用户给的原话实测（`test2 part2`），DeepSeek 深度思考 + 内置联网搜索
确实调用了 4 次搜索、11 次网页抓取，返回 6553 字符"完整原文"。但直接拿去出片会坏：

1. **模型自己声明**：「官方书末 *Audio scripts* 属剑桥大学出版社版权材料，无法直接复制；
   以下依据多个与官方音频一致的文字来源交叉核对……各来源在个别虚词上有微小出入，
   我已按多数一致版本整理」——即**多数表决版**，不是逐词官方 tapescript。
2. **输出里混了大量非录音内容**：试卷题目（Questions 11–20）、A/B/C 选项、答案表、
   题目解析、`##` 标题、`**加粗**`、`---` 分隔线、markdown 表格、版权说明。
   这些文字**音频里根本不存在**，一旦进 `orig_sents`，`SequenceMatcher` 会大面积匹配不上，
   触发 `docs/07` 的「未匹配句掉进说明段 → 内容静默丢失」。
3. 标签被写成 `**ANNOUNCER:**`，`01_align.py` 的过滤正则是
   `^\s*(ANNOUNCER|NARRATOR)\s*:`，**前面的 `**` 会让它匹配不到**，指令行会被当正文循环 3 遍。
4. 正文顺序若被打乱（全部指令堆到文件头），序列对齐会整体错位。

结论：**联网结果只能当素材，不能当原文**。必须经「结构化抽取 + 音频交叉校验」两道闸门。

## 二、DeepSeek API 契约（2026-11-08 实测确认）

| 项 | 值 |
|---|---|
| 检索模型 | `deepseek-v4-pro`（旧 `deepseek-chat` / `deepseek-reasoner` 官方已标弃用，但仍可用） |
| 端点 | `POST https://api.deepseek.com/responses`（OpenAI Responses API 格式） |
| 深度思考 | `reasoning: {"effort": "high"}`（可选 `low/high/max`；`none` 关闭） |
| 联网搜索 | `tools: [{"type": "web_search"}]` —— **仅 `deepseek-v4-pro` 支持** |
| 返回 | `output[]` 中 `type=message` 为答案、`type=web_search_call` 为搜索/抓取记录（`action.queries` / `action.url`）、`type=reasoning` 为思维链 |
| 计费 | `usage.server_tool_use.tool_search` / `tool_fetch`（搜索与抓页各约 `$0.75/1000` 次，失败/空结果不计） |
| 抽取模型 | `deepseek-chat` + `response_format: {"type":"json_object"}`（项目 `02_annotate.py` 同款，便宜、稳定） |

实测可用性（同一把密钥）：`deepseek-chat` → 200 OK；`deepseek-v4-pro` + `web_search` + 思考 → 200 completed。
**现有 `02_annotate.py` 不受影响，不要因为模型改名去改它。**

兼容回退：`/responses` 或 `web_search` 不可用时，脚本回退 `/chat/completions` 旧模型，
但该路径**没有联网**，结果只是模型记忆 → 强制判 `BLOCK`、绝不落盘。

## 三、三阶段流程

```
阶段 A  提示词（用户原话模板）：
        发我雅思剑{cam}听力部分的 test{t} part{p}的原文内容，包含读题内容。注意：要求来源权威、正确。
        → deepseek-v4-pro + effort=high + web_search
        → 长文本 + 来源 URL 清单 + 工具用量

阶段 B  deepseek-chat + json_object，把长文本抽成
        {"segments":[{"type":"narrator","text":"..."},{"type":"body","text":"..."}]}
        严格剔除试卷题目/选项/答案/解析/来源说明/markdown；
        narrator = 录音里念出的读题与考试指令；body = 正文逐句（保留说话人标签）；
        segments 必须按录音时间顺序排列（中间读题提示插在它实际出现的位置）。

阶段 C  faster-whisper（与 01_align.py 同为 small / int8 / cpu_threads≤4）转写音频作为真值
        → 词级 SequenceMatcher(autojunk=False) 双指标置信度闸门
```

### compose_md 的输出契约

- `narrator` 段统一写成 `NARRATOR: ...` 单行 → 会被 `01_align.py` 过滤，**不进正文、不循环、不加字幕**，
  但保留在 md 里供人核对，满足用户「包含读题内容」的要求。
- `body` 段原样保留（含 `DAVID:` 等说话人标签），顺序严格等于录音顺序。
- 不写 `#`、`**`、`---`、表格、题号、答案。

## 四、置信度闸门（阈值可环境变量覆盖）

规范化：去说话人/指令标签 → Unicode NFKD 去重音（`Céide → Ceide`）→ 小写 → 只留 `a-z0-9'`。
`autojunk=False` 必设（长文本否则会被误判）。

| 指标 | 含义 | 抓什么错 |
|---|---|---|
| `coverage` = 匹配词数 / md 词数 | 生成原文里有多少词能在音频里按序找到 | md 混入试卷题目、模型编造、多余段落 |
| `asr_coverage` = 匹配词数 / ASR 词数 | 音频里有多少词被生成原文覆盖 | 漏段、正文被截断 |

| 判定 | 条件 | 行为 |
|---|---|---|
| **PASS** | `coverage ≥ 0.90` 且 `asr_coverage ≥ 0.80` | 落盘 `resource/<stem>.md`，可进入出片 |
| **WARN** | `coverage ≥ 0.82` 且 `asr_coverage ≥ 0.70` | 落盘，但**必须向用户展示差异摘要并取得确认**才出片 |
| **BLOCK** | 其余 / 未联网 / 抽取失败 | **不落盘、退出码 2、不得出片**，等人工提供权威原文 |

环境变量：`TRANSCRIPT_MIN_COVERAGE`、`TRANSCRIPT_MIN_ASR_COVERAGE`、
`TRANSCRIPT_WARN_COVERAGE`、`TRANSCRIPT_WARN_ASR_COVERAGE`、
`TRANSCRIPT_SEARCH_MODEL`、`TRANSCRIPT_EXTRACT_MODEL`、`TRANSCRIPT_HTTP_TIMEOUT`。

差异摘要会按 `(类型, md 片段)` 去重、忽略 1~2 词的零散差异（多为 whisper 对专有名词的识别误差），
最多列 6 处，避免刷屏。

## 五、实测基线

| 篇目 | 联网检索 | 抽取结果 | 音频交叉校验 | 结论 |
|---|---|---|---|---|
| `c19_test1_part4` | 4499 字符 / 13 来源 / 4 搜索 + 13 抓页 | 8 条指令段 + 34 条正文段（743 词） | coverage **0.938**、asr_coverage **0.976** | PASS |
| `c19_test1_part4` 联网版 vs 项目已有官方原文（741 词） | — | — | coverage **0.992**、asr_coverage **0.995** | 唯一差异 `over grazing` ↔ `overgrazing` |

第二行是最有说服力的一条：**联网检索 + 结构化抽取能还原出接近逐词的官方 tapescript**。
但差异确实存在（`overgrazing` 连写），而 `01_align.py` 是逐词对齐的——所以音频交叉校验这道闸门不能省。

## 六、风险与红线（重要）

1. **版权**：官方 Audio scripts 是剑桥大学出版社版权材料。模型明确拒绝直接复制官方页面，
   给的是第三方整理稿的交叉核对版。本能力把「用户自备原文」变成「系统主动检索全文」，
   合规叙述不同，**是否使用由用户自行判断**；`docs/04` 里定的「原创占比守住 25%」等缓释路线继续有效。
2. **准确性**：网络流传的机经/回忆版错误率高，尤其读题指令。这就是必须做音频校验的原因。
3. **音频是唯一真值源**：成片字幕、翻译、考点词全部依赖原文与音频的对齐；
   宁可 BLOCK 也不放一份可疑原文进流水线——返工成本远高于人工补一次原文。
4. **`--skip-asr` 仅用于调试**，生产禁用（跳过校验 = 失去唯一真值）。
5. **`--reuse-raw` 仅用于调试/省钱**：复用 `/tmp` 里的检索、抽取、ASR 缓存，零 API 调用。
   正式任务不得加，否则用的是旧缓存。
6. 所有缓存与报告只写 `/tmp/ielts_transcript_<stem>*`，**绝不进仓库、绝不污染 `resource/` 扫描域**。
7. 密钥只从 `DEEPSEEK_API_KEY` 环境变量读，绝不回显、绝不写入脚本/日志/报告。

## 七、失败处置

| 现象 | 处置 |
|---|---|
| `BLOCK`（退出码 2） | 停止，不落盘、不出片；向用户报 `coverage/asr_coverage` 与差异摘要；请其提供权威原文或确认阈值 |
| 联网检索失败 → 回退无联网模型 | 强制 BLOCK，并明确告知「结果仅为模型记忆，不可信」 |
| `结构化抽取失败 / 无正文段` | 退出码 1，不落盘；可带 `--reuse-raw --force` 重试一次，不要无限重试 |
| **模型因版权策略拒答**（0 来源、返回版权声明） | **不是瞬时故障，禁止重试**；走第九节的外部来源注入兜底，ASR 闸门照跑，PASS 才落盘 |
| `WARN` | 照常落盘，但**在出片前**把差异摘要给用户确认 |
| md 已存在 | 默认跳过（`--force` 才重取）。人工提供的权威原文优先级最高，不要覆盖它 |
| 只有 md 没 mp3 | 扫描阶段 WARN 跳过：没有音频既不能出片，也无法做交叉校验 |

## 八、常用命令

```bash
PY=/Users/sofia/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SKILL=/Users/sofia/libo/personknowlege/ilisten/.workbuddy/skills/ielts-listening-video
ROOT=/Users/sofia/libo/personknowlege/ilisten

# 先看不落盘的预览与校验结果
source ~/.bash_profile   # 取密钥；非登录 shell 每条 Bash 都要
$PY "$SKILL/scripts/fetch_transcript.py" --root "$ROOT" --stem c19_test2_part2 --dry-run

# 正式补齐（PASS 才会写出 resource/<stem>.md）
$PY "$SKILL/scripts/fetch_transcript.py" --root "$ROOT" --stem c19_test2_part2

# 调试：完全离线复用缓存
$PY "$SKILL/scripts/fetch_transcript.py" --root "$ROOT" --stem c19_test2_part2 --reuse-raw --dry-run

# 版权拒答兜底：注入本次多来源交叉整理的结构化原文（ASR 闸门照跑）
$PY "$SKILL/scripts/fetch_transcript.py" --root "$ROOT" --stem c19_test2_part4 \
    --segments-file /tmp/ielts_part4_segments.json \
    --source-url "https://sit-top.zhan.com/ielts/listen/review-1429-1-01.html" \
    --source-url "https://m.kekenet.com/lesson/16868-699289"
```

## 九、版权拒答兜底入口（`--segments-file`，v2.9.0）

**触发**：2026-11-09 `c19_test2_part4`，`deepseek-v4-pro` + `web_search` 直接返回
「无法提供逐词官方 tapescript（版权保护）」的 745 字符声明，**联网工具调用 0 次、来源 0 个**，
阶段 B 结构化抽取为空 → 退出码 1、不落盘。这不是网络超时/5xx，**按运行纪律不重试**
（重试只会再次触发同一策略，白烧一次请求）。

**做法**：由 agent 用可核验的公开来源做多来源交叉，整理成结构化 `segments` JSON 注入：

```json
[{"type": "body", "text": "For my project on invertebrates, I chose to study tardigrades."}, ...]
```

- 来源优先级：**带时间戳的音频逐词稿**（小站备考）> 逐句精讲稿（可可英语）> 全文 audioscript（luckyielts）；
  三者冲突时以音频逐词稿为准（如 `curl into a ball` / `10 days`，而非改写版 `form a little ball` / `10 day`）。
- 必须剔除题号 `Q31~Q40`、markdown 星号、时间戳、中文翻译、答案解析；修正明显 OCR 粘连
  （`1milimetre→1 millimetre`、`barrel-and→barrel - and`、`has pair→has a pair`）。
- 注入后**阶段 B 跳过 LLM 改写**（避免二次引入错词），但**阶段 C 的 faster-whisper 转写与
  `cross_check()` 双指标闸门完全照跑**；`--source-url` 至少 1 个（决定 `searched=True`），
  无来源强制 BLOCK；禁止与 `--skip-asr` 同用。
- 与 `--reuse-raw` 的区别：注入的是**本次新抓的权威素材**，不是 `/tmp` 旧缓存；
  报告里 `model=agent web_fetch (external sources)`、`injected=true`、附完整来源清单。

**实测（`c19_test2_part4`，2026-11-09）**：3 个来源 / 40 段正文 →
coverage **0.966**、asr_coverage **0.904** → **PASS**，落盘 40 行；
`01_align.py` 单跑 39 句 / **39 句全对齐** / 3 个说明段（读题 71.8s、中段 4.7s、结尾 17.7s）。
主要差异均为 ASR 音转写特征：`1 millimetre↔1mm`、`1.2 millimetres↔1.2mm`、
`they are↔they're`、引号、开头读题指令（md 只有正文，指令由音频空隙自动识别）。

**边界**：注入稿是「多来源交叉的音频逐词稿」，**不是逐词官方 tapescript**；
用户若持有正版书 Audio scripts，应优先人工提供并覆盖。md 不含 `NARRATOR:` 行时，
读题指令仍会由 `01_align.py` 的空隙识别为说明段，不循环、不加字幕，不影响成片。

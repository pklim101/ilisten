# 04 · 排障、重试与专项操作

## 失败分类

| 现象 | 分类 | 能否自动重试 |
|---|---|---|
| DeepSeek 5xx / 连接超时 / JSON 批次解析失败 | 瞬时外部故障 | ✅ 最多 1 次 |
| HuggingFace 模型下载失败 / 超时 | 瞬时外部故障 | ✅ 最多 1 次（确认 `HF_ENDPOINT` 已设） |
| `qa_check.py` 返回 BLOCK | 内容缺陷 | ❌ 绝不自动重试 |
| 对齐率 <90% / 说明段异常 / 未匹配句是完整句 | 内容缺陷 | ❌ 停下检查 `data/*.json` |
| 6 步中任一步 Python 断言失败 / `set -e` 退出 | 代码或素材缺陷 | ❌ 看日志定位 |
| `05_compose.py` exit 137 / OOM | 资源 | ❌ 确认在后台运行；不要改小分辨率绕过 |
| `:? DEEPSEEK_API_KEY` 报错 | 环境 | ❌ 在同一条命令里 `source ~/.bash_profile` |

自动重试会重复消耗 DeepSeek 调用费并掩盖真实缺陷，所以只放行「明确的瞬时外部故障」。

## 常见故障速查

**密钥取不到**：Bash 工具是非登录 shell，`~/.bash_profile` 不会自动继承。
`run_one.sh` 用 `set +e; source ~/.bash_profile; set -e` 包裹（防 profile 内其他命令触发 `set -e` 中断），
再 `[ -n "${DEEPSEEK_API_KEY:-}" ]` 判空。只判空，**绝不打印值**。

**`ffprobe` 不存在**：`qa_check.py` 优先用 `ffmpeg_helper.get_ffprobe()`（实测命中
`/Users/sofia/libo/local/miniconda3/bin/ffprobe`）；返回空时解析 `ffmpeg -i` 的 stderr
（`Duration:` / `Video: h264 ... 1920x1080` / `Audio: aac`）兜底。

**PDF 没产出**：第 6 步失败不阻断成片。检查 reportlab 是否装、`annotated.json` 的 `highlights` 是否为空。
补跑第 6 步（见 01 的 `VIDEO_REF` 命令，去掉 `VIDEO_REF` 即可）。

**PDF 标题变成 `c19_test2_part1`**：输出 stem 不符合 `^c\d+_test\d+_part\d+$`，sed 未命中。
`run_one.sh` 会在出片前拦下并报错。

**背景图变深蓝**：误跑了 `00_background.py`（`batch.sh` 会调它）。
`assets/background.png` 是已去水印的浅色图，必须从备份恢复。**Skill 永不调用这两者**。

**说明段数量突然变多 / 文本是完整对话句**：极可能是 `01_align.py` 的固定阈值误伤长句，
长对话被判「未匹配」后掉进说明段只播一遍无字幕。对照 `docs/07` 第六节检查自适应阈值
`sentence_max_sec(n)=max(15, n/2.2*1.5+3)` 与 `MAX_WORD_GAP_SEC=5.0`。

**对齐率突然 <90%、未匹配句是 `.` 或 `BELLA: OK .` 这类碎片**：官方 tapescript 的
`. . .`（空格分隔省略号）被 `split_original_sentences()` 的 `(?<=[.!?])\s+` 在每个点后
各切一刀。2026-11-09 已修复（切句前 `re.sub(r"\.\s*\.\s*\.", "…", text)`），详见
`docs/07` 第八节。**先查 `data/sentences.json` 未匹配句的形态**，绝不调阈值、绝不盲重跑；
改通用脚本前先用新旧切句器对 `resource/` 全部原文做回归。

**成片出现「有字幕无人声」**：锚点错位。看 `timeline.json` 是否有 >20s 的 event，
再查 `sentences.json` 该句词级时间戳是否出现几十秒跳变。对照 `docs/07` 第一~五节。

**`data/*.json` 与成片不对应**：多篇并行或质检延后导致被覆盖。严格串行、逐篇闭环。

## 重跑策略

- **点名即强制**：`--only "test2 part1"` 会强制重跑，不管 `out/` 里有没有成片。
- **`--force`**：全量强制重跑（谨慎，5 篇约 25 分钟 + LLM 费用）。
- **mtime 触发**：`max(md.mtime, mp3.mtime) > mp4.mtime + 2s` 判为 `rerun`。
  若用户常 `touch` md，会误触发；可改为「只有 `--force` / 点名才重跑」。
- **BLOCK 后重跑**：成片保留不删不覆盖，排查修复后重跑本篇会覆盖同名文件（这是预期行为）。

## `partial`（成片在、`_vocab.pdf` 缺/空）

默认**整篇重跑**。原因：`06_vocab_pdf.py` 依赖 `data/annotated.json`，而该路径是共享的，
批量跑完后无法保证仍对应本篇；单独跑 06 可能把别的篇的考点词写进 PDF。
若确认 `annotated.json` 仍是本篇（刚出完片、没跑下一篇），也可只补第 6 步，见 01。

## `VIDEO_REF` 回填（发布后）

发布拿到 BV 号后**只重跑第 6 步**，不重跑整条流水线。必须在 `data/annotated.json` 仍对应本篇时做；
批量出片前先 `cp data/annotated.json /tmp/annotated_<stem>.json` 备份（不进仓库），
再显式传 `ANNOTATED=/tmp/annotated_<stem>.json`。命令见 01。

## 为什么禁用 `batch.sh`

`ielts-video/batch.sh` 存在三个已知问题：

1. `export OUT="$OUT_MP4"` 与 `01_align.py` / `02_annotate.py` 用 `OUT` 表示 `sentences.json` / `annotated.json` 输出路径冲突；
2. 会调用 `00_background.py` 把浅色 `assets/background.png` 覆盖成深蓝渐变；
3. PDF 标题用原始 stem（`MATERIAL_NAME="$NAME"`），没有 `剑19 TestX PartY` 的 sed 派生；且脚本不 `cd`，必须从项目根调用。

本 Skill 的 `run_one.sh` 是对 `run.sh` 的最小包装，串行由上层 agent 逐篇驱动，
既避开这些问题，也满足决策 3B「每篇抽帧人工确认」的交互需要。

## 案例：每句末尾丢半个音节（2026-11-14）

现象：用户验收剑18 Test4 Part3，听感是「每句话最后一个音节被切掉一半」，但 17 项 QA 全 PASS、三帧画面正常。

排查顺序（先排除、再定位）：

1. **受控实验排除编码层**：用同样的 `-ss/-t + aformat + libmp3lame` 切 1/2/3/5s，ffprobe 与解码波形显示时长误差 0ms、尾部 40ms 与原音频逐样本一致 → **不是 ffmpeg/MP3 裁尾**，不要去调 `-ss`/`-t` 或换编码器。
2. **定位边界层**：`01_align.py` 的 `end = w_words[last_wp][2]`（whisper 词级 end），无任何余量。对 54 句做 end 之后能量扫描：77.4% 仍有显著人声，残留中位 140ms / P90 340ms / 最大 400ms；波形图上红线处是满幅硬切、右侧仍有成串音节。
3. **方案对比**：固定 +150/200/300/350/400/450ms 与「按能量自适应」逐档模拟。自适应（+0.10 下限、+0.45 上限、clamp 到下一正文句）把残留 >50ms 的句数从 43 降到 0，且比固定 300ms 少增 11s。

修复落在 `03_rebuild_audio.py`（播放层），**不动 `01_align.py`（对齐真值要保持词级精度）**；说明段起点需让位，否则紧邻说明段的句子补不到尾音。详见 SKILL 铁律 27 与 [02](02-qa-rules.md)。

### 二次校准：用户补充的 1855 实例（同日）

用户又截图指出「Guernsey was actually his third place of exile and he landed there in **1855**.」末尾丢半个音节。
**先定位篇目再动手**：这句在 `resource/c18_test4_part4.md`（Victor Hugo 独白），属于 **Test4 Part4**，不是上一轮验证的 Part3；
而 Part4 成片是 11-10 用**未修复**逻辑生成的旧片，所以仍丢音 —— 上一轮只重跑了 Part3，不能据此说「已全面修好」。

定点复验（原音频 16k mono 逐窗 RMS + small/medium 双模型转写）：

- small 把 `1855` 词尾标在 **184.160s**，高能量人声延续到 184.52s、低电平尾音到 184.62s；medium 给 184.580s。
  真实结束约 +490ms，旧上限 **0.45s** 恰好把它截断 → 上限/扫描范围放宽到 **0.60 / 0.80s**。
- 同篇第 15 句「…to be a **'work of art'**.」更严重：引号让 `SequenceMatcher` 漏匹配 `work`/`art`，
  `end` 落在 242.780，而 "art." 从 242.88 响到 243.36（RMS 0.14~0.37，比句内均值还高）——**整整一个词被砍掉**。
  根因是「连续 3 个 20ms 静音窗就判语音结束」被词间辅音闭塞 / 引号停顿骗到 → 放宽到 **6 窗（120ms）**。
  字幕文本始终取自完整原文，`words` 只用于词级时间戳，**不会缺字**；因此不需要改 `01_align.py`，也不换 medium（铁律 3）。

修复后 Part4：`1855` 补 +490ms、右边界后残留 **0ms**；`'art.'` 补到上限 +600ms、残留降到 80ms；
全篇 33/33 补尾音、平均 +255ms；17 PASS / 0 BLOCK、4 帧通过、交付包按 20:57 重出。

**流程教训**：17 项 QA 全 PASS 也挡不住切片边界缺陷，人工抽听也会漏（用户先听到、我后定位）。
现已把音轨验收固化成 `scripts/tail_check.py`（原音频残留 / 成片末尾硬切 + 淡出检测 / 片段重叠 / 补尾音触顶截断），
逐篇出片后与 `qa_check.py` 并列必跑；用旧片回归验证：22/33 句残留、16 句硬切 → BLOCK，新片 → PASS。
判据与基线见 [02](02-qa-rules.md)，参数与铁律见 SKILL 铁律 27。

**误判案例（2026-11-14，剑17 全量重出首篇）**：`c17_test1_part3` 被判「1 句成片末尾硬切」→ 批量按铁律停批，未带着问题开下一篇。排查：该句 `Yes, dairy cows.` 的 `end` 与下一句 `start` 同为 218.780（**零空隙**），`tail_pad_sec=0` 物理上补不了尾音；成片末 30ms 取到的是 **40ms 淡出斜坡**（648.79→648.83 从 0.27 降到 0.009，之后静音），句中段偏轻使 `tail_ratio=1.055` 虚高，**不是硬切**。**不要去改音频或放宽 `HARD_CUT_RATIO`**：`tail_check.py` 增加 `fade_ratio = RMS(末10ms)/RMS(末40~30ms)`，淡出生效（<0.35）打 WARN「淡出豁免」，无淡出（≈1）才 BLOCK。回归：该篇 PASS、旧缺陷片仍 BLOCK（16 句真硬切、fade 中位 0.724）、剑18 T4 P3 基准仍 PASS。批量出片脚本里 `$VAR` 后紧跟中文全角标点会触发 bash `unbound variable`（变量名吞掉多字节字节），写 `${VAR}`.

**真缺陷案例（2026-11-14，剑17 全量重出 `c17_test2_part1` 连拦两次）**：两次都是**真丢音**，停批正确，不要按误判去放宽判据。

| 次序 | 现象 | 定点证据 | 处理 |
|---|---|---|---|
| ① 0.60s 上限 | 「1 句成片末尾硬切且无淡出」BLOCK | idx37 `In fact, there's Mrs Carroll.`：能量检测算出真实结束点约 +740ms，被 0.60s 上限截断；播放右边界后仍有 ~140ms 强人声（RMS 0.164 ≈ 句内 0.142），成片 `tail_ratio=0.826` / `fade_ratio=0.398` | 上限 0.60→0.90s、扫描 0.80→1.10s |
| ② 0.90s 上限 | 17 项 QA PASS、成片 0 硬切，**但第 4 项触顶截断 BLOCK** | idx15 `I'm used to working on a computer - I presume they're computerised?`：whisper 把 `computerised` 误拆成 `"computer eyes"`，词级 `end=151.780`，实际语音到 152.920（偏早 **1.14s**），下一句 `Oh yes.` 从 153.320 起。补到 0.90s 仍砍掉半个词；40ms 淡出把成片 `tail_ratio` 压成「淡出豁免」，第 3 项完全看不出来 | 上限 0.90→**1.50s**、扫描 1.10→**1.80s**；`tail_check.py` 新增第 [4] 项「补到硬上限且上限后仍有本句人声」 |

修复后该篇：68/78 句补尾音、平均 +211ms / 最大 **+1250ms**（未触顶），0 残留 / 0 重叠 / 0 硬切 / 0 触顶 → PASS；17 项 QA 17 PASS / 0 BLOCK。回归三场景：新片 PASS、剑18 T4 P4 旧缺陷片仍 BLOCK（16 句真硬切）、剑18 T4 P3 基准仍 PASS；另用「把 idx15 缩回 0.90s」的模拟时间轴验证第 [4] 项确实 BLOCK。**教训**：whisper 词级 `end` 偏早可达 1s 以上，禁止拍脑袋设「最多补 0.3~0.6s」；但两道兜底（6 窗静音 break + `hard_cap = 下一正文句 start−0.02s`）不能动，否则相邻片段重叠。批量重出 16 篇时给单篇脚本加 `data/*.json` 留档，后续只调音频参数可从留档恢复，省掉 whisper + LLM 标注。

**clamp 口径案例（2026-11-14，剑17 全量重出第 1 篇 `c17_test1_part1`）**：第 [4] 项报「1 句补尾音触顶」BLOCK，但**不是上限不够，是 clamp 跳过了短句**。idx67 `"It's on the 17th, from 10 a.m."` 的 `end=358.080`，紧接的 idx68 `"until 3."` 只有 0.46s（< `MIN_SENT_SEC=0.5`，不参与重组、没有 timeline 事件）；`03_rebuild_audio.py` 原先用「参与重组的下一句」当 clamp，直接跳到 idx69 的 360.20，补尾音一路吞到 359.58。whisper 转写证实 358.06→360.30 是连续语音（`10am` / `until` / `3am.`），即整句 `until 3.` 被混进上一句。

修法：clamp 用 `annotated` **所有**正文句的 `start`（含被过滤短句）；`tail_check.py` 的 `next_start` 同步改用 `sentences.json` 全量（原先只有 `first` 事件，既漏报重叠又会把短句人声误当残留）。回归：旧 clamp 成片被新口径抓到 `(67, 0.84s)` 真重叠 → BLOCK；剑18 T4 P4 旧缺陷片仍 BLOCK、剑18 T4 P3 基准仍 PASS。**注意**：短句被过滤导致内容不播是既有行为（旧片同样如此），本次不扩大范围改 `MIN_SENT_SEC`；但绝不允许补尾音跨过它。

**说明段误判案例（2026-11-14，剑17 T4 P2 篇末）**：最后一句 idx41 被第 [4] 项判「补到 1.50s 上限且上限后仍有 360ms 人声」BLOCK。定点用 whisper 转写 437.5~444.0 发现，438.18~442.14 实际是考试结束语 `"You now have half a minute to check your answers to part 2."`，被 `01_align.py` 错配成正文句 `"OK, now I'd like to …"`；`instructions.json` 的 (440.34, 472.63) 正是该说明段。也就是说补尾音扫到的 360ms 全是**读题人声**，本句没有丢音。

修法：`tail_check.py` 读 `instructions.json`，第 [4] 项对 `play_end` 落在说明段区间内的句子只记 WARN「说明段人声已排除」；不动 `03_rebuild_audio.py`（避免已完成 13 篇口径变化、不必全量重出）。回归四场景：该篇 PASS、0.90s 上限真缺陷模拟仍 BLOCK（idx15 的 play_end=152.68 不在说明段内）、剑18 T4 P4 旧缺陷片仍 BLOCK、剑18 T4 P3 基准仍 PASS。**已知局限**：这类错配的最后一句补尾音会混入少量读题人声（旧片同样如此），属对齐误差、不在句尾修复范围；禁止为消这个 WARN 去放宽硬上限或取消 clamp。

**偶发交付包失败（同日批量第 13 篇）**：`c17_test4_part1` 的 [6/6] `make_publish_kit.py` 返回 1，但手动原地重跑退出码 0、五文件齐全，前 12 篇均正常 → 判定为瞬时故障（脚本内有 ffprobe / 硬链接 / 文件写入分支），不是内容缺陷。单篇脚本已改成失败时打印完整输出；续跑重出该篇闭环后通过。**不要因为一次交付包失败就去改文案或换发布模式**。

## git 纪律

- `data/sentences.json`、`instructions.json`、`annotated.json` 是 **tracked** 文件，每次出片都会改动。
- Skill 不自动 `git add` / `commit`；QA BLOCK 后不要提交中间产物。
- 交付前提醒用户看 `git status`，由用户决定是否提交。
- 项目级 Skill 目录本身通过 `.gitignore` 的 `.workbuddy/*` + `!.workbuddy/skills/` 例外规则纳入版本控制；
  `.workbuddy/memory/` 仍被忽略。

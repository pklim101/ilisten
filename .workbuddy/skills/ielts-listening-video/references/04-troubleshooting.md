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

## git 纪律

- `data/sentences.json`、`instructions.json`、`annotated.json` 是 **tracked** 文件，每次出片都会改动。
- Skill 不自动 `git add` / `commit`；QA BLOCK 后不要提交中间产物。
- 交付前提醒用户看 `git status`，由用户决定是否提交。
- 项目级 Skill 目录本身通过 `.gitignore` 的 `.workbuddy/*` + `!.workbuddy/skills/` 例外规则纳入版本控制；
  `.workbuddy/memory/` 仍被忽略。

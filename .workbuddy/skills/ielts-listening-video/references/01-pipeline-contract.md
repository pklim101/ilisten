# 01 · 流水线契约（出片前必读）

## 唯一入口

```bash
bash ielts-video/run.sh <音频.mp3> <原文.md> <输出.mp4>
```

`run.sh` 先取参数、再 `cd "$(dirname "$0")/.."` 到项目根，Python 在 cd 后解析路径 →
**参数必须是相对项目根的路径或绝对路径**。Skill 统一传绝对路径。

**必须显式传全 3 个参数**。`run.sh` 的默认值指向 `resource/Test1 Part2.mp3`（该文件早已不存在），
裸跑会串到旧素材或直接失败。

## 六步真实顺序

| 步 | 脚本 | 环境变量（主要） | 输出 |
|---|---|---|---|
| 1 | `01_align.py` | `AUDIO, TEXT, OUT, OUT_INSTR, WHISPER_MODEL, GAP_THRESHOLD, MAX_SENT_SEC, MAX_WORD_GAP_SEC, ALIGN_WPS` | `data/sentences.json`, `data/instructions.json` |
| 2 | `02_annotate.py` | `DEEPSEEK_API_KEY`(必需), `DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, SENTENCES, OUT` | `data/annotated.json` |
| 3 | `03_rebuild_audio.py` | `AUDIO, ANNOTATED, INSTRUCTIONS, OUT_AUDIO, OUT_TIMELINE, REPEATS, REPEAT_GAP_SEC, SENT_GAP_SEC, MIN_SENT_SEC` | `data/rebuilt_audio.mp3`, `data/timeline.json`, `data/tmp_audio/` |
| 4 | `04_subtitles.py` | `TIMELINE, OUT_ASS, VIDEO_W/H` | `data/subtitles.ass` |
| 5 | `05_compose.py` | `AUDIO_REBUILT, ASS, BACKGROUND, OUT, VIDEO_W/H` | `out/<stem>.mp4` |
| 6 | `06_vocab_pdf.py` | `ANNOTATED, OUT_VOCAB_PDF, MATERIAL_NAME, VIDEO_REF` | `out/<stem>_vocab.pdf` |

⚠️ `run.sh` 的 echo 标签是 `[1/5]…[5/5]` 加一个 `[6/6]`，**标签数量不一致**，不要按标签数判断进度。
第 5 步失败会因 `set -euo pipefail` 直接退出；第 6 步失败**只警告、不阻断成片**（缺 reportlab 或 highlights 为空时）。

## 环境与常量

- Python：`/Users/sofia/.workbuddy/binaries/python/versions/3.13.12/bin/python3`（`run.sh` 写死）。
- ffmpeg：`imageio_ffmpeg.get_ffmpeg_exe()`；ffprobe 回退 `/Users/sofia/libo/local/miniconda3/bin/ffprobe`。
- 已装：`faster-whisper 1.2.1 / openai / imageio-ffmpeg / av 13.1.0 / Pillow / reportlab 5.1.0 / pypdf`。
- `run.sh` 自带：`HF_ENDPOINT=https://hf-mirror.com`、`HF_HUB_DISABLE_XET=1`、`WHISPER_MODEL=small`（**不要改 medium**，small 在「正文↔题目说明」边界更稳）。
- 默认阈值：`GAP_THRESHOLD=4.0`（不可 <3.0）、`MAX_SENT_SEC=15.0`、`MAX_WORD_GAP_SEC=5.0`、`ALIGN_WPS=2.2`；
  句长上限自适应 `sentence_max_sec(n) = max(15, n/2.2*1.5+3)`。
- 循环参数：`REPEATS=3`、`REPEAT_GAP_SEC=1.0`、`SENT_GAP_SEC=3.0`、`MIN_SENT_SEC=0.5`。
- 画面：`VIDEO_W/H=1920/1080`、`BACKGROUND=ielts-video/assets/background.png`。
- 外部服务：DeepSeek（`deepseek-chat`、temperature 0.2、json_object、10 句/批）、HuggingFace small 模型（首次需镜像下载）。

**换机器只需改两处常量**：`SKILL.md` 里记录的 `$PY` 与 `$SKILL`/`$ROOT`；
脚本内项目根由 `Path(__file__).resolve().parents[4]`（Python）/ `$(dirname "$0")/../../../..`（Bash）自动推导，无需改。

## 命名派生（写在 `run.sh` 内，Skill 不要自己拼）

```bash
BASE_NAME=$(basename "$OUT_MP4" .mp4)                       # c19_test2_part1
MATERIAL_NAME=$(echo "$BASE_NAME" | sed -E 's/c([0-9]+)_test([0-9]+)_part([0-9]+)/剑\1 Test\2 Part\3/I')
OUT_VOCAB_PDF="${OUT_MP4%.mp4}_vocab.pdf"
```

实测 `c19_test1_part2 → 剑19 Test1 Part2`、`c19_test2_part1 → 剑19 Test2 Part1`。
**stem 不符合 `^c\d+_test\d+_part\d+$` 时 sed 不命中，PDF 标题会退化成原始 stem** → 扫描脚本强制校验。

`VIDEO_REF`（BV 号）可选、默认空。发布后单独重跑第 6 步回填，不重跑整条流水线：

```bash
source ~/.bash_profile   # 06 不需要密钥，但保持环境一致
MATERIAL_NAME="剑19 Test2 Part1" VIDEO_REF="BV1xx..." \
OUT_VOCAB_PDF="<ROOT>/ielts-video/out/c19_test2_part1_vocab.pdf" \
$PY ielts-video/scripts/06_vocab_pdf.py
```
前提：`data/annotated.json` 仍对应本篇（批量跑完会被覆盖，先 `cp` 到 `/tmp` 备份，不进仓库）。

## 共享中间产物（必须串行的原因）

`ielts-video/data/` 下路径固定，逐篇覆盖：

```
sentences.json  instructions.json  annotated.json
rebuilt_audio.mp3  timeline.json  subtitles.ass  tmp_audio/
```

其中 `sentences.json` / `instructions.json` / `annotated.json` 是 **git tracked** 文件，每次出片都会改动。
`tmp_audio/` 被 gitignore 排除，`03_rebuild_audio.py` 会自行覆盖，无需手动清理。

## 素材与状态判定规则（scan_materials.py 实现，v2.0）

1. 只扫 `ROOT/resource/` 的**直接子文件**，只认 `.mp3` / `.md`；stem 严格 `^c(\d+)_test(\d+)_part(\d+)$`（`re.I`，输出统一小写）。
   隐藏文件、子目录、`wav/m4a/txt/png`、`qa_*.png`、`*_v1/v2.mp4`、`.DS_Store` 全部排除。
2. **音频是唯一必需输入**（v2.0 起）：
   - 有 `.mp3` + `.md` → 常规路径；
   - **只有 `.mp3`** → `needs_transcript`，先执行阶段 1.5 补齐 `.md`（见 `05-transcript-fetch.md`）；
   - 只有 `.md`（如 `c19_test2_part2.md` 当前缺 mp3）→ `[WARN]` 跳过，不报错（无音频既不能出片也无法校验原文）。
3. 已有成片：`out/<stem>.mp4 > 1MB` 且 `out/<stem>_vocab.pdf > 10KB`（历史成片 26~76MB、PDF 46~60KB）。
   只匹配**精确** `<stem>.mp4`，`_v1`/`_v2` 不算。
4. 状态：`done`（齐全且源未更新）/ `todo`（无成片 / 点名 / `--force`）/ `partial`（成片在、PDF 缺，按整篇重跑）/
   `rerun`（`max(md.mtime, mp3.mtime) > mp4.mtime + 2.0`）/ **`needs_transcript`**（缺 `.md` 且本篇确实要跑）。
   已成片齐全但 `.md` 被删的情况保持 `done`，不强行补原文。
5. 点名（`--only`）优先级最高，**点名即强制**；从 token 抽 `(test, part)` 两个数字到已扫到的 stems 里找，
   兼容 `c19_test2_part1` / `test2_part1` / `test2 part1` / `test2/part1` / `2 1` / 未来 `c20_...`；
   找不到退出码 3，**绝不拼造不存在的路径**。v2.0 起「只有音频」的篇也能被点名命中。
6. 脚本只读，永不调用 `run.sh`；`--dry-run` 是语义标注，用于先给人看清单。
   待办打印时，对 `needs_transcript` 的篇会额外给出阶段 1.5 的命令。

## 阶段 1.5 / 阶段 6 的额外依赖（不影响上面 6 步流水线）

- **阶段 1.5**（`fetch_transcript.py`）：`DEEPSEEK_API_KEY` + `HF_ENDPOINT` + `HF_HUB_DISABLE_XET`；
  DeepSeek `POST /responses`（`deepseek-v4-pro` + `tools:[{"type":"web_search"}]` + `reasoning.effort=high`），
  抽取用 `deepseek-chat` + `json_object`；`faster-whisper small` 转写音频做交叉校验。
  现有 `02_annotate.py` 仍用 `deepseek-chat`，**不要因模型改名去改它**（实测仍 200 可用）。
  详见 `05-transcript-fetch.md`。
- **阶段 6**（`make_publish_kit.py`）：纯本地，无外部调用；用 ffprobe（回退 `/Users/sofia/libo/local/miniconda3/bin/ffprobe`，
  再回退 `data/timeline.json`）读成片时长。只产出 `/tmp` 的投稿入参 JSON，不投稿。详见 `06-publish.md`。

## 原文格式要求（`01_align.py` 已实现）

- 过滤以 `PART` / `SECTION` / `Test` 开头的行、以 `ANNOUNCER:` / `NARRATOR:` 开头的考试指令行。
- **说话人标签（`WOMAN:` / `COLIN:` / `SPEAKER:`）是正文，必须保留**。
- 按 `(?<=[.!?])\s+` 切句；考点词必须是原句连续子串。
- 不同 Part 格式不统一（Part4 用 `ANNOUNCER:`、Test2 P1 用 `NARRATOR:`），新素材先单独跑第 1 步看日志，
  比整条流水线跑完才返工便宜得多（见 `docs/07` 第七节）。

## 硬性约束（PRD / docs/07）

- 不加片头片尾；音频零改动、零遗漏、不裁剪。
- 题目说明 / 读题静音段**原样播一遍、不循环、不显示字幕**。
- 正文逐句 ×3 遍：挖空 / 全文+橙色高亮 / 英+中；三遍间停 1s、句后停 3s，停顿期字幕保持。
- 对齐异常三信号：说明段数量突然变多 / 说明段文本是完整对话句 / 对齐率 <90% → 停下检查再出片。

# 雅思听力学习视频自动生成流水线

把「雅思听力音频 + 原文文本」自动生成「三遍循环学习视频」，完整还原考试音频节奏：

> 📚 **完整文档**见上级目录 `docs/`（共 7 份，按用途分组）：
>
> **工程与运维**
> - [`docs/01-产品需求文档.md`](../docs/01-产品需求文档.md) — 产品范围、三遍循环设计、视觉规格与验收标准。
> - [`docs/02-技术架构文档.md`](../docs/02-技术架构文档.md) — 流水线架构（01~06）、中间产物契约与踩坑记录。
> - [`docs/03-维护文档.md`](../docs/03-维护文档.md) — 日常运维、参数速查、故障排查表与 QA 清单。
> - [`docs/07-静音错位问题诊断.md`](../docs/07-静音错位问题诊断.md) — 「锚点错位」根因、自适应阈值修复与出片质检/抽帧规范（出片前必读）。
>
> **内容与运营**
> - [`docs/04-B站运营策划方案.md`](../docs/04-B站运营策划方案.md) — 真题引流 70% + 原创信任 30% 的双轨内容与 90 天计划。
> - [`docs/05-方法论文脚本与封面文案.md`](../docs/05-方法论文脚本与封面文案.md) — 信任轨首条视频《精听不是听写》完整脚本与封面模板。
> - [`docs/06-账号名称标题钩子与衍生内容策划.md`](../docs/06-账号名称标题钩子与衍生内容策划.md) — 账号名、标题公式、考点词 PDF 钩子与 16 个衍生选题。

## 核心逻辑（V3）

- **题目说明/考试指令段**：原样播放一遍（含读题静音），不循环、不显示字幕
- **正文句**：逐句循环 3 遍
  - 第 1 遍 = 考点词挖空（下划线填空）
  - 第 2 遍 = 完整原文 + 考点词高亮（橙色）
  - 第 3 遍 = 英文原文 + 中文翻译
  - 三遍之间停顿 **1 秒**（停顿期间字幕保持显示）
  - 每句三遍后停顿 **3 秒**，再进入下一句
- **不改动原始音频材料**，音频内容无遗漏
- 清新自然的雅思主题背景图（淡蓝天空 + 白云 + 田野水面），深色字幕 + 白色描边

## 目录结构

```
ielts-video/
├── run.sh                  # 单篇一键流水线入口
├── batch.sh                # 批量目录处理（拖一个文件夹全自动出片）
├── scripts/
│   ├── ffmpeg_helper.py    # ffmpeg 定位（imageio-ffmpeg 自带二进制）
│   ├── 01_align.py         # 对齐：faster-whisper 时间戳 + 区分题目说明/正文
│   ├── 02_annotate.py      # LLM 标注：DeepSeek 翻译 + 考点词
│   ├── 03_rebuild_audio.py # 音频重组：指令一遍 + 正文×3遍（1s/3s停顿）
│   ├── 04_subtitles.py     # 三态 ASS 字幕（深色文字 + 橙色高亮）
│   ├── 05_compose.py       # FFmpeg 合成出片（背景图，无片头片尾）
│   └── 06_vocab_pdf.py     # 考点词表 PDF（本地复盘资料，不上传 B站）
├── assets/                 # 素材库：生产背景 + A/B 配色 + 可复用底板 + 版式样张
│   ├── background.png      # 蓝天白云原图，成片与封面共用（禁止覆盖）
│   ├── cover_palettes.json # A 学术蓝 / B 剑桥红配色真值源
│   ├── backgrounds/        # 5 张无文字底板
│   └── covers/             # 3 张含文字版式样张
├── data/                   # 中间产物
└── out/                    # <stem>.mp4 + <stem>_vocab.pdf + <stem>_cover.png
```

## 快速开始

```bash
# 单篇（必须显式传全 3 个参数；密钥从 ~/.bash_profile 读取，不要写进命令历史）
source ~/.bash_profile
bash ielts-video/run.sh \
  resource/c19_test1_part2.mp3 \
  resource/c19_test1_part2.md \
  ielts-video/out/c19_test1_part2.mp4
```

每篇产出 3 个文件（均在 `ielts-video/out/`）：
- `<stem>.mp4` 成片
- `<stem>_vocab.pdf` 考点词表（**本地复盘资料，PDF 不上传 B站**；资料闭环零载体依赖：逐句考点在视频第 2 遍高亮字幕，本期速览见置顶评论。自动回复需 1000 粉、专栏需 LV1+，均不作为链路一环）
- `<stem>_cover.png` B站封面（1146×717，A 学术蓝 + `assets/background.png` 成片同款背景；由第 7 步生成，**不上传则 B站自动抽帧，多半抽到纯字幕画面**）

> ⚠️ `batch.sh` 已废弃（`OUT` 变量冲突、会覆盖浅色背景图、PDF 标题退化），请勿使用。
> 批量出片请用项目级 Skill：`.workbuddy/skills/ielts-listening-video/`
> （自动扫描 `resource/` 未出片素材、串行出片、脚本质检 + 抽帧确认）。

## 环境依赖

```bash
pip install faster-whisper "av==13.1.0" openai imageio-ffmpeg pillow
```

> ⚠️ 首次运行需从 HuggingFace 下载 whisper 模型。国内网络设置：
> `export HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1`

## 关键参数（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WHISPER_MODEL` | `small` | whisper 模型（small 在"正文↔指令"边界表现更稳） |
| `GAP_THRESHOLD` | `4.0` | 判定"题目说明段"的最小空隙秒数 |
| `REPEAT_GAP_SEC` | `1.0` | 三遍之间的停顿秒数 |
| `SENT_GAP_SEC` | `3.0` | 每句三遍后的停顿秒数 |
| `REPEATS` | `3` | 每句播放遍数 |
| `BACKGROUND` | 背景图路径 | 留空用默认清新背景图 |
| `VIDEO_W`/`VIDEO_H` | `1920`/`1080` | 输出分辨率 |

## 技术要点

1. **区分题目说明与正文**：用 `difflib.SequenceMatcher` 把原文（纯正文）对齐到 whisper 转写，正文之外 > `GAP_THRESHOLD` 的空隙识别为题目说明段。
2. **模型选择**：`small` 模型在雅思听力"正文↔题目说明"边界处比 `medium` 转写更完整（medium 会漏掉边界处的短句）。
3. **字幕配色**：清新浅色背景 → 深色文字 + 白色描边 + 橙色高亮（浅色背景上比黄色更醒目）。
4. **句尾尾音保护**：faster-whisper 词级 `end` 系统性偏早（误拆词时可达 1s 以上），`03_rebuild_audio.py`
   按原音频能量把播放右边界延到真实语音结束点（+0.10~1.50s，clamp 到下一正文句），并给正文片段
   末尾加 40ms 淡出。**出片后必须跑**项目级 Skill 的 `tail_check.py`（句尾尾音验收，17 项 QA 之外）
   ——17 项全 PASS 也看不出音频被硬切；判据与基线见
   `.workbuddy/skills/ielts-listening-video/references/02-qa-rules.md`。

## 素材库 `assets/`

> 2026-11-09 归档：`out/` 被 `.gitignore` 整体忽略，封面样张放那里会随清理丢失；
> 统一收进 `assets/` 随仓库长期保存。生产背景图保持原名原路径、内容一字未动。

| 文件 | 规格 | 用途 |
|---|---|---|
| `background.png` | 1536×1024 | **生产唯一默认背景**：淡蓝天空 + 白云 + 田野，已手工去水印。`05_compose.py` 与 `make_cover.py` 都读它，**禁止覆盖/替换**，绝不运行 `00_background.py` |
| `intro_bg.png` | 1920×1080 | 深蓝片头卡片（历史素材，当前流水线无片头） |
| `A_serene__fresh_and_calming_st_*.png` | 1536×1024 | 与 `background.png` 完全同内容的 AI 生图原名副本（md5 一致），已被 `.gitignore` 忽略，不入库 |
| `cover_palettes.json` | — | **配色真值源**：A 学术蓝 / B 剑桥红全套 RGB + HEX，源自 [`docs/05`](../docs/05-方法论文脚本与封面文案.md)；`make_cover.py` 优先读它，缺失或损坏才回退内置值 |
| `backgrounds/bg_sky_original.png` | 1536×1024 | 蓝天白云原图归档副本（与 `background.png` 同 md5） |
| `backgrounds/bg_sky_academic_blue.png` | 1146×717 | **2026-11-08 定稿背景**：风景 + 左侧深蓝渐变蒙版（无文字） |
| `backgrounds/bg_sky_cambridge_red.png` | 1146×717 | 同版式 B 剑桥红蒙版，A/B 对照备用 |
| `backgrounds/bg_academic_blue_flat.png` | 1146×717 | A 学术蓝纯色渐变底板（无文字） |
| `backgrounds/bg_cambridge_red_flat.png` | 1146×717 | B 剑桥红纯色渐变底板（无文字） |
| `covers/sample_*_academic_blue_flat.png` | 1146×717 | A 版式样张（含文字） |
| `covers/sample_*_cambridge_red_flat.png` | 1146×717 | B 版式样张（含文字） |
| `covers/sample_*_final_photo_blue.png` | 1146×717 | **已发布定稿封面**样张（风景 + A 学术蓝） |

### 复用方式

```bash
cd /Users/sofia/libo/personknowlege/ilisten
PY=/Users/sofia/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SKILL=.workbuddy/skills/ielts-listening-video/scripts

# 重建全部底板与样张（幂等；改完色卡后加 --force 覆盖）
$PY "$SKILL/make_asset_plates.py" --root "$PWD"

# 换配色：只改 assets/cover_palettes.json，再重出底板，不要改代码
# 用素材库已合成底板直接出封面（必须加 --no-mask，防二次叠加蒙版）
$PY "$SKILL/make_cover.py" --stem c19_test2_part2 \
  --out ielts-video/out/c19_test2_part2_cover.png \
  --background ielts-video/assets/backgrounds/bg_sky_academic_blue.png --no-mask

# 剑桥红 A/B 对照（纯色渐变）
$PY "$SKILL/make_cover.py" --stem c19_test2_part2 --out /tmp/cover_red.png --palette red --flat
```

> ⚠️ 传给 `--background` 的若是**原始风景图**（`background.png` / `bg_sky_original.png`），
> 不要加 `--no-mask`——脚本自己叠蒙版托住白字；若是 `backgrounds/` 里已合成好的底板，必须加。
> `backgrounds/` 是无文字底板，主要给 Figma / 稿定 / 剪辑工具复用；`covers/` 是含文字的版式参考。
> 已验证：用 `bg_sky_academic_blue.png --no-mask` 直出的封面与已发布定稿**像素完全一致**。

## 版本记录

- **V1**：MVP 初版（small 模型、纯色背景、每句×3遍+3秒停顿，无指令区分）
- **V2**：优化挖空 + 批量脚本 + medium 模型 + 渐变背景 + 片头片尾（后按用户要求废弃）
- **V3**：区分题目说明/正文、指令一遍+正文三遍循环（1s/3s停顿）、清新背景图、深色字幕、无片头片尾、small 模型
- **2026-11-09**：封面生成接入流水线（第 7 步），A 学术蓝 / B 剑桥红配色与 5 张背景底板、3 张版式样张归档进 `assets/` 素材库
- **2026-11-14**：修复「每句末尾丢半个音节」（whisper 词级 `end` 偏早），新增 `tail_check.py` 句尾尾音验收；剑17 16 篇按最终参数全量重出

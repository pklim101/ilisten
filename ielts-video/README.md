# 雅思听力学习视频自动生成流水线

把「雅思听力音频 + 原文文本」自动生成「三遍循环学习视频」，完整还原考试音频节奏：

> 📚 **完整文档**见上级目录 `docs/`：
> - [`docs/01-产品需求文档.md`](../docs/01-产品需求文档.md) — 需求、验收标准、版本演进
> - [`docs/02-技术架构文档.md`](../docs/02-技术架构文档.md) — 架构、模块设计、踩坑记录
> - [`docs/03-维护文档.md`](../docs/03-维护文档.md) — 运维、参数调整、故障排查

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
│   └── 05_compose.py       # FFmpeg 合成出片（背景图，无片头片尾）
├── assets/                 # 背景图等素材
├── data/                   # 中间产物
└── out/                    # 成片输出
```

## 快速开始

```bash
# 单篇
export DEEPSEEK_API_KEY="sk-xxx"
bash ielts-video/run.sh "resource/Test1 Part2.mp3" resource/c19_test1_part2.md out/c19_test1_part2.mp4

# 批量（音频+同名原文放一个文件夹）
bash ielts-video/batch.sh resource out
```

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

## 版本记录

- **V1**：MVP 初版（small 模型、纯色背景、每句×3遍+3秒停顿，无指令区分）
- **V2**：优化挖空 + 批量脚本 + medium 模型 + 渐变背景 + 片头片尾（后按用户要求废弃）
- **V3**：区分题目说明/正文、指令一遍+正文三遍循环（1s/3s停顿）、清新背景图、深色字幕、无片头片尾、small 模型

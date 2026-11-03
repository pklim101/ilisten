#!/usr/bin/env bash
# 雅思听力学习视频一键流水线
# 用法：./run.sh [音频.mp3] [原文.md] [输出.mp4]
#
# 示例：
#   ./run.sh "resource/Test1 Part2.mp3" resource/c19_test1_part2.md out/c19_test1_part2.mp4
#
# 依赖：Python3（含 faster-whisper, openai, imageio-ffmpeg, av）
# 环境变量：DEEPSEEK_API_KEY（必需）、HF_ENDPOINT、HF_HUB_DISABLE_XET

set -euo pipefail

PY="/Users/sofia/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
AUDIO="${1:-resource/Test1 Part2.mp3}"
TEXT="${2:-resource/c19_test1_part2.md}"
OUT_MP4="${3:-ielts-video/out/c19_test1_part2.mp4}"

export AUDIO="$AUDIO"
export TEXT="$TEXT"
export OUT_MP4="$OUT_MP4"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
export WHISPER_MODEL="${WHISPER_MODEL:-small}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?请先设置 DEEPSEEK_API_KEY 环境变量}"

cd "$(dirname "$0")/.."

echo "==> [1/5] 音频对齐（词级时间戳）"
$PY ielts-video/scripts/01_align.py

echo "==> [2/5] LLM 标注（翻译 + 考点词）"
$PY ielts-video/scripts/02_annotate.py

echo "==> [3/5] 音频重组（×3遍 + 3秒停顿）"
$PY ielts-video/scripts/03_rebuild_audio.py

echo "==> [4/5] 生成三态字幕"
$PY ielts-video/scripts/04_subtitles.py

echo "==> [5/5] 合成出片"
OUT="$OUT_MP4" $PY ielts-video/scripts/05_compose.py

echo "==> [6/6] 生成考点词表 PDF（B站引流资料）"
BASE_NAME="$(basename "$OUT_MP4" .mp4)"
MATERIAL_NAME="${MATERIAL_NAME:-$(echo "$BASE_NAME" | sed -E 's/c([0-9]+)_test([0-9]+)_part([0-9]+)/剑\1 Test\2 Part\3/I')}"
MATERIAL_NAME="$MATERIAL_NAME" \
OUT_VOCAB_PDF="${OUT_MP4%.mp4}_vocab.pdf" \
$PY ielts-video/scripts/06_vocab_pdf.py \
  || echo "⚠️  考点词 PDF 生成失败（不影响成片，可稍后单独重跑 06_vocab_pdf.py）"

echo ""
echo "完成！成片位于：$OUT_MP4"
echo "考点词表：${OUT_MP4%.mp4}_vocab.pdf（发布后请设 VIDEO_REF=BV号 重新生成以回填）"

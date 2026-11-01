#!/usr/bin/env bash
# 雅思听力学习视频 —— 批量目录处理
#
# 扫描指定目录（默认 resource/）内所有音频文件，找到与音频同名的
# 文本文件（.md/.txt），逐个跑完整流水线出片。
#
# 用法：
#   ./batch.sh [素材目录] [输出目录]
#
# 示例：
#   ./batch.sh resource out
#
# 命名约定：音频 "Test1 Part2.mp3" 对应原文 "Test1 Part2.md" 或 "Test1 Part2.txt"
#
# 依赖：Python3（faster-whisper/openai/imageio-ffmpeg/av/pillow）
# 环境变量：DEEPSEEK_API_KEY（必需）、WHISPER_MODEL（默认 medium）

set -euo pipefail

PY="/Users/sofia/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
SRC_DIR="${1:-resource}"
OUT_DIR="${2:-ielts-video/out}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
export WHISPER_MODEL="${WHISPER_MODEL:-small}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?请先设置 DEEPSEEK_API_KEY 环境变量}"

mkdir -p "$OUT_DIR"

# 生成背景图（若已存在则跳过）
echo "==> 准备背景图/片头底图"
$PY "$SCRIPT_DIR/scripts/00_background.py" || echo "    (跳过背景图生成，使用纯色背景)"

# 收集音频文件
mapfile -t AUDIOS < <(find "$SRC_DIR" -type f \( -iname "*.mp3" -o -iname "*.m4a" -o -iname "*.wav" \) | sort)

if [ ${#AUDIOS[@]} -eq 0 ]; then
  echo "[错误] 目录 $SRC_DIR 下没有找到音频文件（.mp3/.m4a/.wav）"
  exit 1
fi

echo ""
echo "==> 发现 ${#AUDIOS[@]} 个音频文件，开始批量处理"
echo ""

SUCCESS=0
FAILED=0

for AUDIO in "${AUDIOS[@]}"; do
  BASENAME="$(basename "$AUDIO")"
  STEM="${BASENAME%.*}"   # 去掉扩展名
  NAME="$STEM"

  # 找同名文本
  TEXT=""
  for ext in md txt; do
    for cand in "$SRC_DIR/$STEM.$ext" "$SRC_DIR/${STEM}.${ext}"; do
      if [ -f "$cand" ]; then TEXT="$cand"; break; fi
    done
    [ -n "$TEXT" ] && break
  done

  if [ -z "$TEXT" ]; then
    echo "[跳过] $BASENAME —— 未找到同名原文（$STEM.md/.txt）"
    FAILED=$((FAILED + 1))
    continue
  fi

  OUT_MP4="$OUT_DIR/${STEM}.mp4"

  echo "──────────────────────────────────────────────"
  echo "▶ 处理：$BASENAME  (原文: $(basename "$TEXT"))"
  echo "  输出：$OUT_MP4"

  export AUDIO="$AUDIO"
  export TEXT="$TEXT"
  export OUT="$OUT_MP4"

  # 复用根目录 data 中间产物（每篇覆盖）
  export SENTENCES="$SCRIPT_DIR/data/sentences.json"
  export ANNOTATED="$SCRIPT_DIR/data/annotated.json"
  export OUT_AUDIO="$SCRIPT_DIR/data/rebuilt_audio.mp3"
  export OUT_TIMELINE="$SCRIPT_DIR/data/timeline.json"
  export OUT_ASS="$SCRIPT_DIR/data/subtitles.ass"
  export AUDIO_REBUILT="$OUT_AUDIO"
  export ASS="$OUT_ASS"
  export TITLE="$NAME"

  if $PY "$SCRIPT_DIR/scripts/01_align.py" \
    && $PY "$SCRIPT_DIR/scripts/02_annotate.py" \
    && $PY "$SCRIPT_DIR/scripts/03_rebuild_audio.py" \
    && $PY "$SCRIPT_DIR/scripts/04_subtitles.py" \
    && $PY "$SCRIPT_DIR/scripts/05_compose.py"; then
    echo "✅ 完成：$OUT_MP4"
    SUCCESS=$((SUCCESS + 1))
  else
    echo "❌ 失败：$BASENAME"
    FAILED=$((FAILED + 1))
  fi
  echo ""
done

echo "══════════════════════════════════════════════"
echo "批量处理结束：成功 $SUCCESS 个，失败/跳过 $FAILED 个"
echo "输出目录：$OUT_DIR"

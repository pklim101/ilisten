#!/usr/bin/env bash
# 单篇出片包装：取密钥 -> 校验 -> cd 项目根 -> exec run.sh（唯一正确入口）
# 自身不后台运行；后台由上层 Bash 工具 run_in_background: true 提供。
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "用法: run_one.sh <音频.mp3 绝对路径> <原文.md 绝对路径> <输出.mp4 绝对路径>" >&2
    exit 1
fi

AUDIO="$1"
TEXT="$2"
OUT_MP4="$3"

for f in "$AUDIO" "$TEXT"; do
    if [ ! -f "$f" ]; then
        echo "[错误] 素材不存在：${f}" >&2
        exit 1
    fi
done

STEM="$(basename "$OUT_MP4" .mp4)"
if ! echo "$STEM" | grep -Eq '^c[0-9]+_test[0-9]+_part[0-9]+$'; then
    echo "[错误] 输出 stem 不合规：${STEM} (须为 c19_test2_part1 形式，否则 PDF 标题会退化)" >&2
    exit 1
fi

# 非登录 shell 不会继承 ~/.bash_profile；source 时临时关掉 -e/-u，
# 避免 profile 内其他命令失败或未定义变量中断脚本。密钥绝不回显、绝不写文件。
set +eu
source ~/.bash_profile
set -eu
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "[错误] DEEPSEEK_API_KEY 未取到（只判空，不打印值）" >&2
    exit 1
fi

# scripts -> ielts-listening-video -> skills -> .workbuddy -> 项目根
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
mkdir -p "$(dirname "$OUT_MP4")"

echo "[run_one] 开始出片：${STEM}"
exec bash ielts-video/run.sh "$AUDIO" "$TEXT" "$OUT_MP4"

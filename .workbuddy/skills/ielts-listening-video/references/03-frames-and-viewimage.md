# 03 · 抽帧与 ViewImage 视觉确认

**由对话中的 agent 调用 `ViewImage` 工具，不是脚本、不是 shell。**
`qa_frames.py` 只负责算出时间点、抽出 PNG 到 `/tmp/ielts_qa_*`、打印绝对路径与检查清单。

## 抽帧命令

```bash
$FF -y -ss <t> -i <成片.mp4> -frames:v 1 /tmp/ielts_qa_<stem>_<tag>.png -loglevel error
```

`$FF` 来自 `imageio_ffmpeg.get_ffmpeg_exe()`（含 libass，能正确渲染 ASS）。
`-ss` 放在 `-i` 前是快速 seek，可能落在关键帧造成 ±1s 偏差 → 选点一律取事件中部。

## 三帧选点算法（全部从 `timeline.json` 现算，不硬编码秒数）

```python
def t_in(e):
    return e['start'] + 2.0 if (e['end'] - e['start']) > 4.5 else (e['start'] + e['end']) / 2
```

| 帧 | tag | 选点 | 检查项 |
|---|---|---|---|
| 1 | `1_blank` | `repeat==0` 且 `end-start>=4` 的事件，取**中位时长**那条，`t=t_in(e)` | 挖空下划线不重叠、垂直居中、左右留边距（不贴边不裁切）、背景铺满无黑边 |
| 2 | `2_bilingual` | 优先与帧 1 **同 `sentence_index`** 的 `repeat==2` 事件，否则取 `repeat==2` 的中位时长条 | 一行英文原文 + 一行中文翻译同屏、居中、不重叠、不被裁切 |
| 3 | `3_instruction` | 未被任何 event 覆盖且长度 `>=4.0s` 的间隙中取**最长者**，`t=(lo+hi)/2` | 画面**完全没有字幕**（无英文无中文），只有背景图 |

**间隙算法**：把 events 的 `(start,end)` 排序合并，从 `cur=0` 扫到 `total_duration`，
凡 `s-cur >= 4.0` 记为间隙。1s / 3s 停顿 <4s 被排除。
实测 `c19_test2_part1` 恰好剩 3 段：`[0,91.48)` / `36.4s` / `12.9s`，帧 3 落在 `t≈45.7s`。

**可选第 4 帧**：`--with-highlight` → `repeat==1` 且 `highlights` 非空的事件，检查橙色高亮。
橙色高亮平时由 `qa_check.py` 的 C4 查 ASS 标签（`\c&H000080FF&`）判定，属 BLOCK 级。

## 三条 ViewImage query 原文（自包含，工具看不到上下文）

对**每一个** PNG 路径各调用一次 `ViewImage`，`image_path` 传绝对路径，query 原样使用：

```
帧1: 这是一张雅思听力精听视频的抽帧。请只回答看到的事实：1) 画面上是否有英文字幕且含下划线填空；
2) 下划线是否与相邻单词或标点重叠；3) 字幕是否垂直居中、左右是否留有边距（不贴边、不被裁切）；
4) 背景是否铺满整幅画面、有没有黑边。

帧2: 这是一张雅思听力精听视频的抽帧。请只回答看到的事实：画面上是否同时出现一行英文原文和一行中文翻译，
两行是否都在画面内、居中、不重叠、不被裁切。

帧3: 这是一张雅思听力精听视频的抽帧。请只回答看到的事实：这一帧画面上是否完全没有字幕
（既无英文也无中文），只有背景图。如果出现任何文字请指出内容。
```

## 时序铁律

`data/*.json` 会被下一篇覆盖 → `qa_check.py` + `qa_frames.py` + 3 次 ViewImage
必须在**下一篇启动前**全部完成。批量任务逐篇闭环，不允许「先全部出片、最后统一质检」。

## 异常 → 处置映射

任一帧不符 = **BLOCK**：不得交付、不得开始下一篇，成片保留不删，按此表排查后重跑本篇。

| 看到的现象 | 排查方向 | 动作 |
|---|---|---|
| 挖空下划线重叠 / 贴边被裁 | `04_subtitles.py` 的 `wrap_text` / `apply_highlights` / `MAX_W_EN` | 修后重跑本篇 |
| 无橙色高亮 | `02_annotate.py` 的 `highlights` 为空、`04_subtitles.py` 三态分支 | 修后重跑本篇 |
| 双语态缺中文 | `02_annotate.py` 的 `translation` 为空（DeepSeek 批次 JSON 解析失败兜底） | 查 02 日志，重跑本篇 |
| 说明段出现字幕 | `instructions.json` 误判、`03_rebuild_audio.py` 给说明段建了 event、`01_align.py` 过滤漏 | 对照 `docs/07` 第六/七节修 01，重跑本篇 |
| 背景有黑边 / 背景变深蓝 | `05_compose.py` 的 `increase+crop`；或 `00_background.py` 被误跑 | 确认未运行 `00_background.py`，必要时恢复 `assets/background.png` |
| 字幕整体偏移 | `timeline.total_duration` 与成片 duration 不一致（C3 应已 BLOCK） | 阻断，重跑 |

## 抽帧产物纪律

- 只写 `/tmp/ielts_qa_<stem>_<tag>.png`。**不得**写进 `resource/` 或 `ielts-video/out/`，
  **不得**以 `c19_` 开头命名，避免被 `scan_materials.py` 误判为素材或成片。
- 不落仓库、不生成质检报告文档（决策 4A）。
- 项目 `out/` 里另有 6 张历史 QA 参考帧 `qa_{instruction,intro,outro,pass1_blank,pass2_highlight,pass3_bilingual}.png`，
  可作人工对照，但本 Skill 不复用它们做判定。

## 脚本质检不能替代的事

随机抽 2~3 个时间点**听音频**（尤其长句、开头句、说明段前后），确认「字幕与人声对得上」。
`docs/07` 的锚点错位缺陷正是靠人工抽听发现的；C1/C7 只能覆盖已知模式。

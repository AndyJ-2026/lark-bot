#!/usr/bin/env python3
"""
离线转写模块 — FunASR SenseVoice-Small
输入: PCM 文件 (16-bit signed LE, mono, 16000 Hz)
输出: 带时间戳的转写文本

比 Paraformer-large 质量更高，推理速度快 15 倍。
等 Qwen3-ASR 生态成熟后可切换。

用法: python3 transcribe_qwen.py --input recording.pcm --output transcript.txt
"""

import argparse
import os
import sys
import time
import warnings

import numpy as np

# 屏蔽噪音日志
os.environ["MODELSCOPE_LOG_LEVEL"] = "40"
os.environ["FUNASR_DISABLE_LOG"] = "1"
os.environ["TQDM_DISABLE"] = "1"
warnings.filterwarnings("ignore")

SAMPLE_RATE = 16000


def load_pcm(path):
    """读取 PCM 文件，返回 float32 numpy 数组"""
    raw = open(path, "rb").read()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(audio) / SAMPLE_RATE
    print(f"[transcribe] 音频时长: {duration:.1f}s", file=sys.stderr)
    return audio


def transcribe(audio):
    """用 SenseVoice-Small 转写音频"""
    from funasr import AutoModel

    print("[transcribe] 加载 SenseVoice-Small 模型...", file=sys.stderr)
    t0 = time.time()

    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        vad_kwargs={"max_single_segment_time": 30000},
        disable_update=True,
    )

    print(f"[transcribe] 模型加载完成 ({time.time() - t0:.1f}s)", file=sys.stderr)
    print("[transcribe] 开始转写...", file=sys.stderr)
    t1 = time.time()

    results = model.generate(
        input=audio,
        batch_size_s=300,
        disable_pbar=True,
    )

    elapsed = time.time() - t1
    print(f"[transcribe] 转写完成 ({elapsed:.1f}s)", file=sys.stderr)
    return results


def format_output(results, audio_duration):
    """将转写结果格式化为带时间戳的文本行"""
    lines = []
    for result in results:
        text = result.get("text", "").strip()
        if not text:
            continue
        # SenseVoice 可能返回带语言/情感标签的文本，清理掉
        # 格式可能是: <|zh|><|EMO_UNKNOWN|><|Event_UNK|><|woitn|>实际文本
        import re
        text = re.sub(r"<\|[^|]*\|>", "", text).strip()
        if text:
            lines.append(text)

    return lines


def main():
    parser = argparse.ArgumentParser(description="SenseVoice-Small 离线转写")
    parser.add_argument("--input", "-i", required=True, help="PCM 音频文件路径")
    parser.add_argument("--output", "-o", help="输出文本文件路径（不指定则输出到 stdout）")
    args = parser.parse_args()

    audio = load_pcm(args.input)

    if len(audio) < SAMPLE_RATE:
        print("[transcribe] 音频太短（<1s），跳过", file=sys.stderr)
        sys.exit(0)

    duration = len(audio) / SAMPLE_RATE
    results = transcribe(audio)
    lines = format_output(results, duration)

    if not lines:
        print("[transcribe] 未识别到任何语音", file=sys.stderr)
        sys.exit(0)

    output_text = "\n".join(lines)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(f"# 会议转写 {time.strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(output_text + "\n")
        print(f"[transcribe] 已保存: {args.output} ({len(lines)} 句)", file=sys.stderr)
    else:
        print(output_text)

    print(f"[transcribe] 共 {len(lines)} 句", file=sys.stderr)


if __name__ == "__main__":
    main()

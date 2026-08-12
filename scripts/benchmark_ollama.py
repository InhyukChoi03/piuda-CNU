#!/usr/bin/env python3
"""Measure first-token and total response latency for local Ollama models."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request


PROMPT = (
    "당신은 자립생활을 돕는 한국어 생활 도우미입니다. 생각 과정과 설명은 출력하지 마세요. "
    "사용자가 '지금 무엇을 해야 해?'라고 물었습니다. 미완료 일정은 '아침 약 복용', "
    "'점심 식사'입니다. 가장 먼저 할 일 하나만 따뜻한 한국어 한 문장, 35자 이내로 답하세요."
)


def generate(base_url: str, model: str) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(
            {
                "model": model,
                "prompt": PROMPT,
                "stream": True,
                "think": False,
                "keep_alive": "30m",
                "options": {
                    "temperature": 0.1,
                    "num_ctx": 1024,
                    "num_predict": 48,
                    "top_p": 0.8,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first_token = None
    chunks: list[str] = []
    final: dict = {}
    with urllib.request.urlopen(request, timeout=120) as response:
        for raw_line in response:
            item = json.loads(raw_line.decode("utf-8"))
            text = str(item.get("response", ""))
            if text and first_token is None:
                first_token = time.perf_counter() - started
            chunks.append(text)
            if item.get("done"):
                final = item
    wall = time.perf_counter() - started
    return {
        "first_token_s": round(first_token or wall, 3),
        "wall_s": round(wall, 3),
        "load_s": round(final.get("load_duration", 0) / 1_000_000_000, 3),
        "eval_count": final.get("eval_count", 0),
        "eval_tokens_s": round(
            final.get("eval_count", 0) / max(final.get("eval_duration", 0) / 1_000_000_000, 0.001),
            2,
        ),
        "reply": "".join(chunks).strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+", help="Ollama model names")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    for model in args.models:
        print(f"\n[{model}]")
        for run in range(1, args.runs + 1):
            result = generate(args.url, model)
            print(json.dumps({"run": run, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Streaming demo: prints each SSE delta the moment it arrives.

Run with the service already up:

    python scripts/demo_stream.py
    python scripts/demo_stream.py "换一个问题"

The point of this script is visibility, not throughput: every token is written
and flushed immediately, so the terminal shows text appearing progressively
instead of in one block. That is what a screen recording needs to capture.
"""

from __future__ import annotations

import json
import sys

import httpx

URL = "http://127.0.0.1:8000/chat/stream"
QUESTION = "用 30 个字解释什么是 RAG"


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else QUESTION
    payload = {"messages": [{"role": "user", "content": question}]}

    print(f"\n  > {question}\n")
    print("  " + "-" * 56)

    first = True
    with httpx.Client(timeout=120.0) as client:
        with client.stream("POST", URL, json=payload) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")
                print(f"  HTTP {resp.status_code}: {body}")
                return 1

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break

                event = json.loads(raw)
                kind = event.get("type")

                if kind == "meta":
                    print(f"  model={event.get('model')}  fallback={event.get('fallback_used')}")
                    print("  " + "-" * 56)
                elif kind == "delta":
                    if first:
                        print("  ", end="", flush=True)
                        first = False
                    # 逐 token 写出并立刻 flush，终端才会"一个一个字往外蹦"
                    print(event.get("content", ""), end="", flush=True)
                elif kind == "usage":
                    usage = event.get("usage", {})
                    print("\n  " + "-" * 56)
                    print(
                        f"  prompt={usage.get('prompt_tokens')}  "
                        f"completion={usage.get('completion_tokens')}  "
                        f"total={usage.get('total_tokens')}"
                    )
                elif kind == "error":
                    print(f"\n  [error] {event.get('message')}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

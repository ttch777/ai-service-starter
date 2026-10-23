"""延迟压测：对本地 /chat/stream 接口连续发请求，统计 TTFT 与端到端延迟。

用法（服务需先启动）：
    python scripts/bench.py --n 20

输出 P50 / P95，并直接生成一段可粘进 README 的 Markdown 表格。

为什么要测：没有数字的「实现了流式输出」在简历上等于没写。
TTFT（首字延迟）是用户真正感知的速度，端到端延迟是总成本的时间表达。
"""

import argparse
import json
import statistics
import sys
import time

import httpx

PROMPT = "用三句话说明什么是检索增强生成，不要分点。"


def percentile(values: list[float], p: float) -> float:
    """线性插值分位数，样本量小时比 statistics.quantiles 更稳。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p / 100
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def run_once(client: httpx.Client, base_url: str, prompt: str) -> dict:
    started = time.perf_counter()
    ttft = None
    parts: list[str] = []
    usage: dict = {}

    with client.stream(
        "POST",
        f"{base_url}/chat/stream",
        json={"messages": [{"role": "user", "content": prompt}]},
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            kind = event.get("type")
            if kind == "delta":
                if ttft is None:
                    ttft = time.perf_counter() - started
                parts.append(event.get("content", ""))
            elif kind == "usage":
                usage = event.get("usage", {})
            elif kind == "error":
                raise RuntimeError(event.get("detail"))

    total = time.perf_counter() - started
    return {
        "ttft": ttft if ttft is not None else total,
        "total": total,
        "completion_tokens": usage.get("completion_tokens", 0),
        "chars": len("".join(parts)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--n", type=int, default=20, help="正式样本数（预热不计入）")
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args()

    with httpx.Client(timeout=90.0) as client:
        try:
            health = client.get(f"{args.url}/health").json()
        except httpx.HTTPError as exc:
            print(f"[错误] 连不上 {args.url}：{exc}")
            print("请先启动服务：python run.py  或  uvicorn app.main:app")
            return 1

        model = health.get("default_model")
        print(f"服务状态: {health.get('status')} | 模型: {model}")
        if health.get("status") != "ok":
            print("[错误] 没有可用模型，请检查 .env 里的 API Key")
            return 1

        print("预热 1 次…")
        try:
            run_once(client, args.url, args.prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"[错误] 预热失败：{exc}")
            return 1

        results = []
        for i in range(1, args.n + 1):
            try:
                item = run_once(client, args.url, args.prompt)
            except Exception as exc:  # noqa: BLE001
                print(f"  第 {i} 次失败：{exc}")
                continue
            results.append(item)
            print(f"  {i:2d}/{args.n}  TTFT {item['ttft']:.2f}s  总计 {item['total']:.2f}s")

    if not results:
        print("[错误] 没有有效样本")
        return 1

    ttfts = [r["ttft"] for r in results]
    totals = [r["total"] for r in results]
    tokens = [r["completion_tokens"] for r in results if r["completion_tokens"]]

    ttft_p50, ttft_p95 = percentile(ttfts, 50), percentile(ttfts, 95)
    total_p50, total_p95 = percentile(totals, 50), percentile(totals, 95)
    avg_tokens = statistics.mean(tokens) if tokens else 0

    print("\n" + "=" * 52)
    print(f"有效样本 {len(results)} 次 | 模型 {model}")
    print(f"首字延迟 TTFT    P50 {ttft_p50:.2f}s    P95 {ttft_p95:.2f}s")
    print(f"端到端延迟       P50 {total_p50:.2f}s    P95 {total_p95:.2f}s")
    print(f"平均输出 token   {avg_tokens:.0f}")
    print("=" * 52)

    table = f"""
## 实测性能

| 指标 | P50 | P95 |
|---|---|---|
| 首字延迟（TTFT） | {ttft_p50:.2f} s | {ttft_p95:.2f} s |
| 端到端延迟 | {total_p50:.2f} s | {total_p95:.2f} s |

测试条件：模型 `{model}`，{len(results)} 次串行请求（另有 1 次预热），
本地 `{args.url}`，固定 prompt，平均输出 {avg_tokens:.0f} tokens。
"""
    print("\n复制下面这段放进 README：\n")
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())

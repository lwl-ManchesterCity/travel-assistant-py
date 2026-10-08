#!/usr/bin/env python3
"""抓一份真实的 /api/trips/plan 响应，存成结果页渲染 fixture。

用途：P3 结果页（票根头卡、等宽数字、按天卡）的视觉验收。验收探针只对着陆页截图，
结果页必须先把真实方案数据喂进 renderResult() 才渲染得出来。本脚本负责产出那份数据，
render_result_page.py 负责用它渲染并截图。

需要本地后端在跑（默认 127.0.0.1:8123）且已配置可用的 LLM：
    PYTHONPATH=. .venv/bin/python -m travel_assistant.main

用法：
    .venv/bin/python scripts/capture_result_fixture.py
输出：scripts/fixtures/result-plan.json（{"payload": ..., "plan": ...}）
"""
import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8123"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "fixtures" / "result-plan.json"

# 覆盖尽量多的渲染分支：3 天 / 多景点 / 酒店 / 餐饮 / 路线 / 天气 / Agent 轨迹
PAYLOAD = {
    "city": "杭州",
    "startDate": "2026-10-15",
    "endDate": "2026-10-17",
    "budget": 3000,
    "preferences": ["自然风景", "美食", "轻松节奏"],
    "transportation": "公共交通",
    "accommodation": "舒适型酒店",
    "extraRequirements": "",
}


def main() -> int:
    # trust_env=False：本机 httpx 走系统代理会拿到 502，直连 127.0.0.1 才拿得到真实响应
    with httpx.Client(trust_env=False, timeout=600) as client:
        response = client.post(f"{BASE}/api/trips/plan", json=PAYLOAD)
    if response.status_code != 200:
        print(f"HTTP {response.status_code}: {response.text[:500]}", file=sys.stderr)
        return 1

    plan = response.json()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"payload": PAYLOAD, "plan": plan}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写入 {OUT}")
    print(f"  城市={PAYLOAD['city']} 天数={len(plan.get('days', []))} "
          f"景点={sum(len(d.get('attractions', [])) for d in plan.get('days', []))} "
          f"轨迹={len(plan.get('agentTraces', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

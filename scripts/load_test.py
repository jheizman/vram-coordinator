#!/usr/bin/env python3
"""Safe load/chaos harness for vram-coordinator.

Default behavior is DRY RUN: no network traffic and no GPU pressure.
To run traffic intentionally, pass --execute.
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Scenario:
    name: str
    caller_id: str
    tier: int
    vram_mb: int
    deadline_seconds: float
    concurrency: int
    iterations: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe load/chaos harness for vram-coordinator")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787", help="Coordinator base URL")
    parser.add_argument("--dry-run", action="store_true", help="Preview scenarios only (default)")
    parser.add_argument("--execute", action="store_true", help="Actually send traffic (explicit opt-in)")
    parser.add_argument("--max-concurrency", type=int, default=4, help="Upper bound for concurrent workers")
    parser.add_argument("--max-iterations", type=int, default=20, help="Upper bound for iterations per scenario")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for repeatability")
    return parser.parse_args()


def default_scenarios(args: argparse.Namespace) -> list[Scenario]:
    return [
        Scenario("low-pressure", "load-low", 2, 512, 15.0, min(2, args.max_concurrency), min(5, args.max_iterations)),
        Scenario("medium-pressure", "load-med", 2, 2048, 20.0, min(3, args.max_concurrency), min(8, args.max_iterations)),
        Scenario("high-pressure", "load-high", 3, 4096, 10.0, min(4, args.max_concurrency), min(10, args.max_iterations)),
        Scenario("chaos-mixed", "load-chaos", random.choice([1, 2, 3]), random.choice([512, 1024, 2048, 4096]), 8.0, min(4, args.max_concurrency), min(12, args.max_iterations)),
    ]


def _request_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def run_live(base_url: str, scenarios: list[Scenario]) -> None:
    results = {"permit": 0, "deny": 0, "shed": 0, "errors": 0}
    for scenario in scenarios:
        print(f"[execute] scenario={scenario.name} tier={scenario.tier} vram={scenario.vram_mb}MB iterations={scenario.iterations}")
        for i in range(scenario.iterations):
            payload = {
                "caller_id": scenario.caller_id,
                "tier": scenario.tier,
                "vram_mb": scenario.vram_mb,
                "deadline_seconds": scenario.deadline_seconds,
            }
            code, body = _request_json(f"{base_url}/acquire", payload)
            if code != 200:
                results["errors"] += 1
                continue
            result = body.get("result")
            if result in results:
                results[result] += 1
            else:
                results["errors"] += 1
            lease_id = body.get("lease_id")
            if lease_id:
                _request_json(
                    f"{base_url}/release",
                    {"caller_id": scenario.caller_id, "lease_id": lease_id},
                )
            time.sleep(0.02)
    print("[execute] summary:", json.dumps(results, indent=2))


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    scenarios = default_scenarios(args)

    execute = args.execute
    if not args.execute:
        # dry-run is implicit even if --dry-run is not provided.
        args.dry_run = True

    print("=== vram-coordinator load/chaos harness ===")
    print(f"base_url={args.base_url}")
    print(f"dry_run={args.dry_run}")
    print(f"execute={execute}")
    print("")

    for s in scenarios:
        print(
            f"scenario={s.name} caller={s.caller_id} tier={s.tier} "
            f"vram_mb={s.vram_mb} deadline={s.deadline_seconds}s "
            f"concurrency={s.concurrency} iterations={s.iterations}"
        )

    if args.dry_run:
        print("\nDRY RUN ONLY: no requests sent.")
        print("To generate traffic intentionally, run with: --execute")
        return 0

    run_live(args.base_url, scenarios)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
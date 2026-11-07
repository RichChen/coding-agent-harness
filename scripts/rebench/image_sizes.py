"""Sample compressed sizes of the official per-task images from the Docker Hub API (no pulls).

Usage: python scripts/rebench/image_sizes.py [--sample N] [--seed S] [ids...]
Writes scripts/rebench/out/image_sizes.json.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"


def tag_info(image: str) -> dict:
    name, tag = image.rsplit(":", 1)
    namespace, repo = name.split("/", 1)
    url = f"https://hub.docker.com/v2/repositories/{namespace}/{repo}/tags/{tag}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = json.load(response)
            return dict(image=image, full_size=data.get("full_size"), last_updated=data.get("last_updated"),
                        digest=data.get("digest"),
                        arch=[i.get("architecture") for i in data.get("images") or []])
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return dict(image=image, error="404")
            time.sleep(5 * (attempt + 1))
        except OSError:
            time.sleep(5 * (attempt + 1))
    return dict(image=image, error="unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=9)
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    rows = json.loads((ROOT / "runs" / "rebench" / "inventory.json").read_text(encoding="utf-8"))
    by_id = {r["instance_id"]: r for r in rows}
    picked = [by_id[i] for i in args.ids] if args.ids else random.Random(args.seed).sample(rows, args.sample)
    results = []
    for row in picked:
        info = tag_info(row["docker_image"])
        info.update(instance_id=row["instance_id"], repo=row["repo"])
        results.append(info)
        time.sleep(0.3)
    sizes = [r["full_size"] / 1e9 for r in results if r.get("full_size")]
    summary = dict(sampled=len(results), with_size=len(sizes), errors=[r for r in results if r.get("error")],
                   gb_min=min(sizes), gb_median=statistics.median(sizes), gb_mean=statistics.mean(sizes),
                   gb_max=max(sizes), gb_p90=sorted(sizes)[int(0.9 * (len(sizes) - 1))],
                   extrapolated_total_gb=round(statistics.mean(sizes) * len(rows), 1), note="compressed sizes")
    OUT.mkdir(parents=True, exist_ok=True)
    name = "image_sizes.json" if not args.ids else "image_sizes_pilot.json"
    (OUT / name).write_text(json.dumps(dict(summary=summary, rows=results), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

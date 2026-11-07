"""Download the SWE-rebench leaderboard dataset (parquet) into runs/rebench/data. No model calls.

Records the dataset revision and file hashes in runs/rebench/data/fetch.json so later steps can cite them.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "runs" / "rebench" / "data"
DATASET = "nebius/SWE-rebench-leaderboard"
API = "https://huggingface.co/api/datasets/" + DATASET


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    info = json.loads(get(API))
    splits = json.loads(get(API + "/parquet"))["default"]
    files = {}
    for split, urls in sorted(splits.items()):
        for index, url in enumerate(urls):
            blob = get(url)
            path = DATA / f"{split}-{index}.parquet"
            path.write_bytes(blob)
            files[path.name] = dict(url=url, bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
            print(path.name, len(blob))
    record = dict(dataset=DATASET, revision=info.get("sha"), last_modified=info.get("lastModified"),
                  license=(info.get("cardData") or {}).get("license"), files=files)
    (DATA / "fetch.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "files"}))


if __name__ == "__main__":
    main()

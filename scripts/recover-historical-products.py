#!/usr/bin/env python3
"""Incrementally recover factual product fields from a retained CSV feed."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=Path, required=True)
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=20)
    args = parser.parse_args()
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    found = state.get("found", {})
    offset = int(state.get("offset", 0))
    target_ids = set(args.ids.read_text(encoding="utf-8").splitlines())
    with args.feed.open("r", encoding="utf-8-sig", newline="", errors="replace") as source:
        header = next(csv.reader([source.readline()]))
        if offset:
            source.seek(offset)
        # csv.reader disables TextIO.tell() only when it drives the file's
        # iterator directly.  A readline-backed iterator preserves a resume
        # position at each completed logical CSV row (including quoted newlines).
        class Lines:
            def __iter__(self):
                return self

            def __next__(self):
                line = source.readline()
                if not line:
                    raise StopIteration
                return line

        reader = csv.DictReader(Lines(), fieldnames=header)
        deadline = time.monotonic() + args.seconds
        next_offset = offset
        for row in reader:
            external_id = (row.get("itemid") or "").strip()
            identifier = hashlib.sha256(f"feed:{external_id}".encode()).hexdigest()[:16]
            if identifier in target_ids and identifier not in found:
                found[identifier] = {
                    key: row.get(key, "") for key in (
                        "itemid", "title", "global_category1", "sale_price", "price",
                        "product_link", "image_link", "shop_name",
                    )
                }
            next_offset = source.tell()
            if time.monotonic() >= deadline:
                break
        state = {"offset": next_offset, "found": found}
    args.state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"found": len(found), "complete": state["offset"] >= args.feed.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

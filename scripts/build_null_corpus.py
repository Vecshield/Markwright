"""Build the bundled starter null corpus (data/null/gutenberg-en-v1.jsonl).

NETWORK SCRIPT — run manually, never from the API or tests. Downloads
public-domain books from Project Gutenberg, splits them into passages near the
calibration length buckets, tokenizes with the gpt2 tokenizer, and writes one
JSON object per line:

    {"id": "...", "source": "gutenberg:<book id>", "n": <token count>, "ids": [...]}

This corpus is deliberately small and single-domain (19th-century English
prose). Null models fitted on it are PROVISIONAL grade: resolution floor
~3e-3, fiction-only register. It exists so the POC pipeline runs end to end
with honest labelling — production deployments calibrate on their own domain
corpora (design §7.2).

Usage:
    .venv/bin/python scripts/build_null_corpus.py [--out data/null] [--per-bucket 300]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import httpx

BOOKS = {
    # id: (gutenberg book, title) — plain-text UTF-8 mirrors
    "1342": "Pride and Prejudice (Austen)",
    "2701": "Moby Dick (Melville)",
    "1661": "The Adventures of Sherlock Holmes (Doyle)",
    "98": "A Tale of Two Cities (Dickens)",
}
URL = "https://www.gutenberg.org/cache/epub/{bid}/pg{bid}.txt"
TARGETS = (100, 200, 400)          # token-length buckets this corpus feeds
TOLERANCE = 0.25                   # keep passages within ±25% of a target


def strip_gutenberg_boilerplate(raw: str) -> str:
    start = re.search(r"\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", raw)
    end = re.search(r"\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", raw)
    return raw[start.end() if start else 0 : end.start() if end else len(raw)]


def paragraphs(text: str) -> list[str]:
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text)]
    # drop headings, tables of contents, very short fragments
    return [p for p in paras if len(p) > 200 and not p.isupper()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/null")
    ap.add_argument("--per-bucket", type=int, default=300)
    args = ap.parse_args()

    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained("gpt2")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    quota = {t: args.per_bucket for t in TARGETS}

    for bid, title in BOOKS.items():
        raw = httpx.get(URL.format(bid=bid), timeout=60, follow_redirects=True).text
        paras = paragraphs(strip_gutenberg_boilerplate(raw))
        print(f"{title}: {len(paras)} paragraphs")
        buf_ids: list[int] = []
        # Round-robin paragraphs into the emptiest bucket so all three fill evenly.
        for p in paras:
            if not any(quota.values()):
                break
            buf_ids.extend(tok(p, add_special_tokens=False)["input_ids"])
            target = max((t for t in TARGETS if quota[t]), key=lambda t: quota[t], default=None)
            if target is None:
                break
            if len(buf_ids) >= target * (1 - TOLERANCE):
                take = min(len(buf_ids), int(target * (1 + TOLERANCE)))
                ids = buf_ids[:take]
                buf_ids = []
                quota[target] -= 1
                rows.append({
                    "id": f"g{bid}-{len(rows):04d}",
                    "source": f"gutenberg:{bid}",
                    "n": len(ids),
                    "ids": ids,
                })

    path = out / "gutenberg-en-v1.jsonl"
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    by_target = {t: sum(1 for r in rows if abs(r["n"] - t) / t <= TOLERANCE + 0.01) for t in TARGETS}
    print(f"wrote {len(rows)} passages -> {path} ({path.stat().st_size/1e6:.1f} MB); near-bucket counts {by_target}")
    if any(q > 0 for q in quota.values()):
        print(f"WARNING: quotas unmet: {quota} — add books")


if __name__ == "__main__":
    main()

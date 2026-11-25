"""Download the selected IPO filings; no embeddings or model calls.

Run: uv run python -m ingestion.prepare_corpus
For automated SEC access, set SEC_USER_AGENT to your project name and contact email.
Already downloaded files can be checked without network access.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def inspect_html(data, company):
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    if company.lower() not in text.lower() or "prospectus" not in text.lower() or len(text.split()) < 10000:
        raise ValueError(f"Downloaded content is not a complete {company} prospectus")
    return len(text.split())


def prepare_corpus():
    manifest = json.loads((ROOT / "corpus_sources.json").read_text(encoding="utf-8"))
    folder = ROOT / "corpus"
    folder.mkdir(exist_ok=True)
    receipts_path = folder / "download_receipts.json"
    receipts = json.loads(receipts_path.read_text(encoding="utf-8")) if receipts_path.exists() else {}
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    for source in manifest["documents"]:
        path = folder / source["filename"]
        if path.exists():
            data = path.read_bytes()
            origin = "existing local file"
        else:
            if not user_agent:
                raise SystemExit("Set SEC_USER_AGENT to a project name and real contact email, then rerun. Alternatively save the linked filing as HTML in corpus/ with the manifest filename.")
            with httpx.Client(headers={"User-Agent": user_agent}, timeout=90, follow_redirects=True) as client:
                response = client.get(source["url"])
                response.raise_for_status()
                data = response.content
            inspect_html(data, source["company"])
            temporary = path.with_suffix(".html.part")
            temporary.write_bytes(data)
            temporary.replace(path)
            origin = "downloaded from manifest URL"
            time.sleep(0.25)
        words = inspect_html(data, source["company"])
        digest = hashlib.sha256(data).hexdigest()
        old = receipts.get(source["id"], {})
        if old and old["sha256"] != digest:
            raise ValueError(f"{path.name} changed since its recorded download; review before updating the receipt")
        receipts[source["id"]] = old or {
            "source_url": source["url"], "filename": source["filename"],
            "sha256": digest, "bytes": len(data), "raw_word_count": words,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "origin": origin,
            "retained_word_count": None, "personal_data_review": "pending",
        }
        temporary = receipts_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
        temporary.replace(receipts_path)
        print(f"{source['company']}: {words:,} raw words ({origin})")
    print("Raw downloads prepared. Cleaning, personal-data review and retained word counts are still required.")


if __name__ == "__main__":
    prepare_corpus()

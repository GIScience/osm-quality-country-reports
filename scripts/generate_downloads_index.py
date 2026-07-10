#!/usr/bin/env python3
"""Generate a public index.json listing the country folders under
oqapi_hdx/downloads/ in the RustFS/S3 bucket.

Anonymous ListObjectsV2 on the bucket is no longer allowed, so the static
frontend cannot enumerate country folders itself. This script does an
*authenticated* listing and writes the result to a single public object that
the frontend can fetch with a plain anonymous GET.

Run after uploads:
    python scripts/generate_downloads_index.py

Reads credentials from .env:
    RUSTFS_ENDPOINT=https://hot.storage.heigit.org/
    RUSTFS_ACCESS_KEY=...
    RUSTFS_SECRET_KEY=...
"""

import io
import json
import os
import sys
from urllib.parse import urlparse

from minio import Minio

BUCKET = "heigit-hdx-public"
DOWNLOADS_PREFIX = "oqapi_hdx/downloads/"
INDEX_OBJECT = "oqapi_hdx/downloads/index.json"


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def make_client() -> Minio:
    endpoint = os.environ.get("RUSTFS_ENDPOINT", "")
    access_key = os.environ.get("RUSTFS_ACCESS_KEY", "")
    secret_key = os.environ.get("RUSTFS_SECRET_KEY", "")

    if not endpoint or not access_key or not secret_key:
        sys.exit("Missing RUSTFS_ENDPOINT / RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY in .env")

    parsed = urlparse(endpoint)
    host = parsed.netloc or parsed.path  # tolerate value without scheme
    secure = parsed.scheme != "http"

    return Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)


def list_country_codes(client: Minio) -> list[str]:
    codes = []
    for obj in client.list_objects(BUCKET, prefix=DOWNLOADS_PREFIX, recursive=False):
        if not obj.is_dir:
            continue
        code = obj.object_name.removeprefix(DOWNLOADS_PREFIX).rstrip("/")
        if code and code != "index.json":
            codes.append(code)
    return sorted(codes)


def main():
    load_dotenv()
    client = make_client()

    codes = list_country_codes(client)
    print(f"Found {len(codes)} country folders: {codes}")

    payload = json.dumps(codes).encode("utf-8")
    client.put_object(
        BUCKET,
        INDEX_OBJECT,
        io.BytesIO(payload),
        length=len(payload),
        content_type="application/json",
    )
    print(f"Wrote {BUCKET}/{INDEX_OBJECT}")


if __name__ == "__main__":
    main()

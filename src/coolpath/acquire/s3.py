"""Anonymous S3 helpers."""

from functools import lru_cache
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config


@lru_cache
def client(region: str = "us-east-1"):
    return boto3.client("s3", config=Config(signature_version=UNSIGNED), region_name=region)


def list_objects(bucket: str, prefix: str, region: str = "us-east-1") -> list[dict]:
    pages = client(region).get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    return [o for page in pages for o in page.get("Contents", [])]


def download(bucket: str, key: str, dest: Path, region: str = "us-east-1") -> Path:
    """Download unless dest already exists with the same size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = client(region).head_object(Bucket=bucket, Key=key)["ContentLength"]
    if dest.exists() and dest.stat().st_size == size:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    client(region).download_file(bucket, key, str(tmp))
    tmp.rename(dest)
    return dest

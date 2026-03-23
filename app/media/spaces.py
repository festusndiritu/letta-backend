"""
DigitalOcean Spaces wrapper (S3-compatible, boto3).

All media lives under:
  letta-media/
  ├── avatars/
  ├── images/
  ├── videos/
  ├── audio/
  └── documents/

Files are public-read — the URL is what gets stored in the DB and
sent to clients. No pre-signed URLs needed for reads.
"""

import mimetypes
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.client import Config

from app.config import settings

# Allowed MIME types and their folder mapping
ALLOWED_TYPES: dict[str, str] = {
    # Images
    "image/jpeg": "images",
    "image/png": "images",
    "image/webp": "images",
    "image/gif": "images",
    # Video
    "video/mp4": "videos",
    "video/webm": "videos",
    # Audio
    "audio/ogg": "audio",
    "audio/mpeg": "audio",
    "audio/mp4": "audio",
    "audio/webm": "audio",
    # Documents
    "application/pdf": "documents",
    "application/msword": "documents",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "documents",
    "text/plain": "documents",
}

# Max file size per type (bytes)
MAX_SIZES: dict[str, int] = {
    "images": 10 * 1024 * 1024,    # 10 MB
    "videos": 100 * 1024 * 1024,   # 100 MB
    "audio": 20 * 1024 * 1024,     # 20 MB
    "documents": 20 * 1024 * 1024, # 20 MB
    "avatars": 5 * 1024 * 1024,    # 5 MB
}


def _get_client():
    return boto3.client(
        "s3",
        region_name=settings.do_spaces_region,
        endpoint_url=settings.do_spaces_endpoint,
        aws_access_key_id=settings.do_spaces_key,
        aws_secret_access_key=settings.do_spaces_secret,
        config=Config(signature_version="s3v4"),
    )


def _public_url(key: str) -> str:
    """Construct the public CDN URL for a Spaces object."""
    # DO Spaces CDN URL format
    bucket = settings.do_spaces_bucket
    region = settings.do_spaces_region
    return f"https://{bucket}.{region}.cdn.digitaloceanspaces.com/{key}"


async def upload_file(
    file_bytes: bytes,
    mime_type: str,
    folder: str,  # avatars | images | videos | audio | documents
) -> str:
    """
    Upload bytes to DO Spaces. Returns the public URL.
    Raises ValueError for invalid type or size.
    """
    if folder != "avatars" and mime_type not in ALLOWED_TYPES:
        raise ValueError(f"Unsupported file type: {mime_type}")

    max_size = MAX_SIZES.get(folder, 10 * 1024 * 1024)
    if len(file_bytes) > max_size:
        raise ValueError(f"File too large. Max size for {folder}: {max_size // (1024*1024)} MB")

    ext = mimetypes.guess_extension(mime_type) or ""
    # Some MIME types give ugly extensions — normalise common ones
    ext = {".jpe": ".jpg", ".jpeg": ".jpg"}.get(ext, ext)

    key = f"{folder}/{uuid.uuid4()}{ext}"

    client = _get_client()
    client.put_object(
        Bucket=settings.do_spaces_bucket,
        Key=key,
        Body=file_bytes,
        ContentType=mime_type,
        ACL="public-read",
    )

    return _public_url(key)


async def delete_file(url: str) -> None:
    """Delete a file from Spaces given its public URL. Silent on failure."""
    try:
        # Extract key from URL
        cdn_prefix = f"https://{settings.do_spaces_bucket}.{settings.do_spaces_region}.cdn.digitaloceanspaces.com/"
        if not url.startswith(cdn_prefix):
            return
        key = url[len(cdn_prefix):]
        client = _get_client()
        client.delete_object(Bucket=settings.do_spaces_bucket, Key=key)
    except Exception:
        pass
"""Backup automatico monitor.db → Telegram / S3 / Cloudflare R2."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from database import db_path, ensure_db

TELEGRAM_DOC_LIMIT = 49 * 1024 * 1024  # 50 MB con margine


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _sql_dump_path() -> Path:
    ensure_db()
    src = db_path()
    if not src.exists():
        raise FileNotFoundError(f"Database non trovato: {src}")
    dump = src.parent / f"monitor-{_timestamp()}.sql"
    with sqlite3.connect(src) as conn, dump.open("w", encoding="utf-8") as handle:
        for line in conn.iterdump():
            handle.write(f"{line}\n")
    return dump


def backup_to_telegram(*, token: str = "", chat_id: str = "") -> str:
    token = token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = chat_id or os.getenv("TELEGRAM_BACKUP_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN e TELEGRAM_BACKUP_CHAT_ID/TELEGRAM_CHAT_ID richiesti")
    src = db_path()
    if not src.exists():
        raise FileNotFoundError(f"Database non trovato: {src}")
    size = src.stat().st_size
    if size > TELEGRAM_DOC_LIMIT:
        dump = _sql_dump_path()
        upload = dump
        caption = f"Backup SQL monitor ({size // 1024} KB DB originale)"
    else:
        upload = src
        caption = f"Backup monitor.db ({size // 1024} KB) · {_timestamp()} UTC"
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with upload.open("rb") as handle:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (upload.name, handle)},
            timeout=120,
        )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram sendDocument {response.status_code}: {response.text[:300]}")
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API: {body}")
    if upload.suffix == ".sql" and upload != src:
        upload.unlink(missing_ok=True)
    return f"telegram:{chat_id}"


def backup_to_s3(*, bucket: str = "", prefix: str = "backups/") -> str:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 richiesto per backup S3/R2: pip install boto3") from exc
    bucket = bucket or os.getenv("BACKUP_S3_BUCKET", "").strip()
    if not bucket:
        raise ValueError("BACKUP_S3_BUCKET mancante")
    endpoint = os.getenv("BACKUP_S3_ENDPOINT", "").strip() or None
    region = os.getenv("BACKUP_S3_REGION", "auto").strip()
    prefix = prefix or os.getenv("BACKUP_S3_PREFIX", "backups/")
    key = f"{prefix.rstrip('/')}/monitor-{_timestamp()}.db"
    src = db_path()
    if not src.exists():
        raise FileNotFoundError(f"Database non trovato: {src}")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.getenv("BACKUP_S3_ACCESS_KEY", os.getenv("AWS_ACCESS_KEY_ID", "")),
        aws_secret_access_key=os.getenv("BACKUP_S3_SECRET_KEY", os.getenv("AWS_SECRET_ACCESS_KEY", "")),
    )
    client.upload_file(str(src), bucket, key)
    return f"s3://{bucket}/{key}"


def run_backup() -> list[str]:
    load_dotenv()
    targets = os.getenv("BACKUP_TARGETS", "telegram").strip().lower()
    results: list[str] = []
    for target in [item.strip() for item in targets.split(",") if item.strip()]:
        if target == "telegram":
            results.append(backup_to_telegram())
        elif target in ("s3", "r2"):
            results.append(backup_to_s3())
        elif target == "local":
            src = db_path()
            dest = src.parent / f"monitor-{_timestamp()}.db"
            shutil.copy2(src, dest)
            results.append(str(dest))
        else:
            print(f"Target backup sconosciuto: {target}")
    return results


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Backup monitor.db")
    parser.add_argument("--targets", default="", help="telegram,s3,r2,local (override BACKUP_TARGETS)")
    args = parser.parse_args()
    if args.targets:
        os.environ["BACKUP_TARGETS"] = args.targets
    results = run_backup()
    for item in results:
        print(f"OK → {item}")


if __name__ == "__main__":
    main()

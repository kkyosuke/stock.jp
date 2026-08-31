"""Create, verify, or safely stage a restore of private operation state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.operation_state import (
        initialize_or_migrate_workspace,
        secure_private_tree,
    )
except ModuleNotFoundError:  # Direct execution from scripts/
    from operation_state import initialize_or_migrate_workspace, secure_private_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
EXCLUDED_TOP_LEVEL = {"backups", "restores"}


def _parse_jst(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include a UTC offset")
    return parsed.astimezone(JST)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o600)
    temporary_path.replace(path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _backup_files(private: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(private.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(private)
        if relative.parts[0] in EXCLUDED_TOP_LEVEL or relative.name.startswith(".backup-"):
            continue
        files.append(path)
    return files


def _verify_zip(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            if "manifest.json" not in archive.namelist():
                return {"valid": False, "errors": ["manifest.json is missing"]}
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            listed = manifest.get("files", [])
            for item in listed:
                name = str(item.get("archive_path", ""))
                if not name.startswith("private/") or ".." in Path(name).parts:
                    errors.append(f"unsafe archive path: {name}")
                    continue
                try:
                    data = archive.read(name)
                except KeyError:
                    errors.append(f"missing archived file: {name}")
                    continue
                if len(data) != item.get("size"):
                    errors.append(f"size mismatch: {name}")
                if _sha256_bytes(data) != item.get("sha256"):
                    errors.append(f"sha256 mismatch: {name}")
            expected = {str(item.get("archive_path")) for item in listed} | {"manifest.json"}
            extras = sorted(set(archive.namelist()) - expected)
            if extras:
                errors.append("unlisted archive members: " + ", ".join(extras))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        errors.append(str(error))
        manifest = {}
    return {
        "valid": not errors,
        "errors": errors,
        "file_count": len(manifest.get("files", [])),
        "created_at_jst": manifest.get("created_at_jst"),
        "operation_mode": manifest.get("operation_mode"),
    }


def create_backup(
    *,
    at: str,
    allow_plaintext: bool = False,
    age_recipient: str | None = None,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    initialize_or_migrate_workspace(root)
    created = _parse_jst(at)
    private = root / "operations/private"
    policy = _read_json(private / "operation-policy.json")
    mode = str(policy.get("operation_mode", ""))
    age_recipient = age_recipient or os.environ.get("OPERATION_BACKUP_AGE_RECIPIENT")
    age_binary = shutil.which("age")
    encrypted = bool(age_recipient)
    if mode == "LIVE" and not encrypted:
        raise PermissionError("LIVE backup requires age encryption and --age-recipient")
    if encrypted and not age_binary:
        raise RuntimeError("age executable is required for encrypted backup")
    if not encrypted and not allow_plaintext:
        raise PermissionError("plaintext backup requires explicit --allow-plaintext")

    backup_dir = private / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    identifier = created.strftime("%Y%m%dT%H%M%S%z")
    staging = backup_dir / f".backup-{identifier}.zip"
    files = _backup_files(private)
    manifest_files: list[dict[str, Any]] = []
    with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            data = path.read_bytes()
            archive_path = f"private/{path.relative_to(private).as_posix()}"
            archive.writestr(archive_path, data)
            manifest_files.append(
                {
                    "archive_path": archive_path,
                    "size": len(data),
                    "sha256": _sha256_bytes(data),
                }
            )
        manifest = {
            "schema_version": "1.0",
            "created_at_jst": created.isoformat(timespec="seconds"),
            "operation_mode": mode,
            "file_count": len(manifest_files),
            "files": manifest_files,
        }
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
        )
    staging.chmod(0o600)
    verification = _verify_zip(staging)
    if not verification["valid"]:
        staging.unlink(missing_ok=True)
        raise ValueError("new backup failed verification: " + "; ".join(verification["errors"]))

    if encrypted:
        destination = backup_dir / f"operation-{identifier}.zip.age"
        try:
            subprocess.run(
                [str(age_binary), "-r", str(age_recipient), "-o", str(destination), str(staging)],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            staging.unlink(missing_ok=True)
        destination.chmod(0o600)
    else:
        destination = backup_dir / f"operation-{identifier}.zip"
        staging.replace(destination)

    state_path = private / "state.json"
    state = _read_json(state_path)
    state["last_backup_at_jst"] = created.isoformat(timespec="seconds")
    state["last_backup_path"] = destination.relative_to(root).as_posix()
    state["last_backup_sha256"] = _sha256_bytes(destination.read_bytes())
    state["last_backup_verified_before_encryption"] = True
    _atomic_json(state_path, state)
    secure_private_tree(root)
    return {
        "status": "CREATED",
        "archive": destination.relative_to(root).as_posix(),
        "encrypted": encrypted,
        "verified_before_encryption": True,
        "sha256": state["last_backup_sha256"],
        "file_count": verification["file_count"],
        "created_at_jst": created.isoformat(timespec="seconds"),
    }


def _plaintext_archive(
    *, archive: Path, age_identity: Path | None
) -> tuple[Path, bool]:
    if archive.suffix != ".age":
        return archive, False
    age_binary = shutil.which("age")
    if not age_binary or not age_identity:
        raise RuntimeError("encrypted archive verification requires age and --age-identity")
    temporary = archive.parent / f".backup-decrypted-{archive.stem}"
    subprocess.run(
        [age_binary, "-d", "-i", str(age_identity), "-o", str(temporary), str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    temporary.chmod(0o600)
    return temporary, True


def verify_backup(
    *, archive: Path, age_identity: Path | None = None, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    resolved = archive if archive.is_absolute() else root / archive
    plaintext, temporary = _plaintext_archive(
        archive=resolved, age_identity=age_identity
    )
    try:
        result = _verify_zip(plaintext)
    finally:
        if temporary:
            plaintext.unlink(missing_ok=True)
    return {**result, "archive": resolved.relative_to(root).as_posix()}


def stage_restore(
    *,
    archive: Path,
    destination: Path,
    age_identity: Path | None = None,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    resolved_archive = archive if archive.is_absolute() else root / archive
    resolved_destination = destination if destination.is_absolute() else root / destination
    root_resolved = root.resolve()
    try:
        resolved_destination.resolve().relative_to(root_resolved)
    except ValueError as error:
        raise ValueError("restore destination must be inside the project root") from error
    if resolved_destination.exists() and any(resolved_destination.iterdir()):
        raise FileExistsError("restore destination must be absent or empty")
    plaintext, temporary = _plaintext_archive(
        archive=resolved_archive, age_identity=age_identity
    )
    try:
        verification = _verify_zip(plaintext)
        if not verification["valid"]:
            raise ValueError("backup verification failed: " + "; ".join(verification["errors"]))
        resolved_destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        with zipfile.ZipFile(plaintext) as source:
            manifest = json.loads(source.read("manifest.json").decode("utf-8"))
            for item in manifest["files"]:
                archive_path = str(item["archive_path"])
                relative = Path(archive_path).relative_to("private")
                target = resolved_destination / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(source.read(archive_path))
                target.chmod(0o600)
    finally:
        if temporary:
            plaintext.unlink(missing_ok=True)
    return {
        "status": "STAGED",
        "destination": resolved_destination.relative_to(root).as_posix(),
        "file_count": verification["file_count"],
        "note": "review this staged copy; live private state was not overwritten",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--at", required=True)
    create.add_argument("--allow-plaintext", action="store_true")
    create.add_argument("--age-recipient")
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--age-identity", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--age-identity", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "create":
        result = create_backup(
            at=args.at,
            allow_plaintext=args.allow_plaintext,
            age_recipient=args.age_recipient,
        )
    elif args.command == "verify":
        result = verify_backup(
            archive=args.archive, age_identity=args.age_identity
        )
    else:
        result = stage_restore(
            archive=args.archive,
            destination=args.destination,
            age_identity=args.age_identity,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())

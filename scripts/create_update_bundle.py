#!/usr/bin/env python3
"""Build a manifest-checked update archive without including runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# When invoked as ``python scripts/create_update_bundle.py`` Python puts only
# the scripts directory on sys.path. Add the project root so the documented
# command works without requiring callers to set PYTHONPATH first.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.update_bundle import ALLOWED_FILES, ALLOWED_ROOTS, FORMAT, inspect_update_bundle


# These are the installed, user-facing console assets.  Their source of truth
# lives with the Windows installer project, but an online update must place
# them in the installation root's ``installer`` directory as well.  Keep this
# list deliberately narrow: migration/backup tools are not console runtime
# dependencies and must not be removed or replaced as a side effect.
CONSOLE_UPDATE_FILES = (
    "CenterConsole.hta",
    "CenterController.py",
    "CenterConsole.ico",
    "CenterConsoleIcon.png",
    "CenterConsole.ps1",
)
PORTABLE_UPDATE_FILES = ("portable_center.py",)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_generated_artifact(path: Path) -> bool:
    """Return whether *path* is a local interpreter/OS-generated artifact."""
    protected_names = {".env", "agent.json", "state.json", "secrets", "tokens"}
    protected_dirs = {"browser_state", "storage", "cache", "logs", "update-staging", "data"}
    return (
        path.name == ".DS_Store"
        or path.name.lower() in protected_names
        or any(part.lower() in protected_dirs for part in path.parts)
        or path.suffix.lower() in {".pyc", ".pyo"}
        or "__pycache__" in path.parts
    )


def build_update_bundle(source_root: Path, output: Path, version: str, notes: str = "", *, store: bool = False) -> dict:
    rows = []
    sources: dict[str, Path] = {}
    # ``installer`` is a valid target root but never a general source root:
    # only the explicitly mapped console assets below may be emitted there.
    for root in sorted(ALLOWED_ROOTS - {"installer", "portable"}):
        directory = source_root / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and not _is_generated_artifact(path):
                rel = path.relative_to(source_root).as_posix(); rows.append({"path": rel, "size": path.stat().st_size, "sha256": _hash(path)}); sources[rel] = path
    for name in sorted(ALLOWED_FILES):
        path = source_root / name
        if path.is_file() and not path.is_symlink() and not _is_generated_artifact(path):
            rows.append({"path": name, "size": path.stat().st_size, "sha256": _hash(path)}); sources[name] = path
    console_source = source_root / "packaging" / "windows" / "installer"
    for name in CONSOLE_UPDATE_FILES:
        path = console_source / name
        if path.is_file() and not path.is_symlink():
            rel = f"installer/{name}"
            if rel not in sources:
                rows.append({"path": rel, "size": path.stat().st_size, "sha256": _hash(path)})
                sources[rel] = path
    portable_source = source_root / "packaging" / "windows" / "portable"
    for name in PORTABLE_UPDATE_FILES:
        path = portable_source / name
        if path.is_file() and not path.is_symlink():
            rel = f"portable/{name}"
            rows.append({"path": rel, "size": path.stat().st_size, "sha256": _hash(path)})
            sources[rel] = path
    if not rows:
        raise ValueError("没有可打包的程序文件")
    manifest = {"format": FORMAT, "version": version, "created_at": datetime.now(timezone.utc).isoformat(), "notes": notes, "files": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    compression = zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(output, "w", compression) as archive:
        for row in rows:
            archive.write(sources[row["path"]], "payload/" + row["path"])
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    # Run the same validator used by the center before reporting success. A
    # release build must fail at packaging time, not after an operator uploads
    # it and discovers a rejected manifest.
    inspect_update_bundle(output)
    return {"output": str(output), "version": version, "files": len(rows), "sha256": _hash(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("source_root", type=Path); parser.add_argument("output", type=Path); parser.add_argument("version"); parser.add_argument("--notes", default=""); parser.add_argument("--store", action="store_true", help="使用不压缩 ZIP，便于验证旧版后台的 >1MiB COS 直传分支"); args = parser.parse_args()
    print(json.dumps(build_update_bundle(args.source_root, args.output, args.version, args.notes, store=args.store), ensure_ascii=False, indent=2))

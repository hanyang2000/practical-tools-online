"""Reproducibly refresh the Windows portable staging tree from source."""
from pathlib import Path
import shutil
import zipfile
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import __version__

DEST = ROOT / "dist" / "PracticalToolsOnlinePortable"
KEEP = {"runtime", "postgresql"}
COPY = ["app", "frontend", "migrations", "scripts", "packaging", "pyproject.toml", "alembic.ini"]

def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for child in DEST.iterdir():
        if child.name not in KEEP:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    for name in COPY:
        src = ROOT / name; dst = DEST / name
        if src.is_dir(): shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "data", "browser_state"))
        else: shutil.copy2(src, dst)
    shutil.copytree(ROOT / "packaging" / "windows" / "portable", DEST / "portable", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Ship the already assembled Windows capture agent alongside the center.
    # The online update archive carries source changes; the complete green
    # package also needs the matching runtime/dependencies/browser payload.
    agent_package = ROOT / "dist" / "PracticalToolsAgent-Windows-x64"
    if agent_package.is_dir():
        shutil.copytree(agent_package, DEST / "capture-agent", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "agent.json", "browser_state"))
        # The portable Agent tree may have been assembled on an earlier build
        # machine. Always overlay the current source so a full package cannot
        # silently ship an older Agent while keeping the prebuilt Windows
        # runtime and Chromium payload.
        shutil.copytree(ROOT / "agent", DEST / "capture-agent" / "agent", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "agent.json", "browser_state"))
    for path in DEST.rglob("*"):
        if path.name in {".env", "agent.json"} or "browser_state" in path.parts: path.unlink(missing_ok=True)
    # The migration script reads screenshot/config.yaml.  The shared center
    # runtime is assembled from the Windows payload and may not include the
    # optional PyYAML wheel, so copy its Windows package when available.
    agent_site = ROOT / "dist" / "PracticalToolsAgent-Windows-x64" / "runtime" / "Lib" / "site-packages"
    center_site = DEST / "runtime" / "Lib" / "site-packages"
    for name in ("yaml", "_yaml", "pyyaml-6.0.3.dist-info"):
        source = agent_site / name
        target = center_site / name
        if source.exists():
            if target.exists(): shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
    output = ROOT / "dist" / f"PracticalToolsOnlinePortable-Windows-x64-20260903-v{__version__}.zip"
    if output.exists(): output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(DEST.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(DEST)
            # A previous local build may have left a live PostgreSQL cluster
            # inside the reusable staging directory. Never put it in the
            # distributable portable archive.
            if relative.parts[:2] == ("postgresql", "data") or relative.parts[:1] == ("data",):
                continue
            archive.write(path, path.relative_to(DEST.parent).as_posix())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(output); print(digest)
    print(DEST)

if __name__ == "__main__": main()

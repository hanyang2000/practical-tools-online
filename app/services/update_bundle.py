"""Validation for the signed-by-hash center update archive."""
from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

FORMAT = "practical-tools-online-update-v1"
ALLOWED_ROOTS = {"app", "agent", "frontend", "installer", "migrations", "packaging", "portable", "scripts"}
ALLOWED_FILES = {"pyproject.toml", "alembic.ini", ".python-version"}
FORBIDDEN = {".env", "data", "runtime", "storage", "cache", "logs", "update-staging", "secrets"}
MAX_UPDATE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 3 * 1024 * 1024 * 1024
MAX_UPDATE_FILES = 30000


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = str(value).strip().lstrip("v").split(".")
    if len(parts) not in (3, 4) or any(not part.isdigit() for part in parts):
        raise ValueError("更新包版本号格式不正确，应为 主版本.次版本.修订号（可带桥接序号）")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _safe_path(value: str) -> PurePosixPath:
    if "\\" in value:
        raise ValueError(f"更新包路径非法：{value}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"更新包路径非法：{value}")
    if any(part.lower() in FORBIDDEN or "secret" in part.lower() or "token" in part.lower() for part in path.parts):
        raise ValueError(f"更新包包含受保护路径：{value}")
    return path


def inspect_update_bundle(bundle: Path) -> dict:
    package_size = bundle.stat().st_size
    if package_size <= 0 or package_size > MAX_UPDATE_BYTES:
        raise ValueError("更新包不能为空，且不能超过 1GB")
    with zipfile.ZipFile(bundle) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_UPDATE_FILES:
            raise ValueError("更新包文件数量异常")
        names = [_safe_path(info.filename).as_posix() for info in infos]
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError("更新包包含重复文件名")
        if sum(info.file_size for info in infos) > MAX_EXTRACTED_BYTES:
            raise ValueError("更新包解压后超过 3GB")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("更新包缺少有效 manifest.json") from exc
        version = str(manifest.get("version", "")).strip()
        if manifest.get("format") != FORMAT or not version:
            raise ValueError("更新清单格式或版本不正确")
        version_tuple(version)
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("更新清单没有文件")
        expected: dict[str, dict] = {}
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("更新清单文件项无效")
            path = _safe_path(str(item.get("path", "")))
            if path.as_posix() in expected or (path.parts[0] not in ALLOWED_ROOTS and path.as_posix() not in ALLOWED_FILES):
                raise ValueError(f"更新清单路径不允许：{path}")
            digest = str(item.get("sha256", "")).lower()
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError(f"更新清单 SHA-256 无效：{path}")
            if path.as_posix().casefold() in {key.casefold() for key in expected}:
                raise ValueError(f"更新清单包含重复文件名：{path}")
            expected[path.as_posix()] = {"path": path.as_posix(), "size": int(item.get("size", -1)), "sha256": digest}
        actual = set()
        for info in archive.infolist():
            if info.filename == "manifest.json" or info.is_dir():
                continue
            path = _safe_path(info.filename)
            if ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                raise ValueError(f"更新包不允许符号链接：{info.filename}")
            if not path.parts or path.parts[0] != "payload":
                raise ValueError(f"更新包成员必须放在 payload 下：{info.filename}")
            rel = PurePosixPath(*path.parts[1:]).as_posix()
            if rel not in expected:
                raise ValueError(f"更新包存在清单外文件：{info.filename}")
            actual.add(rel)
            data = archive.read(info.filename)
            item = expected[rel]
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"更新文件校验失败：{rel}")
        if actual != set(expected):
            raise ValueError("更新清单与压缩包成员不一致")
        required = {"app/__init__.py", "app/main.py"}
        if not required <= set(expected):
            raise ValueError("更新包缺少核心启动文件")
        version_text = archive.read("payload/app/__init__.py").decode("utf-8", "replace")
        if f'__version__ = "{version}"' not in version_text:
            raise ValueError("更新包版本与程序内部版本不一致")
        return manifest

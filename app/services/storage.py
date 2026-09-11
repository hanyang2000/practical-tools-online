from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.services.admin_config import effective_storage_config

MAX_IMAGE_BYTES = 100 * 1024 * 1024
UPLOAD_COS_THRESHOLD = 1024 * 1024
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    path: Path | None
    sha256: str
    file_size: int

def enforce_upload_policy(settings: Settings, size: int) -> None:
    if size > UPLOAD_COS_THRESHOLD and _backend_name(settings) != "tencent_cos":
        raise RuntimeError("文件超过 1MiB，请在管理员后台启用腾讯云 COS 后使用直传")


def safe_name(name: str) -> str:
    # ``Path`` follows the host OS. The center can receive a Windows path
    # while tests/export tools run on macOS or Linux, so normalize both
    # separator styles explicitly before taking the basename.
    raw = str(name or "file").replace("\x00", "").replace("\\", "/")
    return PurePosixPath(raw).name[:255] or "file"


def resolve_storage_key(settings: Settings, key: str) -> Path:
    # Storage keys are URL-style paths, but older Windows clients sometimes
    # persisted backslashes. Normalize those before resolving so an existing
    # object is still readable after a center migration.
    raw = str(key or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    drive_path = len(pure.parts) > 0 and len(pure.parts[0]) == 2 and pure.parts[0][1] == ":"
    if pure.is_absolute() or drive_path or ".." in pure.parts: raise ValueError("非法存储路径")
    root = settings.storage_root.resolve(); path = root.joinpath(*pure.parts).resolve()
    if path != root and root not in path.parents: raise ValueError("非法存储路径")
    return path


def find_compatible_storage_file(settings: Settings, key: str, *, expected_size: int | None = None,
                                 expected_sha256: str | None = None) -> Path | None:
    """Find a legacy local object without weakening storage path safety.

    The canonical location is ``data/storage/<key>``. A few earlier center
    builds copied the same relative key directly below ``data`` during
    migration, so look there as a read-only compatibility fallback. A
    fallback is accepted only when its recorded size/hash agrees with the
    database row, preventing a same-named unrelated file from being served.
    """
    try:
        raw = str(key or "").replace("\\", "/")
        pure = PurePosixPath(raw)
        drive_path = len(pure.parts) > 0 and len(pure.parts[0]) == 2 and pure.parts[0][1] == ":"
        if pure.is_absolute() or drive_path or ".." in pure.parts or not pure.parts:
            return None
        root = settings.data_root.resolve()
        candidates = [settings.storage_root.resolve().joinpath(*pure.parts), root.joinpath(*pure.parts)]
    except (AttributeError, OSError, ValueError):
        return None
    primary = candidates[0].resolve()
    valid: list[Path] = []
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            if candidate != root and root not in candidate.parents:
                continue
            if not candidate.is_file():
                continue
            if candidate == primary:
                return candidate
            if expected_size is not None and candidate.stat().st_size != int(expected_size):
                continue
            if expected_sha256 and len(expected_sha256) == 64 and sha256_file(candidate) != expected_sha256.lower():
                continue
            valid.append(candidate)
        except (OSError, ValueError):
            continue
    return valid[0] if valid else None


def _backend_name(settings: Settings) -> str:
    return str(effective_storage_config(settings).get("backend") or "local").strip().lower()


def storage_backend(settings: Settings) -> str:
    """Return the effective backend, including administrator JSON overrides."""
    return _backend_name(settings)


def store_bytes(settings: Settings, key: str, data: bytes, *, overwrite: bool = False) -> StoredObject:
    if _backend_name(settings) == "tencent_cos":
        raise RuntimeError("腾讯云 COS 使用 initiate/presign/complete 直传接口")
    target = resolve_storage_key(settings, key); target.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        target.write_bytes(data)
    else:
        with target.open("xb") as stream: stream.write(data)
    return StoredObject(key, target, hashlib.sha256(data).hexdigest(), len(data))


def store_file(settings: Settings, key: str, source: Path, *, sha256: str | None = None,
               file_size: int | None = None, overwrite: bool = False) -> StoredObject:
    """Store a staged file without loading the whole image into RAM."""
    if _backend_name(settings) == "tencent_cos":
        raise RuntimeError("腾讯云 COS 使用 initiate/presign/complete 直传接口")
    source = Path(source)
    target = resolve_storage_key(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        temporary = target.with_name(f".{target.name}.copy-{os.getpid()}")
        with source.open("rb") as stream, temporary.open("wb") as destination:
            shutil.copyfileobj(stream, destination, length=1024 * 1024)
        os.replace(temporary, target)
    else:
        with source.open("rb") as stream, target.open("xb") as destination:
            shutil.copyfileobj(stream, destination, length=1024 * 1024)
    size = int(file_size if file_size is not None else target.stat().st_size)
    digest = str(sha256 or sha256_file(target))
    return StoredObject(key, target, digest, size)


def stage_upload(upload: BinaryIO, settings: Settings) -> tuple[Path, int, str]:
    settings.ensure_directories()
    # Keep an Excel-compatible suffix: openpyxl chooses its reader from the
    # path extension, while image validation uses file magic and is unaffected.
    fd, path_text = tempfile.mkstemp(prefix="upload-", suffix=".xlsx", dir=settings.cache_root)
    path = Path(path_text); total = 0; digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as target:
            while chunk := upload.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes: raise ValueError("上传文件超过大小限制")
                target.write(chunk); digest.update(chunk)
        return path, total, digest.hexdigest()
    except Exception:
        path.unlink(missing_ok=True); raise


def inspect_image(path: Path) -> tuple[int, int]:
    if path.stat().st_size > MAX_IMAGE_BYTES: raise ValueError("图片不能超过 100MB")
    try:
        with Image.open(path) as image:
            size = image.size; image.verify()
        return size
    except (UnidentifiedImageError, OSError) as exc: raise ValueError("文件不是可识别的图片") from exc


_thumbnail_locks: dict[str, threading.Lock] = {}
_thumbnail_locks_guard = threading.Lock()


def _thumbnail_lock(key: str) -> threading.Lock:
    with _thumbnail_locks_guard:
        return _thumbnail_locks.setdefault(key, threading.Lock())


def thumbnail(settings: Settings, source: Path, cache_id: str, edge: int = 480) -> tuple[Path, str]:
    target = settings.cache_root / "thumbnails" / f"{hashlib.sha256(cache_id.encode()).hexdigest()[:32]}-{min(max(edge, 96), 960)}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file(): return target, "image/jpeg"
    with _thumbnail_lock(str(target)):
        if target.is_file(): return target, "image/jpeg"
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                if image.width > edge:
                    image = image.resize((edge, max(1, int(image.height * edge / image.width))))
                image.save(temporary, "JPEG", quality=78, optimize=True)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target, "image/jpeg"


def mobile_preview(settings: Settings, source: Path, cache_id: str, edge: int = 1440) -> tuple[Path, str]:
    """Create a cached, phone-friendly preview without touching the original.

    Screenshot originals are often tall phone captures. The older thumbnail
    helper intentionally scales by width for grid cards, which can still leave
    a 2,000+ pixel portrait image for a mobile viewer. This derivative uses
    the longest edge, keeps the aspect ratio, and is cached independently so
    opening the same screenshot does not repeat the resize work.
    """
    bounded_edge = min(max(int(edge or 1440), 640), 4096)
    cache_key = hashlib.sha256(cache_id.encode()).hexdigest()[:32]
    # Mobile screenshots are mostly text, straight lines, and flat UI colors.
    # A high-quality JPEG still creates ringing/blur around Chinese glyphs, so
    # use lossless WebP first. Android 8+ decodes it natively and it is usually
    # smaller than PNG while preserving every pixel. The JPEG path is kept for
    # Pillow builds without WebP support.
    lossless_target = settings.cache_root / "previews" / f"{cache_key}-{bounded_edge}-lossless.webp"
    jpeg_target = settings.cache_root / "previews" / f"{cache_key}-{bounded_edge}-q96.jpg"
    lossless_target.parent.mkdir(parents=True, exist_ok=True)
    if lossless_target.is_file():
        return lossless_target, "image/webp"
    if jpeg_target.is_file():
        return jpeg_target, "image/jpeg"
    with _thumbnail_lock(str(lossless_target)):
        if lossless_target.is_file():
            return lossless_target, "image/webp"
        if jpeg_target.is_file():
            return jpeg_target, "image/jpeg"
        temporary_webp = lossless_target.with_name(f".{lossless_target.name}.tmp-{os.getpid()}")
        temporary_jpeg = jpeg_target.with_name(f".{jpeg_target.name}.tmp-{os.getpid()}")
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                longest = max(image.width, image.height)
                if longest > bounded_edge:
                    scale = bounded_edge / float(longest)
                    image = image.resize(
                        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                try:
                    image.save(temporary_webp, "WEBP", lossless=True, method=6)
                    os.replace(temporary_webp, lossless_target)
                    return lossless_target, "image/webp"
                except (OSError, ValueError):
                    # Some minimal Pillow builds omit WebP encoding. Keep the
                    # endpoint usable, but use q96 and 4:4:4 JPEG as the safe
                    # compatibility path rather than the old q92 derivative.
                    image.save(temporary_jpeg, "JPEG", quality=96, subsampling=0, optimize=True)
                    os.replace(temporary_jpeg, jpeg_target)
                    return jpeg_target, "image/jpeg"
        finally:
            temporary_webp.unlink(missing_ok=True)
            temporary_jpeg.unlink(missing_ok=True)


class TencentCosStorage:
    """Optional Tencent COS adapter.

    The application process never receives a user's secret in a request. The
    adapter reads env/configuration on the center host and returns short-lived
    private signed URLs. Install ``cos-python-sdk-v5`` in production to enable
    real signatures; tests can inject a fake client implementing the same two
    methods.
    """
    def __init__(self, client=None):
        self.client = client
        values = effective_storage_config(__import__("app.config", fromlist=["get_settings"]).get_settings())
        self.bucket = values["bucket"]
        self.region = values["region"]
        self.prefix = values["prefix"]
        self.secret_id = values["secret_id"]
        self.secret_key = values["secret_key"]

    @property
    def configured(self) -> bool: return bool(self.bucket and self.region)

    def object_key(self, key: str) -> str:
        prefix = self.prefix.strip("/")
        return f"{prefix}/{key.lstrip('/')}" if prefix else key.lstrip("/")

    def _client(self):
        if self.client is not None: return self.client
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as exc: raise RuntimeError("已配置 COS，但未安装 cos-python-sdk-v5") from exc
        secret_id = self.secret_id
        secret_key = self.secret_key
        if not secret_id or not secret_key: raise RuntimeError("COS 凭证未配置")
        self.client = CosS3Client(CosConfig(Region=self.region, SecretId=secret_id, SecretKey=secret_key))
        return self.client

    def presign_put(self, key: str, expires: int = 900, metadata: dict | None = None) -> str:
        return self._client().get_presigned_url(Bucket=self.bucket, Key=self.object_key(key), Method="PUT", Expired=expires, Headers=metadata or {})

    def presign_get(self, key: str, expires: int = 300) -> str:
        return self._client().get_presigned_url(Bucket=self.bucket, Key=self.object_key(key), Method="GET", Expired=expires)

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream"):
        """Upload a generated derived object, such as a thumbnail, to COS."""
        with path.open("rb") as body:
            return self._client().put_object(
                Bucket=self.bucket,
                Key=self.object_key(key),
                Body=body,
                ContentType=content_type,
            )

    def head(self, key: str):
        return self._client().head_object(Bucket=self.bucket, Key=self.object_key(key))

    @contextmanager
    def temporary_download(self, key: str):
        """Stream a private object to a short-lived temp file and always clean it."""
        handle = tempfile.NamedTemporaryFile(prefix="practical-cos-", delete=False)
        path = Path(handle.name)
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=self.object_key(key))
            body = response.get("Body") if isinstance(response, dict) else response
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk: break
                handle.write(chunk)
            handle.close(); yield path
        finally:
            try: handle.close()
            except Exception: pass
            path.unlink(missing_ok=True)

    def delete(self, key: str):
        return self._client().delete_object(Bucket=self.bucket, Key=self.object_key(key))

    def ping(self):
        client = self._client()
        if hasattr(client, "head_bucket"):
            return client.head_bucket(Bucket=self.bucket)
        return client.head_object(Bucket=self.bucket, Key=self.object_key(".healthcheck"))

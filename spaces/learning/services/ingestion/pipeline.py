from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, Literal
from uuid import UUID, uuid4

from spaces.learning.services.ingestion.chunker import chunk_blocks
from spaces.learning.services.ingestion.normalizer import normalize_blocks
from spaces.learning.services.ingestion.parsers import ParserError, parse_document
from spaces.learning.services.ingestion.repository import (
    ArtifactInput,
    FailedSourceRevisionRecord,
    SourceRepository,
    SourceRevisionRecord,
    source_request_digest,
)

DEFAULT_INGESTION_SPEC_VERSION = "ingest-v2-pdf6.6.2-docx1.1.0-md3.0.0-norm2-chunk2"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:LEARNING_ACL_PATH
$current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$owner = $acl.Owner
try {
    $owner = ([System.Security.Principal.NTAccount]$owner).Translate(
        [System.Security.Principal.SecurityIdentifier]
    ).Value
} catch {
    exit 20
}
if ($owner -ne $current) { exit 21 }
$trusted = @($current, 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-0')
$dangerous = [System.Security.AccessControl.FileSystemRights]::Write `
    -bor [System.Security.AccessControl.FileSystemRights]::Delete `
    -bor [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles `
    -bor [System.Security.AccessControl.FileSystemRights]::ChangePermissions `
    -bor [System.Security.AccessControl.FileSystemRights]::TakeOwnership
foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -ne 'Allow') { continue }
    if (($rule.FileSystemRights -band $dangerous) -eq 0) { continue }
    try {
        $sid = $rule.IdentityReference.Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
    } catch {
        exit 22
    }
    if ($trusted -notcontains $sid) { exit 23 }
}
"""
_WINDOWS_HARDEN_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$root = $env:LEARNING_ACL_PATH
$current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$targets = @(
    Get-ChildItem -LiteralPath $root -Directory -Recurse -Force |
        Sort-Object { $_.FullName.Length } -Descending
)
$targets += Get-Item -LiteralPath $root -Force
foreach ($target in $targets) {
    $existing = Get-Acl -LiteralPath $target.FullName
    $owner = $existing.Owner
    try {
        $owner = ([System.Security.Principal.NTAccount]$owner).Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
    } catch {
        exit 30
    }
    if ($owner -ne $current.Value) { exit 31 }
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetOwner($current)
    $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $propagation = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    foreach ($sidValue in @($current.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier($sidValue)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            $allow
        )
        [void]$security.AddAccessRule($rule)
    }
    $security.SetAccessRuleProtection($true, $false)
    Set-Acl -LiteralPath $target.FullName -AclObject $security
}
"""


@dataclass(frozen=True)
class IngestionResult:
    state: Literal["stored", "duplicate", "failed"]
    relative_path: str
    record: SourceRevisionRecord | FailedSourceRevisionRecord | None = None
    error_code: str | None = None


class IngestionPipeline:
    def __init__(
        self,
        repository: SourceRepository,
        *,
        artifact_root: Path,
        maximum_chunk_characters: int = 1200,
        maximum_source_bytes: int = 50_000_000,
        maximum_document_characters: int = 5_000_000,
    ) -> None:
        self._repository = repository
        self._artifact_root = artifact_root.resolve(strict=True)
        _validate_artifact_root_security(self._artifact_root)
        self._maximum_chunk_characters = maximum_chunk_characters
        self._maximum_source_bytes = maximum_source_bytes
        self._maximum_document_characters = maximum_document_characters
        if min(maximum_source_bytes, maximum_document_characters) < 1:
            raise ValueError("ingestion limits must be positive")

    def ingest(
        self,
        *,
        source_path: Path,
        source_id: str,
        course_id: str,
        title: str,
        expected_revision: int,
        idempotency_key: str,
        media_type: str,
        ingestion_spec_version: str = DEFAULT_INGESTION_SPEC_VERSION,
    ) -> IngestionResult:
        if not source_path.is_file():
            raise ValueError("source file is required")
        source_id = str(UUID(source_id))
        course_id = str(UUID(course_id))
        content_hash, size_bytes = _hash_file(
            source_path, maximum_bytes=self._maximum_source_bytes
        )
        suffix = source_path.suffix.lower()
        relative_path = PurePosixPath(
            "sources", course_id, source_id, f"{content_hash}{suffix}"
        ).as_posix()
        stored_path = self._artifact_root / Path(*PurePosixPath(relative_path).parts)
        _copy_atomically(
            source_path,
            stored_path,
            artifact_root=self._artifact_root,
            expected_hash=content_hash,
        )
        artifact = ArtifactInput(
            relative_path=relative_path,
            content_hash=content_hash,
            media_type=media_type,
            size_bytes=size_bytes,
        )
        request_digest = source_request_digest(
            source_id=source_id,
            course_id=course_id,
            title=title,
            expected_revision=expected_revision,
            artifact=artifact,
            ingestion_spec_version=ingestion_spec_version,
        )
        self._repository.assert_request_key_compatible(
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        existing = self._repository.find_revision_by_content(
            source_id=source_id,
            course_id=course_id,
            title=title,
            content_hash=content_hash,
            ingestion_spec_version=ingestion_spec_version,
        )
        if existing is not None:
            belongs = self._repository.idempotency_key_belongs_to(
                idempotency_key, existing, request_digest=request_digest
            )
            if not belongs:
                self._repository.bind_duplicate_request(
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    record=existing,
                )
            if isinstance(existing, FailedSourceRevisionRecord):
                return IngestionResult(
                    state="failed",
                    relative_path=relative_path,
                    record=existing,
                    error_code="parser_failed",
                )
            return IngestionResult(
                state="stored" if belongs else "duplicate",
                relative_path=relative_path,
                record=existing,
            )
        try:
            parsed = parse_document(
                stored_path,
                media_type,
                maximum_document_characters=self._maximum_document_characters,
            )
            chunks = list(
                chunk_blocks(
                    normalize_blocks(parsed.blocks),
                    maximum_characters=self._maximum_chunk_characters,
                )
            )
            record = self._repository.persist_revision(
                source_id=source_id,
                course_id=course_id,
                title=title,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                artifact=artifact,
                chunks=chunks,
                ingestion_spec_version=ingestion_spec_version,
                request_digest=request_digest,
            )
            return IngestionResult(
                state="stored", relative_path=relative_path, record=record
            )
        except ParserError:
            record = self._repository.persist_failed_revision(
                source_id=source_id,
                course_id=course_id,
                title=title,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                artifact=artifact,
                ingestion_spec_version=ingestion_spec_version,
                request_digest=request_digest,
            )
            return IngestionResult(
                state="failed",
                relative_path=relative_path,
                record=record,
                error_code="parser_failed",
            )


def _hash_file(path: Path, *, maximum_bytes: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
            if maximum_bytes is not None and size > maximum_bytes:
                raise ValueError("source exceeds ingestion size limit")
    return digest.hexdigest(), size


def _copy_atomically(
    source: Path,
    destination: Path,
    *,
    artifact_root: Path,
    expected_hash: str,
) -> None:
    with _artifact_writer_lock(artifact_root):
        if os.name == "posix":
            _copy_atomically_posix(
                source,
                destination,
                artifact_root=artifact_root,
                expected_hash=expected_hash,
            )
            return
        _copy_atomically_checked_paths(
            source,
            destination,
            artifact_root=artifact_root,
            expected_hash=expected_hash,
        )


def _copy_atomically_checked_paths(
    source: Path,
    destination: Path,
    *,
    artifact_root: Path,
    expected_hash: str,
) -> None:
    _secure_parent(artifact_root, destination.parent)
    _reject_reparse_point(destination)
    if destination.is_file():
        if _hash_file(destination)[0] != expected_hash:
            raise RuntimeError("stored artifact hash conflict")
        return
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        actual_hash = _copy_to_descriptor(source, descriptor)
        if actual_hash != expected_hash:
            raise RuntimeError("source changed during artifact copy")
        _secure_parent(artifact_root, destination.parent)
        _reject_reparse_point(destination)
        os.replace(temporary, destination)
        resolved = destination.resolve(strict=True)
        if not resolved.is_relative_to(artifact_root):
            raise ValueError("artifact path escaped the artifact root")
    finally:
        temporary.unlink(missing_ok=True)


def _copy_atomically_posix(
    source: Path,
    destination: Path,
    *,
    artifact_root: Path,
    expected_hash: str,
) -> None:
    relative = destination.relative_to(artifact_root)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(artifact_root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        filename = relative.name
        try:
            existing_fd = os.open(
                filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
            )
        except FileNotFoundError:
            existing_fd = None
        if existing_fd is not None:
            with os.fdopen(existing_fd, "rb") as existing:
                if _hash_handle(existing) != expected_hash:
                    raise RuntimeError("stored artifact hash conflict")
            return
        temporary_name = f".{filename}.{uuid4().hex}.tmp"
        temporary_fd = os.open(
            temporary_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            if _copy_to_descriptor(source, temporary_fd) != expected_hash:
                raise RuntimeError("source changed during artifact copy")
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory_fd)


def _copy_to_descriptor(source: Path, descriptor: int) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as source_handle, os.fdopen(
        descriptor, "wb"
    ) as target_handle:
        for block in iter(lambda: source_handle.read(1024 * 1024), b""):
            digest.update(block)
            target_handle.write(block)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    return digest.hexdigest()


def _hash_handle(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    while block := handle.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _secure_parent(artifact_root: Path, parent: Path) -> None:
    relative = parent.relative_to(artifact_root)
    current = artifact_root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_reparse_point(current)
            if not current.is_dir():
                raise ValueError("artifact parent must be a directory")
        else:
            current.mkdir(mode=0o700)
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(artifact_root):
            raise ValueError("artifact path escaped the artifact root")


def _reject_reparse_point(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    status = path.lstat()
    file_attributes = getattr(status, "st_file_attributes", 0)
    if path.is_symlink() or file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("artifact path contains a reparse point")


def _validate_artifact_root_security(artifact_root: Path) -> None:
    if not artifact_root.is_dir():
        raise ValueError("artifact root must be a directory")
    if os.name == "nt":
        _harden_windows_acl(artifact_root)
        _validate_windows_acl(artifact_root)
        return
    if os.name != "posix":
        raise ValueError("artifact root security is unsupported on this platform")
        return
    status = artifact_root.stat()
    if status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) & 0o022:
        raise ValueError("artifact root must be owned and writable only by the service")


def _validate_windows_acl(path: Path) -> None:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise ValueError("Windows ACL verification is unavailable")
    environment = os.environ.copy()
    environment["LEARNING_ACL_PATH"] = str(path)
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _WINDOWS_ACL_SCRIPT,
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Windows ACL verification failed closed") from error
    if result.returncode != 0:
        raise ValueError("artifact root is writable by an untrusted principal")


def _harden_windows_acl(path: Path) -> None:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise ValueError("Windows ACL hardening is unavailable")
    environment = os.environ.copy()
    environment["LEARNING_ACL_PATH"] = str(path)
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _WINDOWS_HARDEN_ACL_SCRIPT,
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Windows ACL hardening failed closed") from error
    if result.returncode != 0:
        raise ValueError("Windows artifact ACL could not be hardened")


@contextmanager
def _artifact_writer_lock(artifact_root: Path) -> Iterator[None]:
    lock_path = artifact_root / ".ingestion.lock"
    with lock_path.open("a+b") as lock_handle:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"\0")
            lock_handle.flush()
        lock_handle.seek(0)
        deadline = time.monotonic() + 30
        while True:
            try:
                _lock_handle(lock_handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("artifact writer lock timed out")
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock_handle(lock_handle)


def _lock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

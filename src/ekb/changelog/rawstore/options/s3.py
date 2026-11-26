"""S3 raw store: originals as objects in a bucket, on SeaweedFS's S3 API locally (S17).

Once the connectors and the worker run as separate services, the worker must
read the originals the connectors stored, so they cannot stay in one process's
memory. S3 is what production uses (with versioning, D13); SeaweedFS speaks the
same API locally, so this option runs unchanged on AWS.

**One object per version, per lifetime.** The object key is
``<source>/<source ID>/<lifetime>/<version>``, each part URL-encoded (IDs and
versions can hold ``/`` or ``:``), and the pointer an event carries is
``s3://<bucket>/<key>``. Like the in-memory store, nothing parses a pointer
except :meth:`get`, which checks it names this bucket.

**Immutable.** ``put`` first asks whether the object exists: the same bytes
return the same pointer (a repeated event is harmless), other bytes are refused
(``ConflictingOriginalError``). The checksum and the time it was stored travel
as object metadata. Only one process writes (the connectors), so the check and
the write need no lock; S3's conditional writes (``If-None-Match``) would close
that gap with several writers.

**Purge.** ``purge`` lists the item's objects (one lifetime, or all) and
deletes them, so a deleted page's text is gone from the store, not just
unreferenced.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

from ekb.changelog.rawstore.port import (
    ConflictingOriginalError,
    OriginalNotFoundError,
    StoredOriginal,
)
from ekb.platform.aws import AwsError, S3Client, call, s3_client
from ekb.platform.infra import scoped
from ekb.platform.registry import register

MISSING = frozenset({"404", "NoSuchKey", "NotFound", "NoSuchBucket"})


def _now() -> datetime:
    return datetime.now(UTC)


def _checksum(original: bytes) -> str:
    return f"sha256:{hashlib.sha256(original).hexdigest()}"


def _part(value: str) -> str:
    return quote(value, safe="")


@register("rawstore", "s3")
class S3RawStore:
    def __init__(
        self,
        bucket: str = "ekb-raw",
        *,
        namespace: str | None = None,
        url: str | None = None,
        client: S3Client | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.bucket = scoped(bucket, namespace)
        self._client = client or s3_client(url)
        self._clock = clock
        self._ready = False

    # ------------------------------------------------------------ addresses

    def _prefix(self, document_key: str, lifetime: int | None = None) -> str:
        source, _, source_id = document_key.partition(":")
        prefix = f"{_part(source)}/{_part(source_id)}/"
        return prefix if lifetime is None else f"{prefix}{lifetime}/"

    def _key(self, document_key: str, version: str, lifetime: int) -> str:
        return f"{self._prefix(document_key, lifetime)}{_part(version)}"

    def pointer_for(self, document_key: str, version: str, *, lifetime: int = 0) -> str:
        return f"s3://{self.bucket}/{self._key(document_key, version, lifetime)}"

    def _key_of(self, pointer: str) -> str:
        head = f"s3://{self.bucket}/"
        if not pointer.startswith(head):
            raise OriginalNotFoundError(f"{pointer} is not in bucket {self.bucket}")
        return pointer[len(head) :]

    # ------------------------------------------------------------ the port

    def put(self, document_key: str, version: str, original: bytes, *, lifetime: int = 0) -> str:
        self._ensure()
        key = self._key(document_key, version, lifetime)
        checksum = _checksum(original)
        existing = self._head(key)
        if existing is not None:
            if existing.get("checksum") != checksum:
                raise ConflictingOriginalError(
                    f"{document_key} version {version} is already stored with other bytes"
                )
            return self.pointer_for(document_key, version, lifetime=lifetime)
        metadata = {
            "checksum": checksum,
            "document-key": _part(document_key),
            "version": _part(version),
            "lifetime": str(lifetime),
            "stored-at": self._clock().isoformat(),
        }
        call(
            f"store {key}",
            lambda: self._client.put_object(
                Bucket=self.bucket, Key=key, Body=bytes(original), Metadata=metadata
            ),
        )
        return self.pointer_for(document_key, version, lifetime=lifetime)

    def get(self, pointer: str) -> bytes:
        key = self._key_of(pointer)
        try:
            response = call(
                f"read {key}", lambda: self._client.get_object(Bucket=self.bucket, Key=key)
            )
        except AwsError as exc:
            if exc.code in MISSING:
                raise OriginalNotFoundError(
                    f"no original at {pointer} (never stored, or purged)"
                ) from exc
            raise
        body: bytes = response["Body"].read()
        return body

    def versions(self, document_key: str) -> list[StoredOriginal]:
        found = []
        for key, size in self._list(self._prefix(document_key)):
            meta = self._head(key)
            if meta is None:  # purged between the listing and the look
                continue
            found.append(
                StoredOriginal(
                    pointer=f"s3://{self.bucket}/{key}",
                    document_key=document_key,
                    version=unquote(meta.get("version", key.rsplit("/", 1)[-1])),
                    size=size,
                    checksum=meta.get("checksum", ""),
                    stored_at=datetime.fromisoformat(meta["stored-at"]),
                )
            )
        return sorted(found, key=lambda o: (o.stored_at, o.pointer))

    def purge(self, document_key: str, *, lifetime: int | None = None) -> int:
        removed = 0
        for key, _ in self._list(self._prefix(document_key, lifetime)):
            self._delete(key)
            removed += 1
        return removed

    # ------------------------------------------------------------ helpers

    def _ensure(self) -> None:
        """Create the bucket on first write (SeaweedFS starts with none)."""
        if self._ready:
            return
        try:
            call(f"look for {self.bucket}", lambda: self._client.head_bucket(Bucket=self.bucket))
        except AwsError as exc:
            if exc.code not in MISSING:
                raise
            request: dict[str, Any] = {"Bucket": self.bucket}
            region = getattr(getattr(self._client, "meta", None), "region_name", None)
            if region and region != "us-east-1":
                # S3 wants the region named, except there (as the AWS CLI sends it,
                # which SeaweedFS accepts too).
                request["CreateBucketConfiguration"] = {"LocationConstraint": region}
            try:
                call(f"create {self.bucket}", lambda: self._client.create_bucket(**request))
            except AwsError as created:
                if created.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise
        self._ready = True

    def _delete(self, key: str) -> None:
        call(f"purge {key}", lambda: self._client.delete_object(Bucket=self.bucket, Key=key))

    def _head(self, key: str) -> dict[str, str] | None:
        """The object's metadata, or ``None`` if there is no such object (or bucket)."""
        try:
            response = call(
                f"look at {key}", lambda: self._client.head_object(Bucket=self.bucket, Key=key)
            )
        except AwsError as exc:
            if exc.code in MISSING:
                return None
            raise
        metadata: dict[str, str] = response.get("Metadata", {})
        return metadata

    def _list(self, prefix: str) -> Iterator[tuple[str, int]]:
        """``(key, size)`` of every object under ``prefix``; nothing if there is no bucket."""
        request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            try:
                page = call(f"list {prefix}", lambda: self._client.list_objects_v2(**request))
            except AwsError as exc:
                if exc.code in MISSING:
                    return
                raise
            for item in page.get("Contents", []):
                yield str(item["Key"]), int(item["Size"])
            if not page.get("IsTruncated"):
                return
            request["ContinuationToken"] = page["NextContinuationToken"]

    def destroy(self) -> None:
        """Delete every object and the bucket (the end of a demo or test run)."""
        for key, _ in self._list(""):
            self._delete(key)
        try:
            call(f"drop {self.bucket}", lambda: self._client.delete_bucket(Bucket=self.bucket))
        except AwsError as exc:
            if exc.code not in MISSING:
                raise
        self._ready = False

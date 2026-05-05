from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlparse

from hypo_agent.models import Attachment, Message

ResourceKind = Literal["file", "attachment", "url", "webpage", "generated_file"]
ResolutionStatus = Literal["resolved", "ambiguous", "not_found", "blocked"]
RecoveryActionType = Literal["ask_user", "search_or_ask", "request_permission"]

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ResourceRef:
    kind: ResourceKind
    uri: str
    display_name: str = ""
    mime_type: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    ref: ResourceRef
    score: float
    source: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ResourceRecoveryAction:
    type: RecoveryActionType
    reason: str
    message: str


@dataclass(frozen=True, slots=True)
class ResourceResolution:
    status: ResolutionStatus
    ref: ResourceRef | None = None
    candidates: list[ResourceCandidate] = field(default_factory=list)
    recovery_action: ResourceRecoveryAction | None = None


@dataclass(frozen=True, slots=True)
class ResourceResolverContext:
    recent_messages: list[Message] = field(default_factory=list)
    generated_files: list[Path | str] = field(default_factory=list)
    uploaded_attachments: list[Attachment] = field(default_factory=list)


class ResourceResolver:
    def __init__(
        self,
        *,
        search_roots: list[Path | str] | None = None,
        context: ResourceResolverContext | None = None,
    ) -> None:
        self.search_roots = [
            Path(root).expanduser().resolve(strict=False)
            for root in (search_roots or [])
        ]
        self.context = context or ResourceResolverContext()

    def resolve(self, query: str, *, purpose: str = "") -> ResourceResolution:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return self._not_found("empty_query")

        url_ref = self._url_ref(normalized_query, purpose=purpose)
        if url_ref is not None:
            return ResourceResolution(status="resolved", ref=url_ref)

        candidates = self.find_candidates(normalized_query, purpose=purpose)
        if not candidates:
            return self._not_found("no_candidates")
        if len(candidates) == 1:
            return ResourceResolution(
                status="resolved",
                ref=candidates[0].ref,
                candidates=candidates,
            )
        return ResourceResolution(
            status="ambiguous",
            candidates=candidates,
            recovery_action=ResourceRecoveryAction(
                type="ask_user",
                reason="multiple_candidates",
                message="找到多个候选资源，请确认要使用哪一个。",
            ),
        )

    def find_candidates(self, query: str, *, purpose: str = "") -> list[ResourceCandidate]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        candidates: list[ResourceCandidate] = []
        candidates.extend(self._path_candidates(normalized_query, purpose=purpose))
        candidates.extend(self._generated_file_candidates(normalized_query, purpose=purpose))
        candidates.extend(self._attachment_candidates(normalized_query, purpose=purpose))

        deduped: dict[str, ResourceCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
            deduped.setdefault(candidate.ref.uri, candidate)
        return list(deduped.values())

    def _path_candidates(self, query: str, *, purpose: str) -> list[ResourceCandidate]:
        requested = Path(query).expanduser()
        direct_paths: list[Path] = []
        if requested.is_absolute():
            direct_paths.append(requested.resolve(strict=False))
        else:
            direct_paths.append(requested.resolve(strict=False))
            for root in self.search_roots:
                direct_paths.append((root / requested).resolve(strict=False))

        candidates: list[ResourceCandidate] = []
        for path in direct_paths:
            if path.exists() and path.is_file():
                candidates.append(
                    ResourceCandidate(
                        ref=self._file_ref(path, purpose=purpose),
                        score=1.0,
                        source="path",
                        reason="exact_path",
                    )
                )

        if candidates:
            return candidates

        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        for root in self.search_roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                score = _match_score(path.name, query_tokens)
                if score <= 0:
                    continue
                candidates.append(
                    ResourceCandidate(
                        ref=self._file_ref(path, purpose=purpose),
                        score=score,
                        source="search_root",
                        reason="fuzzy_filename",
                    )
                )
        return candidates

    def _generated_file_candidates(self, query: str, *, purpose: str) -> list[ResourceCandidate]:
        query_tokens = _tokens(query)
        candidates: list[ResourceCandidate] = []
        for raw_path in self.context.generated_files:
            path = Path(raw_path).expanduser().resolve(strict=False)
            if not path.exists() or not path.is_file():
                continue
            score = max(_match_score(path.name, query_tokens), _match_score(path.stem, query_tokens))
            if score <= 0:
                continue
            candidates.append(
                ResourceCandidate(
                    ref=self._file_ref(path, kind="generated_file", purpose=purpose),
                    score=score + 0.15,
                    source="recent_generated",
                    reason="recent_generated_file",
                )
            )
        return candidates

    def _attachment_candidates(self, query: str, *, purpose: str) -> list[ResourceCandidate]:
        query_tokens = _tokens(query)
        candidates: list[ResourceCandidate] = []
        attachments = list(self.context.uploaded_attachments)
        for message in self.context.recent_messages:
            attachments.extend(message.attachments)

        for attachment in attachments:
            score = max(
                _match_score(attachment.filename or "", query_tokens),
                _match_score(Path(attachment.url).name, query_tokens),
            )
            if score <= 0:
                continue
            path = Path(attachment.url).expanduser().resolve(strict=False)
            candidates.append(
                ResourceCandidate(
                    ref=ResourceRef(
                        kind="attachment",
                        uri=str(path),
                        display_name=attachment.filename or path.name,
                        mime_type=attachment.mime_type,
                        size_bytes=attachment.size_bytes,
                        metadata={
                            "attachment_type": attachment.type,
                            "purpose": purpose,
                        },
                    ),
                    score=score + 0.1,
                    source="recent_attachment",
                    reason="recent_attachment_filename",
                )
            )
        return candidates

    def _url_ref(self, query: str, *, purpose: str) -> ResourceRef | None:
        if _URL_RE.match(query) is None:
            return None
        parsed = urlparse(query)
        display_name = (parsed.netloc + parsed.path).strip("/") or query
        return ResourceRef(
            kind="url",
            uri=query,
            display_name=display_name,
            metadata={"purpose": purpose},
        )

    def _file_ref(
        self,
        path: Path,
        *,
        kind: ResourceKind = "file",
        purpose: str,
    ) -> ResourceRef:
        resolved = path.expanduser().resolve(strict=False)
        size_bytes = resolved.stat().st_size if resolved.exists() and resolved.is_file() else None
        return ResourceRef(
            kind=kind,
            uri=str(resolved),
            display_name=resolved.name,
            size_bytes=size_bytes,
            metadata={"purpose": purpose},
        )

    def _not_found(self, reason: str) -> ResourceResolution:
        return ResourceResolution(
            status="not_found",
            recovery_action=ResourceRecoveryAction(
                type="search_or_ask",
                reason=reason,
                message="没有找到匹配资源。请提供更明确的文件名、路径或重新上传附件。",
            ),
        )


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", str(value or "").lower())
        if token
    ]


def _match_score(value: str, query_tokens: list[str]) -> float:
    if not value or not query_tokens:
        return 0.0
    normalized_value = str(value).lower()
    matched = sum(1 for token in query_tokens if token in normalized_value)
    if matched == 0:
        return 0.0
    return matched / len(query_tokens)

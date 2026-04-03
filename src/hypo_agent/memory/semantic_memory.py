from __future__ import annotations

from dataclasses import dataclass
from hashlib import md5
import json
from pathlib import Path
import re
from typing import Any

import structlog

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - depends on runtime environment
    sqlite_vec = None

try:
    import tiktoken
except ImportError:  # pragma: no cover - depends on runtime environment
    tiktoken = None

from hypo_agent.memory.structured_store import StructuredStore

logger = structlog.get_logger("hypo_agent.memory.semantic_memory")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_DEFAULT_USER_QUERY = "用户偏好 回复风格 喜欢 习惯"
_SEMANTIC_MEMORY_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def estimate_token_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    if tiktoken is None:
        return max(1, len(stripped) // 4)
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(stripped))


@dataclass(slots=True)
class ChunkResult:
    file_path: str
    chunk_text: str
    score: float
    chunk_index: int


@dataclass(slots=True)
class PreparedChunk:
    chunk_index: int
    heading_path: list[str]
    chunk_text: str


class SemanticMemory:
    def __init__(
        self,
        *,
        structured_store: StructuredStore,
        model_router: Any,
        max_chunk_tokens: int = 512,
        chunk_overlap_tokens: int = 128,
        vector_top_k: int = 10,
        keyword_top_k: int = 10,
        rrf_k: int = 60,
        min_rrf_score: float = 0.015,
    ) -> None:
        self.structured_store = structured_store
        self.model_router = model_router
        self.max_chunk_tokens = max(32, int(max_chunk_tokens))
        self.chunk_overlap_tokens = max(0, int(chunk_overlap_tokens))
        self.vector_top_k = max(1, int(vector_top_k))
        self.keyword_top_k = max(1, int(keyword_top_k))
        self.rrf_k = max(1, int(rrf_k))
        self.min_rrf_score = max(0.0, float(min_rrf_score))
        self.knowledge_dir: Path | None = None
        self._encoding = (
            tiktoken.get_encoding("cl100k_base")
            if tiktoken is not None
            else None
        )

    async def build_index(self, knowledge_dir: str | Path) -> None:
        self.knowledge_dir = Path(knowledge_dir).expanduser().resolve(strict=False)
        await self.structured_store.init()

        markdown_files = sorted(self.knowledge_dir.rglob("*.md"))
        indexed_files = set(await self.structured_store.list_semantic_files())
        live_files = {str(path.resolve(strict=False)) for path in markdown_files}

        for stale_file in indexed_files - live_files:
            await self.structured_store.delete_semantic_chunks(stale_file)

        for file_path in markdown_files:
            try:
                await self.update_index(file_path)
            except _SEMANTIC_MEMORY_ERRORS:
                logger.exception("semantic_memory.build_index.file_failed", file_path=str(file_path))

    async def update_index(self, file_path: str | Path) -> None:
        await self.structured_store.init()
        resolved_path = Path(file_path).expanduser().resolve(strict=False)
        file_path_str = str(resolved_path)
        if not resolved_path.exists():
            await self.structured_store.delete_semantic_chunks(file_path_str)
            return

        content = resolved_path.read_text(encoding="utf-8")
        file_hash = md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()
        existing_hash = await self.structured_store.get_semantic_file_hash(file_path_str)
        if existing_hash == file_hash:
            logger.debug(
                "semantic_memory.index_skip",
                file_path=file_path_str,
                reason="hash_unchanged",
            )
            return

        chunks = self._chunk_markdown(content)
        if not chunks:
            await self.structured_store.delete_semantic_chunks(file_path_str)
            return

        logger.info(
            "semantic_memory.index_update",
            file_path=file_path_str,
            old_hash=(existing_hash or "")[:8],
            new_hash=file_hash[:8],
            chunks=len(chunks),
        )

        embeddings = await self._embed_texts([item.chunk_text for item in chunks])
        if not embeddings:
            await self.structured_store.delete_semantic_chunks(file_path_str)
            return

        await self.structured_store.ensure_semantic_vector_dimensions(len(embeddings[0]))
        payload = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            payload.append(
                {
                    "chunk_index": chunk.chunk_index,
                    "chunk_text": chunk.chunk_text,
                    "embedding_blob": self._serialize_embedding(embedding),
                }
            )
        await self.structured_store.replace_semantic_chunks(
            file_path=file_path_str,
            file_hash=file_hash,
            chunks=payload,
        )

    async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        vector_hits: list[ChunkResult] = []
        try:
            embeddings = await self.model_router.embed([normalized_query])
        except _SEMANTIC_MEMORY_ERRORS:
            logger.warning("semantic_memory.vector_search_failed", query=normalized_query)
        else:
            if embeddings:
                rows = await self.structured_store.semantic_vector_search(
                    embeddings[0],
                    limit=self.vector_top_k,
                )
                vector_hits = [
                    ChunkResult(
                        file_path=str(row["file_path"]),
                        chunk_text=str(row["chunk_text"]),
                        score=self._distance_to_similarity(float(row["distance"])),
                        chunk_index=int(row["chunk_index"]),
                    )
                    for row in rows
                ]

        keyword_rows = await self.structured_store.semantic_keyword_search(
            normalized_query,
            limit=self.keyword_top_k,
        )
        keyword_hits = [
            ChunkResult(
                file_path=str(row["file_path"]),
                chunk_text=str(row["chunk_text"]),
                score=1.0 / (1.0 + max(float(row["bm25_score"]), 0.0)),
                chunk_index=int(row["chunk_index"]),
            )
            for row in keyword_rows
        ]

        merged = self._rrf_merge(
            vector_hits=vector_hits,
            keyword_hits=keyword_hits,
            top_k=max(1, int(top_k)),
            rrf_k=self.rrf_k,
        )
        results = [item for item in merged if item.score >= self.min_rrf_score][:top_k]
        logger.info(
            "semantic_memory.search",
            query=normalized_query[:50],
            vector_hits=len(vector_hits),
            keyword_hits=len(keyword_rows),
            final_results=len(results),
            top_score=results[0].score if results else 0.0,
        )
        return results

    def _chunk_markdown(self, markdown_text: str) -> list[PreparedChunk]:
        stack: list[tuple[int, str]] = []
        buffer: list[str] = []
        sections: list[tuple[list[str], str]] = []

        def flush() -> None:
            body = "\n".join(buffer).strip()
            if not body:
                buffer.clear()
                return
            sections.append(([title for _, title in stack], body))
            buffer.clear()

        for raw_line in markdown_text.splitlines():
            match = _HEADING_RE.match(raw_line.strip())
            if match:
                flush()
                level = len(match.group(1))
                title = match.group(2).strip()
                stack[:] = [item for item in stack if item[0] < level]
                stack.append((level, title))
                continue
            buffer.append(raw_line)
        flush()

        chunks: list[PreparedChunk] = []
        chunk_index = 0
        for heading_path, body in sections:
            for chunk_text in self._split_section(heading_path, body):
                chunks.append(
                    PreparedChunk(
                        chunk_index=chunk_index,
                        heading_path=list(heading_path),
                        chunk_text=chunk_text,
                    )
                )
                chunk_index += 1
        return chunks

    def _split_section(self, heading_path: list[str], body: str) -> list[str]:
        context = " > ".join(item for item in heading_path if item).strip()
        body = body.strip()
        if not body:
            return []

        tokens = self._encode(body)
        if len(tokens) <= self.max_chunk_tokens:
            return [self._format_chunk_text(context, body)]

        step = max(1, self.max_chunk_tokens - self.chunk_overlap_tokens)
        chunks: list[str] = []
        for start in range(0, len(tokens), step):
            window = tokens[start : start + self.max_chunk_tokens]
            if not window:
                continue
            chunks.append(self._format_chunk_text(context, self._decode(window).strip()))
            if start + self.max_chunk_tokens >= len(tokens):
                break
        return chunks

    def _format_chunk_text(self, context: str, body: str) -> str:
        if context:
            return f"标题上下文：{context}\n\n{body.strip()}"
        return body.strip()

    def _encode(self, text: str) -> list[int]:
        if self._encoding is None:
            return [ord(char) for char in text]
        return self._encoding.encode(text)

    def _decode(self, tokens: list[int]) -> str:
        if self._encoding is None:
            return "".join(chr(token) for token in tokens)
        return self._encoding.decode(tokens)

    async def _embed_texts(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            if not batch:
                continue
            batch_embeddings = await self.model_router.embed(batch)
            embeddings.extend(batch_embeddings)
        return embeddings

    def _serialize_embedding(self, embedding: list[float]) -> bytes:
        if sqlite_vec is not None:
            return sqlite_vec.serialize_float32([float(value) for value in embedding])
        return json.dumps([float(value) for value in embedding]).encode("utf-8")

    def _distance_to_similarity(self, distance: float) -> float:
        return 1.0 / (1.0 + max(distance, 0.0))

    @staticmethod
    def _rrf_merge(
        *,
        vector_hits: list[ChunkResult],
        keyword_hits: list[ChunkResult],
        top_k: int,
        rrf_k: int = 60,
    ) -> list[ChunkResult]:
        scores: dict[tuple[str, int], float] = {}
        payloads: dict[tuple[str, int], ChunkResult] = {}

        for rank, item in enumerate(vector_hits, start=1):
            key = (item.file_path, item.chunk_index)
            scores[key] = scores.get(key, 0.0) + (1.0 / (rrf_k + rank))
            payloads[key] = item

        for rank, item in enumerate(keyword_hits, start=1):
            key = (item.file_path, item.chunk_index)
            scores[key] = scores.get(key, 0.0) + (1.0 / (rrf_k + rank))
            payloads[key] = item

        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
        results: list[ChunkResult] = []
        for key, score in ranked[:top_k]:
            payload = payloads[key]
            results.append(
                ChunkResult(
                    file_path=payload.file_path,
                    chunk_text=payload.chunk_text,
                    score=score,
                    chunk_index=payload.chunk_index,
                )
            )
        return results

    @staticmethod
    def default_user_query() -> str:
        return _DEFAULT_USER_QUERY

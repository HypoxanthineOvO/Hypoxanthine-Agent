from __future__ import annotations

from datetime import UTC, datetime
import inspect
from pathlib import Path
import re
from typing import Any

import structlog

from hypo_agent.models import SkillOutput

logger = structlog.get_logger("hypo_agent.core.sop_manager")

_SECTION_RE_TEMPLATE = r"^##\s+{heading}\s*$"
_META_CREATED_RE = re.compile(r"^- 创建时间:\s*(.+?)\s*$", re.MULTILINE)
_META_LAST_USED_RE = re.compile(r"^- 最后使用:\s*(.+?)\s*$", re.MULTILINE)
_META_USE_COUNT_RE = re.compile(r"^- 使用次数:\s*(\d+)\s*$", re.MULTILINE)


class SopManager:
    def __init__(
        self,
        *,
        knowledge_dir: Path | str,
        semantic_memory: Any | None = None,
        now_fn=None,
    ) -> None:
        self.knowledge_dir = Path(knowledge_dir)
        self.semantic_memory = semantic_memory
        self._now_fn = now_fn or (lambda: datetime.now(UTC).replace(microsecond=0))

    @property
    def sop_dir(self) -> Path:
        return self.knowledge_dir / "sop"

    async def save_sop(
        self,
        title: str,
        content: str,
        *,
        confirm: bool = False,
        session_id: str | None = None,
    ) -> SkillOutput:
        del session_id
        normalized_title = str(title or "").strip()
        normalized_content = str(content or "").strip()
        if not normalized_title:
            return SkillOutput(status="error", error_info="title is required")
        if not normalized_content:
            return SkillOutput(status="error", error_info="content is required")

        file_path = self._file_path_for_title(normalized_title)
        preview = self._render_template(normalized_title, normalized_content)
        if not confirm:
            return SkillOutput(
                status="success",
                result={
                    "requires_confirmation": True,
                    "title": normalized_title,
                    "file_path": str(file_path),
                    "preview": preview,
                },
            )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(preview, encoding="utf-8")
        await self._update_semantic_index(file_path)
        return SkillOutput(
            status="success",
            result={
                "title": normalized_title,
                "file_path": str(file_path),
            },
        )

    async def search_sop(
        self,
        query: str,
        top_k: int = 3,
        *,
        session_id: str | None = None,
    ) -> SkillOutput:
        del session_id
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return SkillOutput(status="error", error_info="query is required")
        if self.semantic_memory is None or not callable(getattr(self.semantic_memory, "search", None)):
            return SkillOutput(status="success", result={"items": []})

        raw_results = self.semantic_memory.search(normalized_query, top_k=max(top_k * 4, 12))
        if inspect.isawaitable(raw_results):
            raw_results = await raw_results

        sop_root = self.sop_dir.resolve(strict=False)
        dedup: dict[str, float] = {}
        for item in raw_results or []:
            file_path = Path(str(getattr(item, "file_path", "") or "")).resolve(strict=False)
            try:
                file_path.relative_to(sop_root)
            except ValueError:
                continue
            score = float(getattr(item, "score", 0.0) or 0.0)
            dedup[str(file_path)] = max(score, dedup.get(str(file_path), 0.0))

        items: list[dict[str, Any]] = []
        ranked_paths = sorted(dedup.items(), key=lambda item: (-item[1], item[0]))
        for file_path_str, score in ranked_paths[: max(1, int(top_k))]:
            parsed = self._parse_sop_file(Path(file_path_str))
            if parsed is None:
                continue
            parsed["score"] = score
            items.append(parsed)

        return SkillOutput(status="success", result={"items": items})

    async def update_sop_metadata(self, title: str) -> bool:
        file_path = self._file_path_for_title(title)
        return await self._update_sop_file_metadata(file_path)

    async def touch_files(self, file_paths: list[str] | set[str]) -> None:
        for raw_path in sorted({str(path) for path in file_paths if str(path).strip()}):
            path = Path(raw_path)
            try:
                path.resolve(strict=False).relative_to(self.sop_dir.resolve(strict=False))
            except ValueError:
                continue
            await self._update_sop_file_metadata(path)

    def is_sop_path(self, file_path: str | Path) -> bool:
        resolved = Path(file_path).resolve(strict=False)
        try:
            resolved.relative_to(self.sop_dir.resolve(strict=False))
        except ValueError:
            return False
        return True

    async def _update_sop_file_metadata(self, file_path: Path) -> bool:
        if not file_path.exists():
            return False

        content = file_path.read_text(encoding="utf-8")
        title = self._extract_title(content, file_path)
        created_at = self._extract_meta_value(_META_CREATED_RE, content) or self._now_iso()
        use_count = int(self._extract_meta_value(_META_USE_COUNT_RE, content) or 0)
        last_used = self._now_iso()

        meta_block = (
            "## 元信息\n\n"
            f"- 创建时间: {created_at}\n"
            f"- 最后使用: {last_used}\n"
            f"- 使用次数: {use_count + 1}"
        )
        updated = self._replace_meta_block(content, meta_block)
        file_path.write_text(updated, encoding="utf-8")
        await self._update_semantic_index(file_path)
        logger.info("sop.metadata_updated", title=title, usage_count=use_count + 1)
        return True

    def _render_template(self, title: str, content: str) -> str:
        sections = self._parse_sections(content)
        timestamp = self._now_iso()
        applicable = sections.get("适用场景") or "待补充。"
        prerequisites = sections.get("前置条件") or "待补充。"
        steps = sections.get("步骤") or content.strip()
        cautions = sections.get("注意事项") or "待补充。"
        if not steps.lstrip().startswith("1."):
            steps = self._normalize_steps(steps)
        return (
            f"# SOP: {title}\n\n"
            "## 适用场景\n\n"
            f"{applicable}\n\n"
            "## 前置条件\n\n"
            f"{prerequisites}\n\n"
            "## 步骤\n\n"
            f"{steps}\n\n"
            "## 注意事项\n\n"
            f"{cautions}\n\n"
            "## 元信息\n\n"
            f"- 创建时间: {timestamp}\n"
            f"- 最后使用: {timestamp}\n"
            "- 使用次数: 0\n"
        )

    def _parse_sections(self, content: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        for heading in ("适用场景", "前置条件", "步骤", "注意事项"):
            extracted = self._extract_section(content, heading)
            if extracted:
                sections[heading] = extracted
        return sections

    def _extract_section(self, content: str, heading: str) -> str:
        pattern = re.compile(
            _SECTION_RE_TEMPLATE.format(heading=re.escape(heading))
            + r"\n+(.*?)(?=^##\s+|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(content)
        if match is None:
            return ""
        return match.group(1).strip()

    def _normalize_steps(self, content: str) -> str:
        lines = [line.strip(" -") for line in content.splitlines() if line.strip()]
        if not lines:
            return "1. 待补充"
        return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))

    def _parse_sop_file(self, file_path: Path) -> dict[str, Any] | None:
        if not file_path.exists():
            return None
        content = file_path.read_text(encoding="utf-8")
        title = self._extract_title(content, file_path)
        applicable = self._extract_section(content, "适用场景")
        steps = self._extract_section(content, "步骤")
        return {
            "title": title,
            "file_path": str(file_path),
            "applicable_scenario": applicable,
            "steps_summary": self._summarize_steps(steps),
        }

    def _summarize_steps(self, steps: str, limit: int = 3) -> str:
        lines = [line.strip() for line in steps.splitlines() if line.strip()]
        if not lines:
            return ""
        return "\n".join(lines[:limit])

    def _replace_meta_block(self, content: str, meta_block: str) -> str:
        pattern = re.compile(r"^##\s+元信息\s*$.*?(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
        if pattern.search(content):
            return pattern.sub(meta_block, content).rstrip() + "\n"
        suffix = "" if content.endswith("\n\n") else "\n\n"
        return f"{content.rstrip()}{suffix}{meta_block}\n"

    def _extract_meta_value(self, pattern: re.Pattern[str], content: str) -> str:
        match = pattern.search(content)
        if match is None:
            return ""
        return match.group(1).strip()

    def _extract_title(self, content: str, file_path: Path) -> str:
        title = file_path.stem
        heading_match = re.search(r"^#\s+SOP:\s*(.+?)\s*$", content, re.MULTILINE)
        if heading_match is not None:
            title = heading_match.group(1).strip()
        return title

    def _file_path_for_title(self, title: str) -> Path:
        normalized = re.sub(r'[\\/:*?"<>|]+', "-", str(title or "").strip()).strip(". ")
        if not normalized:
            raise ValueError("title is required")
        return self.sop_dir / f"{normalized}.md"

    def _now_iso(self) -> str:
        return self._now_fn().astimezone(UTC).replace(microsecond=0).isoformat()

    async def _update_semantic_index(self, file_path: Path) -> None:
        updater = getattr(self.semantic_memory, "update_index", None)
        if not callable(updater):
            return
        result = updater(file_path)
        if inspect.isawaitable(result):
            await result

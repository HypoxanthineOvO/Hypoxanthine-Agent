from __future__ import annotations

import asyncio
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Callable

import structlog

from hypo_agent.channels.notion import NotionClient, NotionUnavailableError, blocks_to_markdown, markdown_to_blocks
from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.core.uploads import sanitize_upload_filename
from hypo_agent.models import Attachment
from hypo_agent.core.notion_todo_binding import discover_notion_todo_candidate, get_bound_notion_todo_database_id
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill
from hypo_agent.skills.notion_heartbeat import NotionTodoHeartbeatSource

logger = structlog.get_logger("hypo_agent.skills.notion_skill")
_SERVICE_UNAVAILABLE = "Notion 当前不可用，请检查网络、集成密钥和页面授权"
_DEFAULT_TODO_TODAY_MATCH_MODE = "cover_today"
_PAGE_ID_RE = re.compile(
    r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


class NotionSkill(BaseSkill):
    name = "notion"
    description = "读写 Notion 页面和数据库，支持搜索、查询、创建条目和更新页面属性。"
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        secrets_path: Path | str = "config/secrets.yaml",
        notion_client: Any | None = None,
        heartbeat_service: Any | None = None,
        now_fn: Callable[[], datetime] | None = None,
        today_match_mode: str = _DEFAULT_TODO_TODAY_MATCH_MODE,
        exports_dir: Path | str = "memory/exports",
    ) -> None:
        self.secrets_path = Path(secrets_path)
        self.now_fn = now_fn or datetime.now
        self._exports_dir = Path(exports_dir).expanduser().resolve(strict=False)
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        self._todo_today_match_mode = self._normalize_today_match_mode(today_match_mode)
        self._client = notion_client or self._build_client_from_config()
        self._todo_database_id: str | None = self._load_todo_database_id()
        if self._todo_database_id and heartbeat_service is not None and hasattr(
            heartbeat_service, "register_event_source"
        ):
            heartbeat_service.register_event_source(
                "notion_todo",
                NotionTodoHeartbeatSource(
                    notion_client=self._client,
                    todo_database_id=self._todo_database_id,
                    now_fn=self.now_fn,
                    row_normalizer=self.normalize_todo_rows,
                    today_matcher=self.todo_item_matches_today,
                    display_title_getter=self.render_todo_display_title,
                    today_match_mode_getter=self.get_todo_today_match_mode,
                ).collect,
            )

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "notion_get_schema",
                    "description": "Get the property schema for a Notion database.",
                    "parameters": {
                        "type": "object",
                        "properties": {"database_id": {"type": "string"}},
                        "required": ["database_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_read_page",
                    "description": "Read a Notion page's properties and body content.",
                    "parameters": {
                        "type": "object",
                        "properties": {"page_id": {"type": "string"}},
                        "required": ["page_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_export_page_markdown",
                    "description": "Read a Notion page and export it as a Markdown file attachment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "filename": {"type": "string"},
                        },
                        "required": ["page_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_write_page",
                    "description": "Write markdown content to a Notion page.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "content": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["append", "replace"],
                                "default": "append",
                            },
                        },
                        "required": ["page_id", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_update_page",
                    "description": "Update a Notion page's properties from JSON input.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "properties": {"type": "string"},
                        },
                        "required": ["page_id", "properties"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_query_db",
                    "description": "Query a Notion database with optional filter and sort JSON.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "database_id": {"type": "string"},
                            "filter": {"type": "string", "default": ""},
                            "sorts": {"type": "string", "default": ""},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                        },
                        "required": ["database_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_create_entry",
                    "description": "Create a new entry in a Notion database.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "database_id": {"type": "string"},
                            "properties": {"type": "string"},
                            "content": {"type": "string", "default": ""},
                        },
                        "required": ["database_id", "properties"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "notion_search",
                    "description": "Search pages or databases in the connected Notion workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "type": {"type": "string", "enum": ["page", "database"], "default": "page"},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "notion_get_schema":
                database_id = self._normalize_page_id(params.get("database_id"))
                return SkillOutput(status="success", result=await self.notion_get_schema(database_id))
            if tool_name == "notion_read_page":
                page_id = self._normalize_page_id(params.get("page_id"))
                return SkillOutput(status="success", result=await self.notion_read_page(page_id))
            if tool_name == "notion_export_page_markdown":
                page_id = self._normalize_page_id(params.get("page_id"))
                filename = str(params.get("filename") or "")
                return await self.notion_export_page_markdown(page_id, filename=filename)
            if tool_name == "notion_write_page":
                page_id = self._normalize_page_id(params.get("page_id"))
                content = str(params.get("content") or "")
                mode = str(params.get("mode") or "append").strip() or "append"
                return SkillOutput(
                    status="success",
                    result=await self.notion_write_page(page_id, content=content, mode=mode),
                )
            if tool_name == "notion_update_page":
                page_id = self._normalize_page_id(params.get("page_id"))
                properties = self._parse_json_object(params.get("properties"), field_name="properties")
                return SkillOutput(
                    status="success",
                    result=await self.notion_update_page(page_id, properties=properties),
                )
            if tool_name == "notion_query_db":
                database_id = self._normalize_page_id(params.get("database_id"))
                filter_payload = self._parse_optional_json_object(params.get("filter"))
                sorts_payload = self._parse_optional_json_list(params.get("sorts"))
                limit = min(100, max(1, int(params.get("limit") or 20)))
                return SkillOutput(
                    status="success",
                    result=await self.notion_query_db(
                        database_id,
                        filter=filter_payload,
                        sorts=sorts_payload,
                        limit=limit,
                    ),
                )
            if tool_name == "notion_create_entry":
                database_id = self._normalize_page_id(params.get("database_id"))
                properties = self._parse_json_object(params.get("properties"), field_name="properties")
                content = str(params.get("content") or "")
                return SkillOutput(
                    status="success",
                    result=await self.notion_create_entry(
                        database_id,
                        properties=properties,
                        content=content,
                    ),
                )
            if tool_name == "notion_search":
                query = str(params.get("query") or "").strip()
                if not query:
                    return SkillOutput(status="error", error_info="query is required")
                object_type = str(params.get("type") or "page").strip() or "page"
                return SkillOutput(
                    status="success",
                    result=await self.notion_search(query, object_type=object_type),
                )
        except NotionUnavailableError as exc:
            error_text = str(exc).strip()
            if error_text:
                return SkillOutput(status="error", error_info=f"{_SERVICE_UNAVAILABLE}：{error_text}")
            return SkillOutput(status="error", error_info=_SERVICE_UNAVAILABLE)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("notion_skill.execute_failed", tool_name=tool_name, error=str(exc))
            return SkillOutput(status="error", error_info=str(exc))
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def notion_read_page(self, page_id: str) -> str:
        page = await self._client.get_page(page_id)
        blocks = await self._client.get_page_content(page_id)
        title = self._extract_title(page) or page_id
        properties_text = self._format_property_summary(page.get("properties", {}))
        content = blocks_to_markdown(blocks)
        parts = [f"📄 {title}"]
        if properties_text:
            parts.extend(["", f"属性：{properties_text}"])
        parts.extend(["", "---"])
        if content:
            parts.extend(["", content])
        return "\n".join(parts).strip()

    async def notion_export_page_markdown(self, page_id: str, *, filename: str = "") -> SkillOutput:
        markdown = await self.notion_read_page(page_id)
        safe_stem = self._safe_export_stem(filename or page_id)
        target_path = self._build_export_path(safe_stem, ".md")
        target_path.write_text(markdown + "\n", encoding="utf-8")
        attachment = self._build_attachment(target_path, mime_type="text/markdown")
        return SkillOutput(
            status="success",
            result=str(target_path),
            metadata={"format": "markdown", "page_id": page_id},
            attachments=[attachment],
        )

    async def notion_write_page(self, page_id: str, *, content: str, mode: str = "append") -> str:
        blocks = markdown_to_blocks(content)
        if mode == "replace":
            existing_blocks = await self._client.get_page_content(page_id)
            for block in existing_blocks:
                block_type = str(block.get("type") or "")
                if block_type in {"child_page", "child_database"}:
                    continue
                block_id = str(block.get("id") or "").strip()
                if block_id:
                    await self._client.delete_block(block_id)
        await self._client.append_blocks(page_id, blocks)
        return f"已写入 {len(blocks)} 个块到页面 {page_id}"

    async def notion_update_page(self, page_id: str, *, properties: dict[str, Any]) -> str:
        page = await self._client.get_page(page_id)
        schema = self._page_schema(page)
        parent = page.get("parent", {}) if isinstance(page.get("parent"), dict) else {}
        database_id = str(parent.get("database_id") or "").strip()
        if database_id:
            database = await self._client.get_database(database_id)
            schema.update(self._database_schema(database))
        notion_properties = self._convert_properties(properties, schema=schema)
        updated = await self._client.update_page_properties(page_id, notion_properties)
        return f"已更新页面属性：{self._format_property_summary(updated.get('properties', notion_properties))}"

    async def notion_query_db(
        self,
        database_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 20,
    ) -> str:
        database = await self._client.get_database(database_id)
        schema = self._database_schema(database)
        normalized_filter = self._normalize_query_filter(filter, schema=schema)
        normalized_sorts = self._normalize_query_sorts(sorts, schema=schema)
        rows = await self._client.query_database(
            database_id,
            filter=normalized_filter,
            sorts=normalized_sorts,
            page_size=limit,
        )
        shown = rows[:limit]
        title = self._extract_database_title(database) or database_id
        lines = [
            f"📊 {title}（共 {len(rows)} 条）",
            "",
            "| 标题 | Status | Tags | 更新时间 |",
            "| --- | --- | --- | --- |",
        ]
        for row in shown:
            lines.append(
                "| "
                + " | ".join(
                    [
                        self._extract_title(row) or "-",
                        self._extract_property_text(row.get("properties", {}), "Status") or "-",
                        self._extract_property_text(row.get("properties", {}), "Tags") or "-",
                        self._format_date(row.get("last_edited_time")) or "-",
                    ]
                )
                + " |"
            )
        if len(rows) > limit:
            lines.extend(["", f"仅显示前 {limit} 条。"])
        return "\n".join(lines)

    async def notion_create_entry(
        self,
        database_id: str,
        *,
        properties: dict[str, Any],
        content: str = "",
    ) -> str:
        database = await self._client.get_database(database_id)
        notion_properties = self._convert_properties(properties, schema=self._database_schema(database))
        children = markdown_to_blocks(content) if content.strip() else None
        created = await self._client.create_page(
            parent={"database_id": database_id},
            properties=notion_properties,
            children=children,
        )
        title = self._extract_title(created) or self._extract_title_from_payload(notion_properties) or database_id
        created_id = str(created.get("id") or "")
        return f"已创建条目：{title}（ID: {created_id}）"

    async def notion_search(self, query: str, *, object_type: str = "page") -> str:
        results = await self._client.search(query, object_type=object_type, page_size=10)
        lines = [f"🔍 搜索结果（共 {len(results)} 条）", ""]
        icon = "📄" if object_type == "page" else "📊"
        for index, item in enumerate(results, start=1):
            title = self._extract_title(item) or self._extract_database_title(item) or str(item.get("id") or "")
            lines.append(
                f"{index}. {icon} {title} - 最后编辑: {self._format_date(item.get('last_edited_time')) or '-'} - ID: {item.get('id')}"
            )
        return "\n".join(lines).strip()

    async def get_todo_snapshot(
        self,
        *,
        structured_store: Any | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        database_id = await get_bound_notion_todo_database_id(
            structured_store,
            configured_database_id=self._todo_database_id,
        )
        if not database_id:
            discovery = await discover_notion_todo_candidate(structured_store, self._client)
            return {
                "available": False,
                "error": str(discovery.get("error") or "notion todo database is unavailable"),
                "human_summary": str(discovery.get("human_summary") or "").strip(),
                "binding_status": str(discovery.get("status") or "").strip(),
                "candidate": discovery.get("candidate"),
                "candidates": discovery.get("candidates"),
            }
        try:
            rows = await self._client.query_database(database_id, filter=None, sorts=None, page_size=limit)
        except NotionUnavailableError as exc:
            error_text = str(exc).strip() or _SERVICE_UNAVAILABLE
            return {
                "available": False,
                "database_id": database_id,
                "error": error_text,
                "human_summary": f"Notion 待办查询失败：{error_text}",
            }
        return {
            "available": True,
            "database_id": database_id,
            "items": await self.normalize_todo_rows(rows),
        }

    def configure_todo_heartbeat(self, *, today_match_mode: str | None = None) -> None:
        if today_match_mode is not None:
            self._todo_today_match_mode = self._normalize_today_match_mode(today_match_mode)

    def get_todo_today_match_mode(self) -> str:
        return self._todo_today_match_mode

    async def normalize_todo_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await self._normalize_todo_rows(rows)

    def render_todo_display_title(self, item: dict[str, Any]) -> str:
        display_title = str(item.get("display_title") or "").strip()
        if display_title:
            return display_title
        title = str(item.get("title") or "").strip() or "未命名任务"
        parent_title = str(item.get("parent_title") or "").strip()
        if parent_title:
            return f"{parent_title} / {title}"
        return title

    def todo_item_due_date(self, item: dict[str, Any]) -> date | None:
        due_text = (
            str(item.get("date_end") or "").strip()
            or str(item.get("due_date") or "").strip()
            or str(item.get("date_start") or "").strip()
        )
        return self._parse_iso_date(due_text)

    def todo_item_matches_today(
        self,
        item: dict[str, Any],
        *,
        today: date | None = None,
        match_mode: str | None = None,
    ) -> bool:
        current_day = today or self.now_fn().date()
        resolved_mode = self._normalize_today_match_mode(match_mode)
        start = self._parse_iso_date(
            str(item.get("date_start") or "").strip() or str(item.get("due_date") or "").strip()
        )
        end = self._parse_iso_date(
            str(item.get("date_end") or "").strip()
            or str(item.get("due_date") or "").strip()
            or str(item.get("date_start") or "").strip()
        )
        if start is None:
            return False
        if resolved_mode == "due_only":
            return start == current_day
        if end is None:
            end = start
        if end < start:
            end = start
        return start <= current_day <= end

    async def notion_get_schema(self, database_id: str) -> str:
        database = await self._client.get_database(database_id)
        properties = database.get("properties", {})
        title = self._extract_database_title(database) or database_id
        lines = [f"📊 {title} 字段列表：", ""]
        for name, value in properties.items():
            prop_type = str(value.get("type") or "").strip() if isinstance(value, dict) else ""
            lines.append(f"- `{name}` ({prop_type})")
        return "\n".join(lines)

    def _build_client_from_config(self) -> NotionClient:
        try:
            secrets = load_secrets_config(self.secrets_path)
        except FileNotFoundError as exc:
            raise ValueError(
                "Missing Notion config: config/secrets.yaml -> services.notion.integration_secret"
            ) from exc
        services = secrets.services
        notion_cfg = services.notion if services is not None else None
        integration_secret = (
            str(notion_cfg.integration_secret).strip() if notion_cfg is not None else ""
        )
        if not integration_secret:
            raise ValueError(
                "Missing Notion config: config/secrets.yaml -> services.notion.integration_secret"
            )
        proxy_url = str(notion_cfg.proxy_url).strip() if notion_cfg is not None else ""
        timeout_ms = int(getattr(notion_cfg, "timeout_ms", 60_000) or 60_000)
        api_timeout_seconds = float(getattr(notion_cfg, "api_timeout_seconds", 30.0) or 30.0)
        max_retries = int(getattr(notion_cfg, "max_retries", 3) or 3)
        return NotionClient(
            integration_secret=integration_secret,
            proxy_url=proxy_url or None,
            timeout_ms=timeout_ms,
            api_timeout_seconds=api_timeout_seconds,
            max_retries=max_retries,
        )

    def _load_todo_database_id(self) -> str | None:
        try:
            services = load_secrets_config(self.secrets_path).services
        except FileNotFoundError:
            return None
        notion_cfg = services.notion if services is not None else None
        value = str(notion_cfg.todo_database_id).strip() if notion_cfg is not None else ""
        return value or None

    def _normalize_page_id(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("page_id/database_id is required")
        match = _PAGE_ID_RE.search(raw)
        if not match:
            raise ValueError(f"Invalid Notion ID or URL: {raw}")
        cleaned = match.group(1).replace("-", "").lower()
        return (
            f"{cleaned[0:8]}-{cleaned[8:12]}-{cleaned[12:16]}-"
            f"{cleaned[16:20]}-{cleaned[20:32]}"
        )

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if not callable(close):
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result

    def _parse_json_object(self, raw: Any, *, field_name: str) -> dict[str, Any]:
        try:
            payload = json.loads(str(raw or ""))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{field_name} must be a JSON object")
        return payload

    def _parse_optional_json_object(self, raw: Any) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        payload = self._parse_json_object(text, field_name="filter")
        return payload or None

    def _safe_export_stem(self, filename: str) -> str:
        cleaned = sanitize_upload_filename(filename or "notion-export")
        stem = Path(cleaned).stem.strip() or "notion-export"
        return stem

    def _build_export_path(self, stem: str, suffix: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return (self._exports_dir / f"{stamp}_{stem}{suffix}").resolve(strict=False)

    def _build_attachment(self, path: Path, *, mime_type: str) -> Attachment:
        resolved = path.expanduser().resolve(strict=False)
        return Attachment(
            type="file",
            url=str(resolved),
            filename=resolved.name,
            mime_type=mime_type,
            size_bytes=resolved.stat().st_size,
        )

    def _parse_optional_json_list(self, raw: Any) -> list[dict[str, Any]] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("sorts must be valid JSON") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("sorts must be a JSON array")
        return payload or None

    def _page_schema(self, page: dict[str, Any]) -> dict[str, str]:
        schema: dict[str, str] = {}
        properties = page.get("properties", {})
        if not isinstance(properties, dict):
            return schema
        for name, value in properties.items():
            if isinstance(value, dict):
                prop_type = str(value.get("type") or "").strip()
                if prop_type:
                    schema[str(name)] = prop_type
        return schema

    def _database_schema(self, database: dict[str, Any]) -> dict[str, str]:
        schema: dict[str, str] = {}
        properties = database.get("properties", {})
        if not isinstance(properties, dict):
            return schema
        for name, value in properties.items():
            if isinstance(value, dict):
                prop_type = str(value.get("type") or "").strip()
                if prop_type:
                    schema[str(name)] = prop_type
        return schema

    def _convert_properties(self, payload: dict[str, Any], *, schema: dict[str, str]) -> dict[str, Any]:
        notion_properties: dict[str, Any] = {}
        for name, value in payload.items():
            resolved_name = self._resolve_schema_property_name(name, schema=schema)
            prop_type = schema.get(resolved_name) or self._infer_property_type(resolved_name, value)
            notion_properties[resolved_name] = self._convert_property_value(
                resolved_name,
                value,
                prop_type=prop_type,
            )
        return notion_properties

    def _convert_property_value(self, name: str, value: Any, *, prop_type: str) -> dict[str, Any]:
        if prop_type == "title":
            return {"title": [{"type": "text", "text": {"content": str(value)}}]}
        if prop_type == "rich_text":
            return {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}
        if prop_type == "select":
            return {"select": {"name": str(value)}}
        if prop_type == "status":
            return {"status": {"name": str(value)}}
        if prop_type == "multi_select":
            values = value if isinstance(value, list) else [value]
            return {"multi_select": [{"name": str(item)} for item in values]}
        if prop_type == "number":
            return {"number": value}
        if prop_type == "checkbox":
            return {"checkbox": bool(value)}
        if prop_type == "date":
            if isinstance(value, dict):
                if isinstance(value.get("date"), dict):
                    return {"date": dict(value.get("date") or {})}
                return {"date": value}
            return {"date": {"start": str(value)}}
        if prop_type == "url":
            return {"url": str(value)}
        if prop_type == "email":
            return {"email": str(value)}
        if prop_type == "phone_number":
            return {"phone_number": str(value)}
        if prop_type in {"rollup", "created_time", "last_edited_time", "formula"}:
            raise ValueError(f"属性 {name}（{prop_type}）不可通过 API 直接更新")
        return {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}

    def _infer_property_type(self, name: str, value: Any) -> str:
        lowered = str(name).strip().casefold()
        if lowered in {"name", "title"}:
            return "title"
        if isinstance(value, bool):
            return "checkbox"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, list):
            return "multi_select"
        return "rich_text"

    def _resolve_schema_property_name(self, name: str, *, schema: dict[str, str]) -> str:
        raw_name = str(name or "").strip()
        if not raw_name:
            return raw_name
        if raw_name in schema:
            return raw_name

        lowered = raw_name.casefold()
        for candidate in schema:
            if str(candidate).casefold() == lowered:
                return str(candidate)

        aliases = (
            self._property_alias_candidates(raw_name)
            + self._property_alias_candidates(schema.get(raw_name) or "")
        )
        for alias in aliases:
            for candidate in schema:
                if str(candidate or "").casefold() == alias.casefold():
                    return str(candidate)

        inferred = self._infer_schema_property_name(raw_name, schema=schema)
        if inferred:
            return inferred
        return raw_name

    def _property_alias_candidates(self, name: str) -> list[str]:
        lowered = str(name or "").strip().casefold()
        if lowered in {"name", "title", "名称"}:
            return ["名称", "Name", "Title"]
        if lowered in {"due date", "due", "deadline", "截止日期", "截至", "日期", "time", "时间"}:
            return ["日期", "Date", "Due Date", "Due", "Deadline", "截止日期", "截至", "When"]
        if lowered in {"done", "completed", "完成", "已完成"}:
            return ["已完成", "Done", "Completed", "完成"]
        if lowered in {"status", "状态"}:
            return ["状态", "Status"]
        if lowered in {"tags", "tag", "标签"}:
            return ["标签", "Tags", "Tag"]
        if lowered in {"priority", "优先级", "优先程度"}:
            return ["优先级", "优先程度", "Priority"]
        if lowered in {"description", "描述"}:
            return ["描述", "Description"]
        return []

    def _infer_schema_property_name(self, name: str, *, schema: dict[str, str]) -> str:
        lowered = str(name or "").strip().casefold()
        if lowered in {"due date", "due", "deadline", "截止日期", "截至", "日期", "date", "time", "时间", "when"}:
            return self._unique_schema_property_by_type(schema, "date")
        return ""

    def _unique_schema_property_by_type(self, schema: dict[str, str], *property_types: str) -> str:
        matches = [name for name, prop_type in schema.items() if prop_type in property_types]
        if len(matches) == 1:
            return str(matches[0])
        return ""

    def _normalize_query_filter(
        self,
        payload: dict[str, Any] | None,
        *,
        schema: dict[str, str],
    ) -> dict[str, Any] | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            return payload
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "property" and isinstance(value, str):
                normalized[key] = self._resolve_schema_property_name(value, schema=schema)
                continue
            if isinstance(value, dict):
                normalized[key] = self._normalize_query_filter(value, schema=schema)
                continue
            if isinstance(value, list):
                normalized[key] = [
                    self._normalize_query_filter(item, schema=schema) if isinstance(item, dict) else item
                    for item in value
                ]
                continue
            normalized[key] = value
        return normalized

    def _normalize_query_sorts(
        self,
        payload: list[dict[str, Any]] | None,
        *,
        schema: dict[str, str],
    ) -> list[dict[str, Any]] | None:
        if payload is None:
            return None
        normalized: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            updated = dict(item)
            if isinstance(updated.get("property"), str):
                updated["property"] = self._resolve_schema_property_name(
                    str(updated["property"]),
                    schema=schema,
                )
            normalized.append(updated)
        return normalized

    def _extract_title(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        properties = payload.get("properties", {})
        if isinstance(properties, dict):
            for value in properties.values():
                if not isinstance(value, dict):
                    continue
                prop_type = str(value.get("type") or "")
                if prop_type == "title":
                    return self._extract_rich_text(value.get("title", []))
        title = payload.get("title")
        if isinstance(title, list):
            return self._extract_rich_text(title)
        return ""

    def _extract_database_title(self, payload: dict[str, Any]) -> str:
        title = payload.get("title")
        if isinstance(title, list):
            return self._extract_rich_text(title)
        return self._extract_title(payload)

    def _extract_title_from_payload(self, properties: dict[str, Any]) -> str:
        for value in properties.values():
            if isinstance(value, dict) and isinstance(value.get("title"), list):
                return self._extract_rich_text(value["title"])
        return ""

    def _extract_first_property(self, properties: Any, *names: str) -> str:
        if not isinstance(properties, dict):
            return ""
        for name in names:
            value = properties.get(name)
            if isinstance(value, dict):
                parsed = self._extract_property_value(value)
                if parsed:
                    return parsed
        return ""

    def _format_property_summary(self, properties: Any) -> str:
        if not isinstance(properties, dict):
            return ""
        pairs: list[str] = []
        for name, value in properties.items():
            text = self._extract_property_value(value)
            if text:
                pairs.append(f"{name}={text}")
        return ", ".join(pairs)

    def _extract_property_text(self, properties: Any, name: str) -> str:
        if not isinstance(properties, dict):
            return ""
        value = properties.get(name)
        return self._extract_property_value(value) if isinstance(value, dict) else ""

    def _extract_property_value(self, value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        prop_type = str(value.get("type") or "").strip()
        if not prop_type:
            return ""
        payload = value.get(prop_type)
        if prop_type in {"title", "rich_text"} and isinstance(payload, list):
            return self._extract_rich_text(payload)
        if prop_type in {"select", "status"} and isinstance(payload, dict):
            return str(payload.get("name") or "")
        if prop_type == "multi_select" and isinstance(payload, list):
            return ", ".join(str(item.get("name") or "") for item in payload if isinstance(item, dict))
        if prop_type == "number":
            return str(payload)
        if prop_type == "checkbox":
            return "true" if bool(payload) else "false"
        if prop_type == "date" and isinstance(payload, dict):
            return str(payload.get("start") or "")
        return ""

    def _extract_rich_text(self, items: Any) -> str:
        if not isinstance(items, list):
            return ""
        parts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            parts.append(str(item.get("plain_text") or item.get("text", {}).get("content") or ""))
        return "".join(parts).strip()

    def _format_date(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text[:10]

    def _normalize_today_match_mode(self, value: str | None) -> str:
        text = str(value or "").strip().casefold()
        if text == "due_only":
            return "due_only"
        return _DEFAULT_TODO_TODAY_MATCH_MODE

    def _parse_iso_date(self, value: Any) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text[:10]).date()
        except ValueError:
            return None

    def _extract_due_date(self, row: dict[str, Any]) -> str:
        properties = row.get("properties", {})
        if not isinstance(properties, dict):
            return ""
        for key in ("Due Date", "Due", "Deadline", "截止日期", "截至"):
            value = properties.get(key)
            if isinstance(value, dict):
                parsed = self._extract_property_value(value)
                if parsed:
                    return parsed[:10]
        return ""

    def _extract_status(self, row: dict[str, Any]) -> str:
        properties = row.get("properties", {})
        if not isinstance(properties, dict):
            return ""
        for key in ("Status", "状态"):
            value = properties.get(key)
            if isinstance(value, dict):
                parsed = self._extract_property_value(value)
                if parsed:
                    return parsed
        return ""

    async def _normalize_todo_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        row_parent_ids: dict[str, list[str]] = {}
        unique_parent_ids: list[str] = []
        seen_parent_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            parent_ids = self._extract_parent_relation_ids(row)
            row_id = str(row.get("id") or "").strip()
            if row_id and parent_ids:
                row_parent_ids[row_id] = parent_ids
            for parent_id in parent_ids:
                if parent_id in seen_parent_ids:
                    continue
                seen_parent_ids.add(parent_id)
                unique_parent_ids.append(parent_id)

        parent_titles: dict[str, str] = {}
        if unique_parent_ids:
            titles = await asyncio.gather(
                *(self._safe_get_page_title(parent_id) for parent_id in unique_parent_ids)
            )
            for parent_id, title in zip(unique_parent_ids, titles, strict=False):
                if title:
                    parent_titles[parent_id] = title

        items: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            parent_ids = row_parent_ids.get(str(row.get("id") or "").strip(), [])
            parent_page_id = parent_ids[0] if parent_ids else ""
            items.append(
                self._normalize_todo_row(
                    row,
                    parent_page_id=parent_page_id,
                    parent_title=parent_titles.get(parent_page_id, ""),
                )
            )
        return items

    async def _safe_get_page_title(self, page_id: str) -> str:
        try:
            page = await self._client.get_page(page_id)
        except NotionUnavailableError:
            return ""
        return self._extract_title(page) or page_id

    def _normalize_todo_row(
        self,
        row: dict[str, Any],
        *,
        parent_page_id: str = "",
        parent_title: str = "",
    ) -> dict[str, Any]:
        properties = row.get("properties", {})
        status = str(row.get("status") or "").strip() or self._extract_first_property(properties, "Status", "状态")
        priority = str(row.get("priority") or "").strip() or self._extract_first_property(
            properties,
            "Priority",
            "优先级",
            "优先程度",
        )
        date_start = str(row.get("date_start") or "").strip()
        date_end = str(row.get("date_end") or "").strip()
        if not date_start:
            date_start, date_end = self._extract_first_date_range(
                properties,
                "日期",
                "Due Date",
                "Due",
                "Deadline",
                "截止日期",
                "截至",
            )
        if not date_end:
            date_end = date_start
        is_date_span = bool(row.get("is_date_span")) or (
            bool(date_start) and bool(date_end) and date_end != date_start
        )
        due_date = str(row.get("due_date") or "").strip() or date_end or date_start
        tags = str(row.get("tags") or "").strip() or self._extract_first_property(properties, "Tags", "标签")
        recurrence = str(row.get("recurrence") or "").strip() or self._extract_first_property(
            properties,
            "Repeat",
            "Repeating",
            "Recurring",
            "Recurrence",
            "重复",
            "重复规则",
            "周期",
            "频率",
        )
        done_value = str(row.get("done") or "").strip() or self._extract_first_property(
            properties,
            "已完成",
            "Done",
            "完成",
        )
        title = str(row.get("title") or "").strip() or self._extract_title(row)
        normalized_status = status.casefold()
        normalized_done = done_value.casefold()
        display_title = str(row.get("display_title") or "").strip() or self.render_todo_display_title(
            {"title": title, "parent_title": parent_title}
        )
        return {
            "id": str(row.get("id") or ""),
            "title": title,
            "display_title": display_title,
            "status": status,
            "priority": priority,
            "due_date": due_date[:10] if due_date else "",
            "date_start": date_start[:10] if date_start else "",
            "date_end": date_end[:10] if date_end else "",
            "is_date_span": is_date_span,
            "tags": tags,
            "done": normalized_done == "true" or normalized_status in {"done", "completed", "完成", "已完成"},
            "recurrence": recurrence,
            "parent_page_id": parent_page_id,
            "parent_title": parent_title,
        }

    def _extract_parent_relation_ids(self, row: dict[str, Any]) -> list[str]:
        properties = row.get("properties", {})
        if not isinstance(properties, dict):
            return []
        explicit_names = (
            "Parent item",
            "Parent",
            "Parent task",
            "父任务",
            "父级任务",
            "上级任务",
            "所属任务",
        )
        for name in explicit_names:
            relation_ids = self._extract_relation_property_ids(properties.get(name))
            if relation_ids:
                return relation_ids
        for name, value in properties.items():
            label = str(name or "").casefold()
            if "parent" not in label and "父" not in label and "上级" not in str(name or ""):
                continue
            relation_ids = self._extract_relation_property_ids(value)
            if relation_ids:
                return relation_ids
        return []

    def _extract_relation_property_ids(self, value: Any) -> list[str]:
        if not isinstance(value, dict):
            return []
        if str(value.get("type") or "").strip() != "relation":
            return []
        relation = value.get("relation")
        if not isinstance(relation, list):
            return []
        ids: list[str] = []
        for item in relation:
            if not isinstance(item, dict):
                continue
            page_id = str(item.get("id") or "").strip()
            if page_id:
                ids.append(page_id)
        return ids

    def _extract_first_date_range(self, properties: Any, *names: str) -> tuple[str, str]:
        if not isinstance(properties, dict):
            return "", ""
        for name in names:
            value = properties.get(name)
            start, end = self._extract_date_range_property(value)
            if start:
                return start, end
        return "", ""

    def _extract_date_range_property(self, value: Any) -> tuple[str, str]:
        if not isinstance(value, dict):
            return "", ""
        if str(value.get("type") or "").strip() != "date":
            return "", ""
        payload = value.get("date")
        if not isinstance(payload, dict):
            return "", ""
        start = str(payload.get("start") or "").strip()[:10]
        end = str(payload.get("end") or "").strip()[:10] or start
        return start, end

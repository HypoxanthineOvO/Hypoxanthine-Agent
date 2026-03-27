from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable

import structlog

from hypo_agent.channels.notion import NotionClient, NotionUnavailableError, blocks_to_markdown, markdown_to_blocks
from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

logger = structlog.get_logger("hypo_agent.skills.notion_skill")
_SERVICE_UNAVAILABLE = "Notion 当前不可用，请检查网络、集成密钥和页面授权"
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
    ) -> None:
        self.secrets_path = Path(secrets_path)
        self.now_fn = now_fn or datetime.now
        self._client = notion_client or self._build_client_from_config()
        self._todo_database_id: str | None = self._load_todo_database_id()
        if self._todo_database_id and heartbeat_service is not None and hasattr(
            heartbeat_service, "register_event_source"
        ):
            heartbeat_service.register_event_source("notion_todo", self._heartbeat_todo_source)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "notion_read_page",
                    "description": "读取 Notion 页面的属性和正文内容，支持 page_id 或完整页面 URL。",
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
                    "name": "notion_write_page",
                    "description": "向 Notion 页面写入 Markdown 内容，支持 append 或 replace。",
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
                    "description": "更新 Notion 页面属性，properties 使用 JSON 字符串。",
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
                    "description": "查询 Notion 数据库，filter 和 sorts 使用 Notion API JSON。",
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
                    "description": "在 Notion 数据库中创建新条目，可附带 Markdown 正文。",
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
                    "description": "在 Notion 工作区搜索页面或数据库。",
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
            if tool_name == "notion_read_page":
                page_id = self._normalize_page_id(params.get("page_id"))
                return SkillOutput(status="success", result=await self.notion_read_page(page_id))
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
        except NotionUnavailableError:
            return SkillOutput(status="error", error_info=_SERVICE_UNAVAILABLE)
        except Exception as exc:
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
        rows = await self._client.query_database(database_id, filter=filter, sorts=sorts, page_size=limit)
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

    async def _heartbeat_todo_source(self) -> dict[str, Any] | None:
        database_id = str(self._todo_database_id or "").strip()
        if not database_id:
            return None
        rows = await self._client.query_database(database_id, page_size=50)
        today = self.now_fn().date().isoformat()
        items: list[dict[str, str]] = []
        for row in rows:
            due = self._extract_due_date(row)
            status = self._extract_status(row)
            if due == today and status.casefold() not in {"done", "complete", "completed", "已完成"}:
                title = self._extract_title(row) or str(row.get("id") or "")
                items.append({"title": f"{title}（截止今天）"})
        if not items:
            return None
        return {"items": items}

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
        return NotionClient(integration_secret=integration_secret)

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
            prop_type = schema.get(name) or self._infer_property_type(name, value)
            notion_properties[name] = self._convert_property_value(name, value, prop_type=prop_type)
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

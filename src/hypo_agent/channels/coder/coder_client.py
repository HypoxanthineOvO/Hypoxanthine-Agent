from __future__ import annotations

from typing import Any

import httpx

from hypo_agent.exceptions import ExternalServiceError

_UNAVAILABLE_MESSAGE = "Hypo-Coder 当前不可用，请确认服务是否启动"


class CoderUnavailableError(ExternalServiceError):
    """Raised when Hypo-Coder cannot be reached or returns an unusable response."""


class CoderClient:
    def __init__(
        self,
        base_url: str,
        agent_token: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_token = agent_token
        self.timeout_seconds = timeout_seconds

    def supports_streaming(self) -> bool:
        return False

    def supports_continuation(self) -> bool:
        return False

    async def create_task(
        self,
        prompt: str,
        working_directory: str,
        model: str = "o4-mini",
        approval_policy: str = "full-auto",
        webhook: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "prompt": prompt,
            "workingDirectory": working_directory,
            "model": model,
            "approvalPolicy": approval_policy,
        }
        if webhook:
            payload["webhook"] = webhook
        response = await self._request("POST", "/api/tasks", json=payload)
        return response if isinstance(response, dict) else {}

    async def get_task(self, task_id: str) -> dict:
        response = await self._request("GET", f"/api/tasks/{task_id}")
        return response if isinstance(response, dict) else {}

    async def abort_task(self, task_id: str) -> dict:
        response = await self._request("POST", f"/api/tasks/{task_id}/abort")
        return response if isinstance(response, dict) else {}

    async def list_tasks(self, status: str | None = None) -> list[dict]:
        params = {"status": status} if status else None
        response = await self._request("GET", "/api/tasks", params=params)
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if isinstance(response, dict):
            for key in ("tasks", "items", "results", "data"):
                value = response.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    async def health(self) -> dict:
        response = await self._request("GET", "/api/health")
        return response if isinstance(response, dict) else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        headers = {"Authorization": f"Bearer {self.agent_token}"}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                headers=headers,
            ) as client:
                response = await client.request(method, path, json=json, params=params)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CoderUnavailableError(_UNAVAILABLE_MESSAGE) from exc

from __future__ import annotations

import json
from typing import Any

import httpx

from hypo_agent.exceptions import ExternalServiceError


class WeWeRSSClientError(ExternalServiceError):
    """Base exception for WeWe RSS API failures."""


class WeWeRSSAuthError(WeWeRSSClientError):
    """Raised when WeWe RSS rejects the configured authCode."""


class WeWeRSSProtocolError(WeWeRSSClientError):
    """Raised when WeWe RSS returns an unexpected payload shape."""


class WeWeRSSClient:
    def __init__(
        self,
        base_url: str,
        auth_code: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.auth_code = str(auth_code or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=transport,
            headers={"Authorization": self.auth_code},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_accounts(self) -> dict[str, Any]:
        payload = await self._query("account.list", None)
        if not isinstance(payload, dict):
            raise WeWeRSSProtocolError("account.list returned non-object payload")
        items = payload.get("items")
        blocks = payload.get("blocks")
        return {
            "items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else [],
            "blocks": [item for item in blocks if isinstance(item, (str, int))] if isinstance(blocks, list) else [],
        }

    async def create_login_url(self) -> dict[str, Any]:
        payload = await self._mutation("platform.createLoginUrl", None)
        if not isinstance(payload, dict):
            raise WeWeRSSProtocolError("platform.createLoginUrl returned non-object payload")
        return payload

    async def get_login_result(self, login_id: str) -> dict[str, Any]:
        payload = await self._query("platform.getLoginResult", {"id": str(login_id or "").strip()})
        if not isinstance(payload, dict):
            raise WeWeRSSProtocolError("platform.getLoginResult returned non-object payload")
        return payload

    async def add_account(self, *, id: str, name: str, token: str) -> dict[str, Any]:
        payload = await self._mutation(
            "account.add",
            {
                "id": str(id or "").strip(),
                "name": str(name or "").strip(),
                "token": str(token or "").strip(),
            },
        )
        if not isinstance(payload, dict):
            raise WeWeRSSProtocolError("account.add returned non-object payload")
        return payload

    async def _query(self, path: str, input_payload: dict[str, Any] | None) -> Any:
        batch_item: dict[str, Any] = {}
        if isinstance(input_payload, dict):
            batch_item.update(input_payload)
        elif input_payload is not None:
            batch_item["input"] = input_payload
        params = {
            "batch": "1",
            "input": json.dumps(
                {"0": batch_item},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        response = await self._client.get(f"/trpc/{path}", params=params)
        return self._unwrap_response(response, path=path)

    async def _mutation(self, path: str, input_payload: dict[str, Any] | None) -> Any:
        if input_payload is None:
            attempts = (
                ({}, {"json": None}),
                ({"batch": "1"}, {"0": {"json": None}}),
                ({}, None),
            )
        else:
            attempts = (
                ({}, input_payload),
                ({"batch": "1"}, {"0": input_payload}),
                ({}, {"json": input_payload}),
                ({"batch": "1"}, {"0": {"json": input_payload}}),
            )
        last_error: Exception | None = None
        for params, body in attempts:
            response = await self._client.post(f"/trpc/{path}", params=params, json=body)
            try:
                return self._unwrap_response(response, path=path)
            except WeWeRSSProtocolError as exc:
                last_error = exc
                continue
        raise last_error or WeWeRSSProtocolError(f"{path} returned unusable mutation payload")

    def _unwrap_response(self, response: httpx.Response, *, path: str) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeWeRSSProtocolError(f"{path} returned invalid JSON") from exc

        error_payload = self._extract_error_payload(payload)
        if response.status_code == 401 or self._is_auth_error(error_payload):
            detail = self._error_message(error_payload) or f"{path} unauthorized"
            raise WeWeRSSAuthError(detail, operation=path, status_code=response.status_code)
        if response.status_code >= 400:
            detail = self._error_message(error_payload) or f"{path} http {response.status_code}"
            raise WeWeRSSClientError(detail, operation=path, status_code=response.status_code)

        result_payload = self._extract_result_payload(payload)
        if result_payload is None:
            raise WeWeRSSProtocolError(f"{path} missing result payload")
        return result_payload

    def _extract_result_payload(self, payload: Any) -> Any | None:
        if isinstance(payload, list):
            if not payload:
                return None
            return self._extract_result_payload(payload[0])
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        data = result.get("data")
        if not isinstance(data, dict):
            return None
        if "json" in data:
            return data.get("json")
        return data

    def _extract_error_payload(self, payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, list):
            for item in payload:
                extracted = self._extract_error_payload(item)
                if extracted is not None:
                    return extracted
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        return error if isinstance(error, dict) else None

    def _is_auth_error(self, error_payload: dict[str, Any] | None) -> bool:
        if not isinstance(error_payload, dict):
            return False
        message = str(error_payload.get("message") or "").strip().lower()
        data = error_payload.get("data")
        code = str(data.get("code") or "").strip().upper() if isinstance(data, dict) else ""
        return code == "UNAUTHORIZED" or "authcode" in message

    def _error_message(self, error_payload: dict[str, Any] | None) -> str:
        if not isinstance(error_payload, dict):
            return ""
        data = error_payload.get("data")
        parts = [str(error_payload.get("message") or "").strip()]
        if isinstance(data, dict):
            path = str(data.get("path") or "").strip()
            if path:
                parts.append(path)
        return " | ".join(part for part in parts if part)

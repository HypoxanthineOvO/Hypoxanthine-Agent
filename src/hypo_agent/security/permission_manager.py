from __future__ import annotations

from pathlib import Path
from typing import Literal

import structlog

from hypo_agent.models import DirectoryWhitelist, WhitelistRule

logger = structlog.get_logger()

Operation = Literal["read", "write", "execute"]


class PermissionManager:
    def __init__(self, whitelist: DirectoryWhitelist) -> None:
        self._whitelist = whitelist
        self._resolved_rules: list[tuple[Path, set[str], WhitelistRule]] = []
        for rule in whitelist.rules:
            resolved = self._resolve_path(rule.path)
            self._resolved_rules.append((resolved, set(rule.permissions), rule))

        self._resolved_rules.sort(key=lambda item: len(str(item[0])), reverse=True)

    def check_permission(
        self,
        path: str,
        operation: Operation,
        *,
        log_allowed: bool = True,
    ) -> tuple[bool, str]:
        try:
            resolved_path = self._resolve_path(path)
        except (OSError, RuntimeError) as exc:
            reason = f"Failed to resolve path: {exc}"
            logger.warning(
                "permission.check.denied",
                path=path,
                resolved_path="",
                operation=operation,
                reason=reason,
            )
            return False, reason

        matched_rule = self._find_matching_rule(resolved_path)
        if matched_rule is not None:
            base_path, permissions, rule = matched_rule
            if operation in permissions:
                reason = f"Allowed by whitelist rule '{rule.path}'"
                if log_allowed:
                    logger.info(
                        "permission.check.allowed",
                        path=path,
                        resolved_path=str(resolved_path),
                        operation=operation,
                        reason=reason,
                    )
                return True, reason

            reason = (
                f"Operation '{operation}' not allowed for whitelist rule '{rule.path}'"
            )
            logger.warning(
                "permission.check.denied",
                path=path,
                resolved_path=str(resolved_path),
                operation=operation,
                reason=reason,
                whitelist_path=str(base_path),
            )
            return False, reason

        if self._whitelist.default_policy == "readonly" and operation == "read":
            reason = "Allowed by readonly default policy"
            if log_allowed:
                logger.info(
                    "permission.check.allowed",
                    path=path,
                    resolved_path=str(resolved_path),
                    operation=operation,
                    reason=reason,
                )
            return True, reason

        reason = (
            f"Path '{resolved_path}' is outside whitelist; "
            "readonly default policy denies write/execute"
        )
        logger.warning(
            "permission.check.denied",
            path=path,
            resolved_path=str(resolved_path),
            operation=operation,
            reason=reason,
        )
        return False, reason

    def has_whitelist_match(self, path: str) -> bool:
        try:
            resolved_path = self._resolve_path(path)
        except (OSError, RuntimeError):
            return False
        return self._find_matching_rule(resolved_path) is not None

    def writable_paths(self) -> list[Path]:
        return self.paths_for_operation("write")

    def paths_for_operation(self, operation: Operation) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for resolved_path, permissions, _ in self._resolved_rules:
            if operation in permissions and resolved_path not in seen:
                paths.append(resolved_path)
                seen.add(resolved_path)
        return paths

    def _find_matching_rule(
        self,
        resolved_path: Path,
    ) -> tuple[Path, set[str], WhitelistRule] | None:
        for rule_path, permissions, rule in self._resolved_rules:
            if self._is_within(resolved_path, rule_path):
                return rule_path, permissions, rule
        return None

    def _resolve_path(self, path: str | Path) -> Path:
        return Path(path).expanduser().resolve(strict=False)

    def _is_within(self, resolved_path: Path, rule_path: Path) -> bool:
        try:
            resolved_path.relative_to(rule_path)
            return True
        except ValueError:
            return False

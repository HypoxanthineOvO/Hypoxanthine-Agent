from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def normalize_command(command: str) -> str:
    return " ".join(str(command or "").strip().split())


@dataclass(frozen=True, slots=True)
class ExecProfile:
    name: str
    allow_prefixes: tuple[str, ...]
    deny_prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecDecision:
    allowed: bool
    profile_name: str
    normalized_command: str
    reason: str = ""
    matched_prefix: str = ""


class ExecProfileRegistry:
    def __init__(self, profiles: dict[str, ExecProfile]) -> None:
        self._profiles = dict(profiles)
        if "default" not in self._profiles:
            self._profiles["default"] = self._default_profile()

    @classmethod
    def _default_profile(cls) -> ExecProfile:
        return ExecProfile(
            name="default",
            allow_prefixes=("*",),
            deny_prefixes=("rm -rf /", "shutdown", "reboot", "mkfs", "dd if="),
        )

    @classmethod
    def default(cls) -> "ExecProfileRegistry":
        return cls({"default": cls._default_profile()})

    @classmethod
    def from_file(cls, path: Path | str | None) -> "ExecProfileRegistry":
        return cls.from_yaml(path)

    @classmethod
    def from_yaml(cls, path: Path | str | None) -> "ExecProfileRegistry":
        if path is None:
            return cls.default()

        config_path = Path(path)
        if not config_path.exists():
            return cls.default()

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        profiles_payload = payload.get("profiles", {}) if isinstance(payload, dict) else {}
        profiles: dict[str, ExecProfile] = {}
        if isinstance(profiles_payload, dict):
            for name, raw_profile in profiles_payload.items():
                if not isinstance(raw_profile, dict):
                    continue
                profiles[str(name)] = ExecProfile(
                    name=str(name),
                    allow_prefixes=_normalize_prefixes(raw_profile.get("allow_prefixes")),
                    deny_prefixes=_normalize_prefixes(raw_profile.get("deny_prefixes")),
                )

        return cls(profiles or {"default": cls._default_profile()})

    def get(self, profile_name: str | None) -> ExecProfile:
        selected = str(profile_name or "default").strip() or "default"
        profile = self._profiles.get(selected)
        if profile is None:
            raise KeyError(selected)
        return profile

    def evaluate(self, command: str, *, profile_name: str | None = None) -> ExecDecision:
        normalized_command = normalize_command(command)
        selected_name = str(profile_name or "default").strip() or "default"
        profile = self._profiles.get(selected_name)
        if profile is None:
            profile = self._profiles["default"]

        deny_prefixes = list(profile.deny_prefixes)
        if profile.name != "default":
            deny_prefixes.extend(self._profiles["default"].deny_prefixes)
        for prefix in deny_prefixes:
            if _matches_prefix(normalized_command, prefix):
                return ExecDecision(
                    allowed=False,
                    profile_name=profile.name,
                    normalized_command=normalized_command,
                    reason=f"matched deny prefix '{prefix}'",
                    matched_prefix=prefix,
                )

        if "*" in profile.allow_prefixes:
            return ExecDecision(
                allowed=True,
                profile_name=profile.name,
                normalized_command=normalized_command,
            )

        for prefix in profile.allow_prefixes:
            if _matches_prefix(normalized_command, prefix):
                return ExecDecision(
                    allowed=True,
                    profile_name=profile.name,
                    normalized_command=normalized_command,
                    matched_prefix=prefix,
                )

        return ExecDecision(
            allowed=False,
            profile_name=profile.name,
            normalized_command=normalized_command,
            reason="allowlist miss",
        )


def _normalize_prefixes(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    prefixes: list[str] = []
    for item in raw:
        normalized = normalize_command(str(item))
        if normalized:
            prefixes.append(normalized)
    return tuple(prefixes)


def _matches_prefix(command: str, prefix: str) -> bool:
    normalized_command = normalize_command(command)
    normalized_prefix = normalize_command(prefix)
    if not normalized_command or not normalized_prefix:
        return False
    if normalized_prefix == "*":
        return True
    if normalized_command == normalized_prefix:
        return True
    if not normalized_command.startswith(normalized_prefix):
        return False
    if len(normalized_command) == len(normalized_prefix):
        return True

    next_char = normalized_command[len(normalized_prefix)]
    if normalized_prefix[-1].isalnum():
        return next_char == " "
    return True

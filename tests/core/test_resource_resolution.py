from __future__ import annotations

from pathlib import Path

from hypo_agent.core.resource_resolution import (
    ResourceResolver,
    ResourceResolverContext,
)
from hypo_agent.models import Attachment, Message


def test_resource_resolver_returns_exact_file_for_channel_delivery(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "daily-summary.md"
    report.parent.mkdir()
    report.write_text("summary", encoding="utf-8")
    resolver = ResourceResolver(search_roots=[tmp_path])

    resolution = resolver.resolve("reports/daily-summary.md", purpose="channel_delivery")

    assert resolution.status == "resolved"
    assert resolution.ref is not None
    assert resolution.ref.kind == "file"
    assert resolution.ref.uri == str(report.resolve())
    assert resolution.ref.metadata["purpose"] == "channel_delivery"


def test_resource_resolver_finds_recent_generated_report_by_fuzzy_name(tmp_path: Path) -> None:
    export = tmp_path / "exports" / "20260503_hypo-agent-c1-m1-report.md"
    export.parent.mkdir()
    export.write_text("audit", encoding="utf-8")
    resolver = ResourceResolver(
        search_roots=[tmp_path],
        context=ResourceResolverContext(
            generated_files=[export],
        ),
    )

    resolution = resolver.resolve("m1 report", purpose="channel_delivery")

    assert resolution.status == "resolved"
    assert resolution.ref is not None
    assert resolution.ref.uri == str(export.resolve())
    assert resolution.candidates[0].source == "recent_generated"


def test_resource_resolver_returns_confirmation_for_ambiguous_attachment(tmp_path: Path) -> None:
    first = tmp_path / "uploads" / "first" / "report.pdf"
    second = tmp_path / "uploads" / "second" / "report.pdf"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    resolver = ResourceResolver(
        context=ResourceResolverContext(
            recent_messages=[
                Message(
                    text="file one",
                    sender="user",
                    session_id="main",
                    attachments=[
                        Attachment(type="file", url=str(first), filename="report.pdf"),
                        Attachment(type="file", url=str(second), filename="report.pdf"),
                    ],
                )
            ],
        )
    )

    resolution = resolver.resolve("report.pdf", purpose="channel_delivery")

    assert resolution.status == "ambiguous"
    assert resolution.recovery_action is not None
    assert resolution.recovery_action.type == "ask_user"
    assert resolution.recovery_action.reason == "multiple_candidates"
    assert [candidate.ref.uri for candidate in resolution.candidates] == [
        str(first.resolve()),
        str(second.resolve()),
    ]


def test_resource_resolver_returns_recovery_action_for_missing_resource(tmp_path: Path) -> None:
    resolver = ResourceResolver(search_roots=[tmp_path])

    resolution = resolver.resolve("missing-report.pdf", purpose="channel_delivery")

    assert resolution.status == "not_found"
    assert resolution.ref is None
    assert resolution.recovery_action is not None
    assert resolution.recovery_action.type == "search_or_ask"
    assert resolution.recovery_action.reason == "no_candidates"


def test_resource_resolver_models_urls_as_web_resources() -> None:
    resolver = ResourceResolver()

    resolution = resolver.resolve("https://www.zhihu.com/question/123", purpose="web_read")

    assert resolution.status == "resolved"
    assert resolution.ref is not None
    assert resolution.ref.kind == "url"
    assert resolution.ref.uri == "https://www.zhihu.com/question/123"
    assert resolution.ref.display_name == "www.zhihu.com/question/123"

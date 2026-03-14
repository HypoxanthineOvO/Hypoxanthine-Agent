from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Message


def test_session_memory_appends_and_restores_jsonl(tmp_path) -> None:
    sessions_dir = tmp_path / "sessions"
    store = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    store.append(
        Message(
            text="你好",
            sender="user",
            session_id="main",
            timestamp=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        )
    )
    store.append(
        Message(
            text="在的",
            sender="assistant",
            session_id="main",
            timestamp=datetime(2026, 3, 3, 10, 1, tzinfo=UTC),
        )
    )

    restored = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    messages = restored.get_messages("main")

    assert [m.sender for m in messages] == ["user", "assistant"]
    assert messages[0].text == "你好"
    assert messages[1].text == "在的"
    assert messages[0].timestamp == datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
    assert messages[1].timestamp == datetime(2026, 3, 3, 10, 1, tzinfo=UTC)


def test_session_memory_keeps_only_recent_n_in_buffer(tmp_path) -> None:
    store = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=3)
    for i in range(6):
        store.append(Message(text=f"m{i}", sender="user", session_id="s1"))

    recent = store.get_recent_messages("s1")
    assert [m.text for m in recent] == ["m3", "m4", "m5"]


def test_session_memory_get_messages_reads_full_history(tmp_path) -> None:
    store = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=2)
    for i in range(5):
        store.append(Message(text=f"m{i}", sender="user", session_id="s1"))

    full_history = store.get_messages("s1")
    assert [m.text for m in full_history] == ["m0", "m1", "m2", "m3", "m4"]


def test_session_memory_lists_sessions_by_updated_at(tmp_path) -> None:
    store = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    store.append(
        Message(
            text="a",
            sender="user",
            session_id="s1",
            timestamp=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        )
    )
    store.append(
        Message(
            text="b",
            sender="user",
            session_id="s2",
            timestamp=datetime(2026, 3, 3, 10, 1, tzinfo=UTC),
        )
    )

    sessions = store.list_sessions()

    assert sessions[0]["session_id"] == "s2"
    assert sessions[1]["session_id"] == "s1"


def test_session_memory_new_instance_reads_existing_files_for_listing_and_history(
    tmp_path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    writer = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    writer.append(Message(text="u1", sender="user", session_id="persisted"))
    writer.append(Message(text="a1", sender="assistant", session_id="persisted"))

    restored = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    listed = restored.list_sessions()
    history = restored.get_messages("persisted")

    assert listed[0]["session_id"] == "persisted"
    assert listed[0]["message_count"] == 2
    assert [m.text for m in history] == ["u1", "a1"]


def test_session_memory_clear_session_removes_buffer_and_jsonl(tmp_path) -> None:
    sessions_dir = tmp_path / "sessions"
    store = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    store.append(Message(text="u1", sender="user", session_id="to-clear"))
    store.append(Message(text="a1", sender="assistant", session_id="to-clear"))

    assert len(store.get_recent_messages("to-clear")) == 2
    assert len(store.get_messages("to-clear")) == 2

    store.clear_session("to-clear")

    assert store.get_recent_messages("to-clear") == []
    assert store.get_messages("to-clear") == []
    assert store.list_sessions() == []


def test_session_memory_migrates_legacy_session_file_to_main(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    legacy_message = Message(
        text="legacy",
        sender="user",
        session_id="session-1",
        timestamp=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
    )
    legacy_file = sessions_dir / "session-1.jsonl"
    legacy_file.write_text(f"{legacy_message.model_dump_json()}\n", encoding="utf-8")

    store = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)

    assert legacy_file.exists()
    main_messages = store.get_messages("main")
    assert (sessions_dir / "main.jsonl").exists()
    assert [message.text for message in main_messages] == ["legacy"]
    assert [message.session_id for message in main_messages] == ["main"]


def test_session_memory_writes_timestamp_to_jsonl(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    store = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    store.append(
        Message(
            text="hello",
            sender="user",
            session_id="main",
            timestamp=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        )
    )

    raw = (sessions_dir / "main.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(raw)

    assert payload["timestamp"] == "2026-03-03T10:00:00Z"


def test_session_memory_keeps_legacy_messages_without_timestamp_empty(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "main.jsonl").write_text(
        '{"text":"legacy","sender":"user","session_id":"main","channel":"webui","metadata":{}}\n',
        encoding="utf-8",
    )

    store = SessionMemory(sessions_dir=sessions_dir, buffer_limit=20)
    messages = store.get_messages("main")

    assert len(messages) == 1
    assert messages[0].text == "legacy"
    assert messages[0].timestamp is None

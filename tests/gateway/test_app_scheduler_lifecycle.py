from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.app import (
    AppDeps,
    _derive_heartbeat_service_timeout_seconds,
    create_app,
)
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class RecordingScheduler:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.interval_jobs: list[tuple[str, int]] = []
        self.cron_jobs: list[tuple[str, str]] = []
        self.is_running = False

    async def start(self) -> None:
        self.started += 1
        self.is_running = True

    async def stop(self) -> None:
        self.stopped += 1
        self.is_running = False

    def register_interval_job(self, job_id: str, minutes: int, coro, *, replace_existing: bool = True):
        del coro, replace_existing
        self.interval_jobs.append((job_id, minutes))

    def register_cron_job(self, job_id: str, cron: str, coro, *, replace_existing: bool = True):
        del coro, replace_existing
        self.cron_jobs.append((job_id, cron))


class RecordingPipeline:
    def __init__(self) -> None:
        self.consumer_started = 0
        self.consumer_stopped = 0

    async def start_event_consumer(self) -> None:
        self.consumer_started += 1

    async def stop_event_consumer(self) -> None:
        self.consumer_stopped += 1

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class OrderedPipeline:
    def __init__(self, scheduler: RecordingScheduler) -> None:
        self.scheduler = scheduler
        self.consumer_started = 0
        self.consumer_stopped = 0
        self.on_proactive_message = None

    async def start_event_consumer(self) -> None:
        self.consumer_started += 1
        assert self.scheduler.started == 1
        assert self.on_proactive_message is not None

    async def stop_event_consumer(self) -> None:
        self.consumer_stopped += 1

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class DummyEventQueue:
    async def put(self, event):  # pragma: no cover - interface placeholder
        del event

    async def get(self):  # pragma: no cover - interface placeholder
        return {}

    def task_done(self):  # pragma: no cover - interface placeholder
        return None

    def empty(self):  # pragma: no cover - interface placeholder
        return True

    def qsize(self):  # pragma: no cover - interface placeholder
        return 0


class NoopRouter:
    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return "ok"

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        del model_name, messages, tools, session_id
        return {"text": "ok", "tool_calls": []}

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        yield "ok"


class RecordingImageRenderer:
    def __init__(self) -> None:
        self.initialized = 0
        self.stopped = 0
        self.available = True

    async def initialize(self) -> None:
        self.initialized += 1

    async def shutdown(self) -> None:
        self.stopped += 1


def test_app_lifespan_starts_and_stops_scheduler_and_pipeline_consumer(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    with TestClient(app):
        assert scheduler.started == 1
        assert pipeline.consumer_started == 1

    assert scheduler.stopped == 1
    assert pipeline.consumer_stopped == 1


def test_app_lifespan_starts_consumer_after_scheduler_and_callback_wiring(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = OrderedPipeline(scheduler)
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    with TestClient(app):
        assert scheduler.started == 1
        assert pipeline.consumer_started == 1

    assert pipeline.consumer_stopped == 1
    assert scheduler.stopped == 1


def test_app_lifespan_initializes_and_stops_image_renderer(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()
    renderer = RecordingImageRenderer()
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        image_renderer=renderer,
    )
    app = create_app(
        auth_token="test-token",
        pipeline=pipeline,
        deps=deps,
    )

    with TestClient(app):
        assert renderer.initialized == 1
        assert app.state.image_renderer is renderer

    assert renderer.stopped == 1


def test_event_consumer_starts_on_lifespan(tmp_path) -> None:
    queue = EventQueue()
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=session_memory,
        event_queue=queue,
    )
    deps = AppDeps(
        session_memory=session_memory,
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=RecordingScheduler(),
        event_queue=queue,
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)

    with TestClient(app):
        task = app.state.pipeline._event_consumer_task
        assert task is not None
        assert task.done() is False


def test_app_registers_heartbeat_job_from_tasks_config(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: true
  interval_minutes: 1
""".strip(),
        encoding="utf-8",
    )

    class DummyHeartbeatService:
        def __init__(self) -> None:
            self.run_calls = 0

        async def run(self) -> None:
            self.run_calls += 1

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        heartbeat_service=DummyHeartbeatService(),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert scheduler.interval_jobs == [("heartbeat", 1)]


def test_app_registers_heartbeat_cron_job_from_tasks_config(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: true
  mode: cron
  cron: "*/10 * * * *"
""".strip(),
        encoding="utf-8",
    )

    class DummyHeartbeatService:
        async def run(self) -> None:
            return None

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        heartbeat_service=DummyHeartbeatService(),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert scheduler.interval_jobs == []
        assert scheduler.cron_jobs == [("heartbeat", "*/10 * * * *")]


def test_app_registers_wewe_rss_job_from_tasks_config(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: false
wewe_rss:
  enabled: true
  interval_minutes: 7
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  wewe_rss:
    enabled: true
    base_url: "http://10.15.88.94:4000"
    auth_code: "test-auth-code"
    login_timeout_seconds: 180
    poll_interval_seconds: 3
""".strip(),
        encoding="utf-8",
    )

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert ("wewe_rss", 7) in scheduler.interval_jobs
        assert getattr(app.state, "wewe_rss_monitor", None) is not None


def test_app_registers_hypo_info_digest_jobs_from_tasks_config(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: false
hypo_info_digest:
  enabled: true
  interval_minutes: 480
  time: "09:00,21:00"
""".strip(),
        encoding="utf-8",
    )

    class DummyInfoReachSkill:
        async def run_scheduled_summary(self) -> dict[str, int]:
            return {"pushed": 0}

    class DummySkillManager:
        def __init__(self) -> None:
            self._skills = {"info_reach": DummyInfoReachSkill()}

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        skill_manager=DummySkillManager(),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert scheduler.interval_jobs == []
        assert scheduler.cron_jobs == [
            ("hypo_info_digest_0900", "0 9 * * *"),
            ("hypo_info_digest_2100", "0 21 * * *"),
        ]


def test_app_points_heartbeat_service_to_config_prompt_file(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: true
  interval_minutes: 1
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "heartbeat_prompt.md").write_text("# prompt", encoding="utf-8")

    class DummyHeartbeatService:
        def __init__(self) -> None:
            self.prompt_path = Path("config/heartbeat_prompt.md")

        async def run(self) -> None:
            return None

    heartbeat_service = DummyHeartbeatService()
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        heartbeat_service=heartbeat_service,
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert heartbeat_service.prompt_path == config_dir / "heartbeat_prompt.md"


def test_app_registers_hypo_info_digest_jobs_from_fixed_times(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: false
hypo_info_digest:
  enabled: true
  interval_minutes: 480
  time: "09:00,21:00"
""".strip(),
        encoding="utf-8",
    )

    class DummyInfoReachSkill:
        async def run_scheduled_summary(self) -> None:
            return None

    class DummySkillManager:
        def __init__(self) -> None:
            self._skills = {"info_reach": DummyInfoReachSkill()}

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        skill_manager=DummySkillManager(),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert ("hypo_info_digest_0900", "0 9 * * *") in scheduler.cron_jobs
        assert ("hypo_info_digest_2100", "0 21 * * *") in scheduler.cron_jobs


def test_app_ignores_legacy_email_scan_interval_config(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: false
  interval_minutes: 1
email_scan:
  enabled: true
  interval_minutes: 1
""".strip(),
        encoding="utf-8",
    )

    class DummyEmailScanner:
        async def scheduled_scan(self) -> None:
            return None

    class DummySkillManager:
        def __init__(self) -> None:
            self._skills = {"email_scanner": DummyEmailScanner()}

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        skill_manager=DummySkillManager(),
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert scheduler.interval_jobs == []


def test_app_registers_memory_gc_cron_job(tmp_path) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()

    class DummyMemoryGC:
        async def run(self) -> dict:
            return {"processed_count": 0, "skipped_count": 0}

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
    )
    deps.memory_gc = DummyMemoryGC()
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)

    with TestClient(app):
        assert ("memory_gc", "0 4 * * *") in scheduler.cron_jobs


def test_app_lifespan_starts_and_stops_napcat_websocket_client(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str] | str] = []

    class FakeNapCatWebSocketClient:
        def __init__(
            self,
            *,
            url,
            bot_qq="",
            token,
            service_getter,
            pipeline_getter,
            reconnect_delay_seconds=5.0,
            connect_timeout_seconds=5.0,
        ) -> None:
            del service_getter, pipeline_getter, reconnect_delay_seconds, connect_timeout_seconds
            calls.append(("init", url))
            calls.append(("bot_qq", bot_qq))
            calls.append(("token", token))

        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr("hypo_agent.gateway.app.NapCatWebSocketClient", FakeNapCatWebSocketClient)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  qq:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq:
    napcat_ws_url: ws://127.0.0.1:3009/onebot/v11/ws
    napcat_ws_token: ws-token-123
    napcat_http_url: http://127.0.0.1:3008
    bot_qq: "123456789"
    allowed_users:
      - "10001"
""".strip(),
        encoding="utf-8",
    )

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=RecordingScheduler(),
        event_queue=DummyEventQueue(),
    )
    app = create_app(auth_token="test-token", pipeline=RecordingPipeline(), deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert ("init", "ws://127.0.0.1:3009/onebot/v11/ws") in calls
        assert ("bot_qq", "123456789") in calls
        assert ("token", "ws-token-123") in calls
        assert "start" in calls

    assert "stop" in calls


def test_app_lifespan_prefers_qqbot_websocket_client_when_secrets_enable_bot(
    tmp_path,
    monkeypatch,
) -> None:
    qqbot_calls: list[str] = []
    napcat_calls: list[str] = []

    class FakeQQBotWebSocketClient:
        def __init__(self, *, service_getter, pipeline_getter, reconnect_delay_seconds=5.0, connect_timeout_seconds=10.0, intents=None) -> None:
            del service_getter, pipeline_getter, reconnect_delay_seconds, connect_timeout_seconds, intents
            qqbot_calls.append("init")

        async def start(self) -> None:
            qqbot_calls.append("start")

        async def stop(self) -> None:
            qqbot_calls.append("stop")

    class FakeNapCatWebSocketClient:
        def __init__(self, **kwargs) -> None:
            del kwargs
            napcat_calls.append("init")

        async def start(self) -> None:
            napcat_calls.append("start")

        async def stop(self) -> None:
            napcat_calls.append("stop")

    monkeypatch.setattr("hypo_agent.gateway.app.QQBotWebSocketClient", FakeQQBotWebSocketClient)
    monkeypatch.setattr("hypo_agent.gateway.app.NapCatWebSocketClient", FakeNapCatWebSocketClient)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  qq:
    enabled: true
  qq_bot:
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  qq_bot:
    app_id: "1029384756"
    app_secret: "bot-secret-xyz"
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=RecordingScheduler(),
        event_queue=DummyEventQueue(),
    )
    app = create_app(auth_token="test-token", pipeline=RecordingPipeline(), deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert qqbot_calls == ["init", "start"]
        assert napcat_calls == []

    assert qqbot_calls == ["init", "start", "stop"]


def test_app_lifespan_schedules_directory_index_refresh_in_background(
    tmp_path,
    monkeypatch,
) -> None:
    started = threading.Event()

    async def fake_refresh_directory_index(**kwargs) -> bool:
        del kwargs
        started.set()
        await asyncio.sleep(0.5)
        return True

    monkeypatch.setattr(
        "hypo_agent.gateway.app.refresh_directory_index",
        fake_refresh_directory_index,
    )

    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=RecordingScheduler(),
        event_queue=DummyEventQueue(),
    )
    app = create_app(auth_token="test-token", pipeline=RecordingPipeline(), deps=deps)

    with TestClient(app):
        assert started.wait(timeout=1.0) is True
        task = app.state.directory_index_task
        assert task is not None
        assert task.done() is False


def test_app_lifespan_warms_email_cache_in_background_when_store_is_stale(
    tmp_path,
) -> None:
    scheduler = RecordingScheduler()
    pipeline = RecordingPipeline()
    started = threading.Event()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: false
  interval_minutes: 1
email_store:
  enabled: true
  max_entries: 5000
  retention_days: 90
  warmup_hours: 168
""".strip(),
        encoding="utf-8",
    )

    class DummyEmailStore:
        def needs_warmup(self, *, max_age_hours: int = 24) -> bool:
            assert max_age_hours == 24
            return True

    class DummyEmailScanner:
        def __init__(self) -> None:
            self.email_store = DummyEmailStore()
            self.scan_params: list[dict] = []

        def configure_email_store(self, *, max_entries: int, retention_days: int) -> None:
            assert max_entries == 5000
            assert retention_days == 90

        async def scan_emails(self, *, params=None) -> dict:
            self.scan_params.append(dict(params or {}))
            started.set()
            await asyncio.sleep(0.5)
            return {"new_emails": 0, "items": []}

    class DummySkillManager:
        def __init__(self) -> None:
            self._skills = {"email_scanner": DummyEmailScanner()}

    skill_manager = DummySkillManager()
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        scheduler=scheduler,
        event_queue=DummyEventQueue(),
        skill_manager=skill_manager,
    )
    app = create_app(auth_token="test-token", pipeline=pipeline, deps=deps)
    app.state.config_dir = config_dir

    with TestClient(app):
        assert started.wait(timeout=1.0) is True
        task = app.state.email_cache_warmup_task
        assert task is not None
        assert task.done() is False
        assert skill_manager._skills["email_scanner"].scan_params == [
            {"hours_back": 168, "triggered_by": "cache_warmup"}
        ]


def test_heartbeat_service_timeout_budget_scales_with_rounds_and_per_round_timeout() -> None:
    class PipelineStub:
        heartbeat_max_react_rounds = 4
        heartbeat_model_timeout_seconds = 25

    assert _derive_heartbeat_service_timeout_seconds(PipelineStub()) == 235

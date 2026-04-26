from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import hypo_agent.channels.codex_bridge as codex_bridge_module
from hypo_agent.channels.codex_bridge import CodexBridge, CodexThread


class FakeItem:
    def __init__(self, text: str) -> None:
        self.text = text

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        del mode
        return {"type": "agentMessage", "text": self.text}


@dataclass
class FakeCompletedTurn:
    id: str
    status: object
    error: object | None = None


class BlockingTurnHandle:
    def __init__(self, turn_id: str, *, final_status: str = "completed", text: str = "done") -> None:
        self.id = turn_id
        self.final_status = final_status
        self.text = text
        self.interrupted = False
        self._release = asyncio.Event()

    async def run(self) -> FakeCompletedTurn:
        await self._release.wait()
        if self.interrupted:
            return FakeCompletedTurn(
                id=self.id,
                status=SimpleNamespace(value="aborted"),
                error=SimpleNamespace(message="aborted"),
            )
        if self.final_status == "failed":
            return FakeCompletedTurn(
                id=self.id,
                status=SimpleNamespace(value="failed"),
                error=SimpleNamespace(message=self.text),
            )
        if self.final_status == "interrupted":
            return FakeCompletedTurn(
                id=self.id,
                status=SimpleNamespace(value="interrupted"),
                error=None,
            )
        return FakeCompletedTurn(id=self.id, status=SimpleNamespace(value="completed"))

    async def stream(self):
        await self._release.wait()
        if self.interrupted or self.final_status == "interrupted":
            yield SimpleNamespace(
                method="thread/status/changed",
                payload=SimpleNamespace(status=SimpleNamespace(root=SimpleNamespace(type="idle"))),
            )
            return
        yield SimpleNamespace(
            method="item/agentMessage/delta",
            payload=SimpleNamespace(delta=self.text, item_id="item-2", thread_id="thread", turn_id=self.id),
        )
        completed_status = "failed" if self.final_status == "failed" else "completed"
        error = SimpleNamespace(message=self.text) if completed_status == "failed" else None
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(
                turn=FakeCompletedTurn(
                    id=self.id,
                    status=SimpleNamespace(value=completed_status),
                    error=error,
                )
            ),
        )

    async def interrupt(self) -> None:
        self.interrupted = True
        self._release.set()

    def release(self) -> None:
        self._release.set()


class FakeThread:
    def __init__(self, thread_id: str, *, text: str = "done", final_status: str = "completed") -> None:
        self.id = thread_id
        self._text = text
        self._final_status = final_status
        self.turn_handle = BlockingTurnHandle("turn-1", final_status=final_status, text=text)
        self.turn_calls: list[dict[str, object]] = []

    async def turn(self, _input, **kwargs):
        self.turn_calls.append(dict(kwargs))
        return self.turn_handle

    async def read(self, *, include_turns: bool = False):
        del include_turns
        effective_status = "aborted" if self.turn_handle.interrupted else self._final_status
        turn = SimpleNamespace(
            id=self.turn_handle.id,
            status=SimpleNamespace(value=effective_status),
            items=[FakeItem(self._text)],
            error=(SimpleNamespace(message=self._text) if effective_status == "failed" else None),
        )
        return SimpleNamespace(thread=SimpleNamespace(turns=[turn], status=SimpleNamespace(value=effective_status)))


class FakeAsyncCodex:
    def __init__(self, *, start_thread: FakeThread | None = None, raise_on_start: Exception | None = None) -> None:
        self.start_thread = start_thread or FakeThread("thread-1")
        self.raise_on_start = raise_on_start
        self.started = False
        self.closed = False
        self.resume_calls: list[str] = []
        self.thread_resume_calls: list[dict[str, object]] = []
        self.thread_start_calls: list[dict[str, object]] = []

    async def __aenter__(self):
        self.started = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        self.closed = True

    async def close(self) -> None:
        self.closed = True

    async def thread_start(self, **kwargs):
        self.thread_start_calls.append(dict(kwargs))
        if self.raise_on_start is not None:
            raise self.raise_on_start
        return self.start_thread

    async def thread_resume(self, thread_id: str, **kwargs):
        self.thread_resume_calls.append(dict(kwargs))
        self.resume_calls.append(thread_id)
        return self.start_thread


def test_codex_bridge_submit_completes_and_calls_callback() -> None:
    async def _run() -> None:
        fake_codex = FakeAsyncCodex(start_thread=FakeThread("thread-1", text="fixed"))
        completed: list[tuple[str, str, str | None]] = []
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
        )
        await bridge.start()

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            completed.append((run_id, status, result))

        thread = await bridge.submit(
            run_id="repair-1",
            prompt="fix it",
            working_dir="/repo",
            on_complete=on_complete,
        )
        fake_codex.start_thread.turn_handle.release()
        await asyncio.wait_for(thread.task, timeout=2)

        assert thread.status == "completed"
        assert thread.result == "fixed"
        assert bridge.get_status("repair-1") is thread
        assert completed == [("repair-1", "completed", "fixed")]

        await bridge.stop()
        assert fake_codex.closed is True

    asyncio.run(_run())


def test_codex_bridge_start_passes_isolated_home_env_and_config(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CapturingAsyncCodex(FakeAsyncCodex):
        def __init__(self, *, config):
            captured["config"] = config
            super().__init__()

    monkeypatch.setattr(codex_bridge_module, "AsyncCodex", CapturingAsyncCodex)

    async def _run() -> None:
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_bin="/home/heyx/.volta/bin/codex",
            codex_home=str(tmp_path / "codex-home"),
            app_server_cwd=str(tmp_path / "app-server-cwd"),
            config_overrides={"history.persistence": "none"},
        )

        assert bridge.isolation_mode == "dedicated_codex_home"
        assert await bridge.start() is True

        config = captured["config"]
        assert config.codex_bin == "/home/heyx/.volta/bin/codex"
        assert config.cwd == str(tmp_path / "app-server-cwd")
        assert config.env["CODEX_HOME"] == str(tmp_path / "codex-home")
        assert "history.persistence=none" in config.config_overrides

        await bridge.stop()

    asyncio.run(_run())


def test_codex_bridge_submit_failure_reports_failed_without_stuck_state() -> None:
    async def _run() -> None:
        fake_codex = FakeAsyncCodex(raise_on_start=RuntimeError("spawn codex ENOENT"))
        completed: list[tuple[str, str, str | None]] = []
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
        )
        await bridge.start()

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            completed.append((run_id, status, result))

        thread = await bridge.submit(
            run_id="repair-1",
            prompt="fix it",
            working_dir="/repo",
            on_complete=on_complete,
        )

        assert thread.status == "failed"
        assert "ENOENT" in str(thread.result)
        assert completed == [("repair-1", "failed", "spawn codex ENOENT")]

    asyncio.run(_run())


def test_codex_bridge_continue_thread_uses_thread_resume() -> None:
    async def _run() -> None:
        fake_codex = FakeAsyncCodex(start_thread=FakeThread("thread-42", text="continued"))
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
        )
        await bridge.start()
        completed: list[tuple[str, str, str | None]] = []

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            completed.append((run_id, status, result))

        thread = await bridge.continue_thread(
            run_id="repair-2",
            thread_id="thread-42",
            prompt="continue",
            working_dir="/repo",
            on_complete=on_complete,
        )
        fake_codex.start_thread.turn_handle.release()
        await asyncio.wait_for(thread.task, timeout=2)

        assert fake_codex.resume_calls == ["thread-42"]
        assert completed == [("repair-2", "completed", "continued")]

    asyncio.run(_run())


def test_codex_bridge_submit_propagates_full_access_execution_policy() -> None:
    async def _run() -> None:
        fake_thread = FakeThread("thread-1", text="ok")
        fake_codex = FakeAsyncCodex(start_thread=fake_thread)
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
        )
        await bridge.start()

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            del run_id, status, result

        thread = await bridge.submit(
            run_id="repair-1",
            prompt="fix it",
            working_dir="/repo",
            on_complete=on_complete,
        )
        fake_thread.turn_handle.release()
        await asyncio.wait_for(thread.task, timeout=2)

        assert fake_codex.thread_start_calls
        assert str(fake_codex.thread_start_calls[0]["approval_policy"]) in {"never", "AskForApprovalValue.never"}
        assert str(fake_codex.thread_start_calls[0]["sandbox"]) in {
            "danger-full-access",
            "SandboxMode.danger_full_access",
        }
        assert fake_thread.turn_calls
        assert str(fake_thread.turn_calls[0]["approval_policy"]) in {"never", "AskForApprovalValue.never"}
        assert fake_thread.turn_calls[0]["sandbox_policy"] == {"type": "dangerFullAccess"}

    asyncio.run(_run())


def test_codex_bridge_submit_propagates_auto_review_execution_policy() -> None:
    async def _run() -> None:
        fake_thread = FakeThread("thread-1", text="ok")
        fake_codex = FakeAsyncCodex(start_thread=fake_thread)
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
            approval_policy="on-request",
            approvals_reviewer="guardian_subagent",
            sandbox_mode="workspace-write",
        )
        await bridge.start()

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            del run_id, status, result

        thread = await bridge.submit(
            run_id="repair-1",
            prompt="fix it",
            working_dir="/repo",
            on_complete=on_complete,
        )
        fake_thread.turn_handle.release()
        await asyncio.wait_for(thread.task, timeout=2)

        assert fake_codex.thread_start_calls
        start_call = fake_codex.thread_start_calls[0]
        assert str(start_call["approval_policy"]) in {
            "on-request",
            "AskForApprovalValue.on_request",
            "root=<AskForApprovalValue.on_request: 'on-request'>",
        }
        assert str(start_call["approvals_reviewer"]) in {
            "guardian_subagent",
            "ApprovalsReviewer.guardian_subagent",
        }
        assert str(start_call["sandbox"]) in {
            "workspace-write",
            "SandboxMode.workspace_write",
        }
        assert fake_thread.turn_calls
        turn_call = fake_thread.turn_calls[0]
        assert str(turn_call["approval_policy"]) in {
            "on-request",
            "AskForApprovalValue.on_request",
            "root=<AskForApprovalValue.on_request: 'on-request'>",
        }
        assert str(turn_call["approvals_reviewer"]) in {
            "guardian_subagent",
            "ApprovalsReviewer.guardian_subagent",
        }
        assert turn_call["sandbox_policy"] == {
            "type": "workspaceWrite",
            "writableRoots": ["/repo"],
            "readOnlyAccess": {"type": "fullAccess"},
            "networkAccess": False,
            "excludeTmpdirEnvVar": False,
            "excludeSlashTmp": False,
        }

    asyncio.run(_run())


def test_codex_bridge_abort_interrupts_active_turn() -> None:
    async def _run() -> None:
        fake_codex = FakeAsyncCodex(start_thread=FakeThread("thread-1", text="aborted"))
        completed: list[tuple[str, str, str | None]] = []
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
        )
        await bridge.start()

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            completed.append((run_id, status, result))

        thread = await bridge.submit(
            run_id="repair-1",
            prompt="fix it",
            working_dir="/repo",
            on_complete=on_complete,
        )
        await asyncio.sleep(0)
        await bridge.abort("repair-1")
        await asyncio.wait_for(thread.task, timeout=2)

        assert fake_codex.start_thread.turn_handle.interrupted is True
        assert thread.status == "aborted"
        assert completed == [("repair-1", "aborted", "aborted")]

    asyncio.run(_run())


def test_codex_bridge_interrupted_turn_reports_failed_terminal_state() -> None:
    async def _run() -> None:
        fake_codex = FakeAsyncCodex(start_thread=FakeThread("thread-1", text="interrupted", final_status="interrupted"))
        completed: list[tuple[str, str, str | None]] = []
        bridge = CodexBridge(
            model="gpt-5.4",
            codex_factory=lambda: fake_codex,
            codex_bin="/home/heyx/.volta/bin/codex",
        )
        await bridge.start()

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            completed.append((run_id, status, result))

        thread = await bridge.submit(
            run_id="repair-2",
            prompt="fix it",
            working_dir="/repo",
            on_complete=on_complete,
        )
        fake_codex.start_thread.turn_handle.release()
        await asyncio.wait_for(thread.task, timeout=2)

        assert thread.status == "failed"
        assert completed == [("repair-2", "failed", "interrupted")]

    asyncio.run(_run())

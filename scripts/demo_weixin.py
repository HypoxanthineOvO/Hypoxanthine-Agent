#!/usr/bin/env python3
"""Standalone Weixin iLink demo for MW.0 protocol validation."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

import qrcode
import structlog

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hypo_agent.channels.weixin import ILinkClient, SessionExpiredError
from hypo_agent.core.logging import configure_logging

logger = structlog.get_logger("hypo_agent.scripts.demo_weixin")

_EXPERIMENT_DELAY_SECONDS = 300
_GROUP_FIELDS = ("group_id", "chat_type", "ChatType", "session_type", "room_id")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tencent Weixin iLink echo demo")
    parser.add_argument(
        "--base-url",
        default="https://ilinkai.weixin.qq.com",
        help="iLink API base URL",
    )
    parser.add_argument(
        "--token-path",
        default="memory/weixin_auth.json",
        help="Persistent auth state path",
    )
    parser.add_argument(
        "--experiment",
        action="store_true",
        help="Enable MW.0 experiment mode",
    )
    return parser


def _print_qrcode(qrcode_content: str) -> None:
    print("\n请使用微信扫码登录：")
    qr = qrcode.QRCode(border=1)
    qr.add_data(qrcode_content)
    qr.print_ascii(tty=True)
    print(f"二维码内容: {qrcode_content}\n")


def _extract_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in message.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        item_type = int(item.get("type") or 0)
        if item_type == 1:
            text_item = item.get("text_item")
            if isinstance(text_item, dict):
                text = str(text_item.get("text") or "").strip()
                if text:
                    parts.append(text)
        elif item_type == 3:
            voice_item = item.get("voice_item")
            if isinstance(voice_item, dict):
                text = str(voice_item.get("text") or "").strip()
                if text:
                    parts.append(text)
        elif item_type == 4:
            file_item = item.get("file_item")
            if isinstance(file_item, dict):
                file_name = str(file_item.get("file_name") or "").strip()
                if file_name:
                    parts.append(f"[文件] {file_name}")
    return "\n".join(parts).strip()


def _is_success_response(response: dict[str, Any]) -> bool:
    ret = response.get("ret")
    errcode = response.get("errcode")
    return (ret in (None, 0)) and (errcode in (None, 0))


def _pretty_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _report_experiment(index: int, description: str, success: bool, response: dict[str, Any]) -> None:
    result = "成功" if success else "失败"
    print(f"[实验{index}] {description}: {result}")
    print(f"[实验{index}] 响应: {_pretty_json(response)}")
    logger.info(
        "weixin.experiment.result",
        experiment=index,
        description=description,
        success=success,
        response=response,
    )


class DemoApp:
    def __init__(self, client: ILinkClient, *, experiment: bool) -> None:
        self.client = client
        self.experiment = experiment
        self.stop_event = asyncio.Event()
        self.latest_context_tokens: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._experiment1_users: set[str] = set()
        self._experiment2_users: set[str] = set()
        self._run_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        self._run_task = asyncio.current_task()
        self._install_signal_handlers()
        try:
            await self._ensure_login()
            while not self.stop_event.is_set():
                try:
                    messages = await self.client.get_updates()
                except SessionExpiredError:
                    print("检测到会话超时，正在重新登录。")
                    logger.warning("weixin.demo.session_expired")
                    await self._ensure_login(force=True)
                    continue
                except asyncio.CancelledError:
                    if self.stop_event.is_set():
                        logger.info("weixin.demo.cancelled")
                        return
                    raise
                except Exception as exc:
                    logger.exception("weixin.demo.poll_failed", error=str(exc))
                    await asyncio.sleep(2.0)
                    continue

                for message in messages:
                    await self._handle_message(message)
        finally:
            self._run_task = None

    async def shutdown(self) -> None:
        self.stop_event.set()
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        await self.client.close()

    async def _ensure_login(self, *, force: bool = False) -> None:
        if self.client.bot_token and not force:
            print("已加载持久化 bot_token，跳过登录。")
            logger.info("weixin.demo.reuse_token", token_path=str(self.client.token_path))
            return
        session = await self.client.login(on_qrcode_content=_print_qrcode)
        print(f"登录成功！Bot ID: {session['bot_id']}")
        print(f"会话信息已持久化到 {self.client.token_path}")
        logger.info(
            "weixin.demo.login_succeeded",
            bot_token_prefix=session["token"][:8],
            bot_id=session["bot_id"],
            user_id=session["user_id"],
            baseurl=session["baseurl"],
        )

    async def _handle_message(self, message: dict[str, Any]) -> None:
        from_user_id = str(message.get("from_user_id") or "").strip()
        context_token = str(message.get("context_token") or "").strip()
        text = _extract_text(message)

        if self.experiment:
            self._run_experiment3(message)

        if from_user_id and context_token:
            self.latest_context_tokens[from_user_id] = context_token
            if self.experiment and from_user_id not in self._experiment1_users:
                self._experiment1_users.add(from_user_id)
                print("[实验1] 已缓存 token，将在 5 分钟后尝试主动发送...")
                self._schedule_task(self._run_experiment1(from_user_id))

        if self.experiment and from_user_id and from_user_id not in self._experiment2_users:
            self._experiment2_users.add(from_user_id)
            self._schedule_task(self._run_experiment2(from_user_id))

        if not from_user_id:
            logger.warning("weixin.demo.skip_message", reason="missing_from_user_id", message=message)
            return

        if not text:
            logger.info(
                "weixin.demo.skip_message",
                reason="non_text_message",
                from_user_id=from_user_id,
                message_id=message.get("message_id"),
            )
            return

        await self.client.send_message(from_user_id, f"[echo] {text}", context_token=context_token)
        logger.info(
            "weixin.demo.echo",
            from_user_id=from_user_id,
            context_token=context_token,
            text=text,
        )
        print(f"[收到] {from_user_id}: {text}")
        print(f"[发送] {from_user_id}: [echo] {text}")

    def _run_experiment3(self, message: dict[str, Any]) -> None:
        print("[实验3] 完整消息结构:")
        print(_pretty_json(message))
        interesting = {field: message.get(field) for field in _GROUP_FIELDS if field in message}
        if interesting:
            logger.info("weixin.experiment3.group_fields", fields=interesting, message=message)
        else:
            logger.info("weixin.experiment3.message", message=message)

    async def _run_experiment1(self, user_id: str) -> None:
        try:
            await asyncio.sleep(_EXPERIMENT_DELAY_SECONDS)
            context_token = self.latest_context_tokens.get(user_id, "")
            response = await self.client.send_message_raw(
                to_user_id=user_id,
                text="[实验1] 这是 5 分钟后用缓存 token 发送的主动消息",
                context_token=context_token,
            )
            _report_experiment(
                1,
                "5 分钟后复用缓存 context_token 主动发送",
                _is_success_response(response),
                response,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _report_experiment(
                1,
                "5 分钟后复用缓存 context_token 主动发送",
                False,
                {
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )

    async def _run_experiment2(self, user_id: str) -> None:
        cases = [
            ("空字符串 context_token", ""),
            ("省略 context_token 字段", None),
            ("无效 context_token", "invalid_test_token"),
        ]
        for description, context_token in cases:
            try:
                response = await self.client.send_message_raw(
                    to_user_id=user_id,
                    text=f"[实验2] {description}",
                    context_token=context_token,
                )
                _report_experiment(2, description, _is_success_response(response), response)
            except Exception as exc:
                _report_experiment(
                    2,
                    description,
                    False,
                    {
                        "error": type(exc).__name__,
                        "detail": str(exc),
                    },
                )

    def _schedule_task(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except NotImplementedError:
                logger.warning("weixin.demo.signal_not_supported", signal=str(sig))

    def _handle_signal(self) -> None:
        self.stop_event.set()
        if self._run_task is not None:
            self._run_task.cancel()


async def _async_main() -> int:
    args = _build_parser().parse_args()
    configure_logging(level="INFO", json_logs=False)
    client = ILinkClient(base_url=args.base_url, token_path=args.token_path)
    app = DemoApp(client, experiment=args.experiment)
    try:
        await app.run()
    finally:
        await app.shutdown()
    return 0


def main() -> int:
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        print("收到 Ctrl+C，正在退出。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

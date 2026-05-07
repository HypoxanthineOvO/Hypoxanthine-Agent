from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from hypo_agent.channels.weixin.crypto import encrypt_media, generate_aes_key
from hypo_agent.channels.weixin.weixin_adapter import WeixinAdapter
from hypo_agent.channels.weixin.weixin_channel import WeixinChannel
from hypo_agent.models import Message


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5kZxQAAAAASUVORK5CYII="
)


class QueueStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def put(self, event: dict) -> None:
        self.events.append(event)


class MediaClientStub:
    def __init__(self, *, bot_token: str | None = "bot-token") -> None:
        self.bot_token = bot_token
        self.user_id = "target@im.wechat"
        self.bot_id = "bot-1"
        self.last_context_token = ""
        self.downloads: dict[str, bytes] = {}
        self.download_requests: list[str] = []
        self.sent_text: list[dict] = []
        self.upload_requests: list[dict] = []
        self.uploaded_payloads: list[tuple[str, bytes]] = []
        self.sent_images: list[dict] = []

    async def close(self) -> None:
        return None

    def remember_user_id(self, user_id: str) -> None:
        self.user_id = user_id

    def remember_context_token(self, context_token: str) -> None:
        self.last_context_token = context_token

    async def download_media(self, url: str) -> bytes:
        self.download_requests.append(url)
        return self.downloads[url]

    async def send_message(self, to_user_id: str, text: str, context_token: str = "", **kwargs) -> str:
        self.sent_text.append(
            {
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
                **kwargs,
            }
        )
        return "wcb-test"

    @staticmethod
    def build_media_upload_payload(
        *,
        to_user_id: str,
        media_type: int,
        plaintext: bytes,
        encrypted_size: int,
        aes_key_hex: str,
    ) -> dict:
        return {
            "filekey": "filekey-1",
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(plaintext),
            "rawfilemd5": "md5-1",
            "filesize": encrypted_size,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
        }

    async def get_upload_url(self, **payload) -> dict:
        self.upload_requests.append(payload)
        return {"upload_param": f"upload-param-{len(self.upload_requests)}"}

    async def upload_media(self, *, upload_param: str, filekey: str, encrypted_data: bytes) -> str:
        self.uploaded_payloads.append((f"{upload_param}:{filekey}", encrypted_data))
        return f"download-param-{len(self.uploaded_payloads)}"

    async def send_image(
        self,
        to_user_id: str,
        encrypt_query_param: str,
        aes_key: str,
        encrypted_file_size: int,
        *,
        context_token: str = "",
        **kwargs,
    ) -> dict:
        payload = {
            "to_user_id": to_user_id,
            "encrypt_query_param": encrypt_query_param,
            "aes_key": aes_key,
            "encrypted_file_size": encrypted_file_size,
            "context_token": context_token,
            **kwargs,
        }
        self.sent_images.append(payload)
        return payload


class ImageRendererStub:
    def __init__(self, rendered_path: Path) -> None:
        self.rendered_path = rendered_path
        self.available = True
        self.calls: list[tuple[str, str]] = []

    async def render_to_image(self, content: str, block_type: str = "markdown") -> str:
        self.calls.append((content, block_type))
        return str(self.rendered_path)


def test_weixin_channel_downloads_image_and_voice_into_message(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = MediaClientStub()
        aes_key = generate_aes_key()
        encrypted = encrypt_media(_PNG_1X1, aes_key)
        client.downloads["https://cdn.example/image"] = encrypted

        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,  # type: ignore[arg-type]
            uploads_dir=tmp_path,
        )
        channel.client = client  # type: ignore[assignment]

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "item_list": [
                    {"type": 1, "text_item": {"text": "看图"}},
                    {"type": 3, "voice_item": {"text": "语音转写"}},
                    {
                        "type": 2,
                        "image_item": {
                            "url": "https://cdn.example/image",
                            "aes_key": aes_key.hex(),
                            "file_size": len(_PNG_1X1),
                            "width": 1,
                            "height": 1,
                        },
                    },
                ],
            }
        )

        assert len(queue.events) == 1
        message = queue.events[0]["message"]
        assert message.text == "看图\n语音转写"
        assert len(message.attachments) == 1
        attachment = message.attachments[0]
        assert attachment.type == "image"
        assert attachment.filename == "weixin-alice@im.wechat-image.png"
        assert attachment.mime_type == "image/png"
        assert Path(attachment.url).exists()
        assert Path(attachment.url).read_bytes() == _PNG_1X1

    asyncio.run(_run())


def test_weixin_channel_accepts_raw_ecb_image_payload_without_pkcs7_padding(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = MediaClientStub()
        aes_key = generate_aes_key()
        payload = b"0123456789abcdef" * 2
        cipher = Cipher(algorithms.AES(aes_key), modes.ECB())
        encryptor = cipher.encryptor()
        client.downloads["https://cdn.example/image-raw"] = encryptor.update(payload) + encryptor.finalize()

        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,  # type: ignore[arg-type]
            uploads_dir=tmp_path,
        )
        channel.client = client  # type: ignore[assignment]

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "url": "https://cdn.example/image-raw",
                            "aes_key": aes_key.hex(),
                            "file_size": len(payload),
                            "width": 1,
                            "height": 1,
                        },
                    },
                ],
            }
        )

        assert len(queue.events) == 1
        attachment = queue.events[0]["message"].attachments[0]
        assert Path(attachment.url).read_bytes() == payload

    asyncio.run(_run())


def test_weixin_channel_accepts_plain_image_payload_when_cdn_returns_unencrypted_bytes(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = MediaClientStub()
        aes_key = generate_aes_key()
        client.downloads["https://cdn.example/image-plain"] = _PNG_1X1

        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,  # type: ignore[arg-type]
            uploads_dir=tmp_path,
        )
        channel.client = client  # type: ignore[assignment]

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "url": "https://cdn.example/image-plain",
                            "aes_key": aes_key.hex(),
                            "file_size": len(_PNG_1X1),
                            "width": 1,
                            "height": 1,
                        },
                    },
                ],
            }
        )

        assert len(queue.events) == 1
        attachment = queue.events[0]["message"].attachments[0]
        assert Path(attachment.url).read_bytes() == _PNG_1X1

    asyncio.run(_run())


def test_weixin_channel_downloads_image_from_encrypt_query_param_with_hex_aeskey(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = MediaClientStub()
        aes_key = generate_aes_key()
        encrypted_query_param = "3057020100044b3049020100020462fec931"
        encrypted = encrypt_media(_PNG_1X1, aes_key)
        expected_url = (
            "https://novac2c.cdn.weixin.qq.com/c2c/download"
            "?encrypted_query_param=3057020100044b3049020100020462fec931"
        )
        client.downloads[expected_url] = encrypted

        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,  # type: ignore[arg-type]
            uploads_dir=tmp_path,
        )
        channel.client = client  # type: ignore[assignment]

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "url": encrypted_query_param,
                            "aeskey": aes_key.hex(),
                            "media": {
                                "encrypt_query_param": encrypted_query_param,
                            },
                            "file_size": len(_PNG_1X1),
                        },
                    },
                ],
            }
        )

        assert client.download_requests == [expected_url]
        assert len(queue.events) == 1
        attachment = queue.events[0]["message"].attachments[0]
        assert Path(attachment.url).read_bytes() == _PNG_1X1

    asyncio.run(_run())


def test_weixin_channel_downloads_file_and_video_attachments(tmp_path: Path) -> None:
    async def _run() -> None:
        queue = QueueStub()
        client = MediaClientStub()
        file_key = generate_aes_key()
        video_key = generate_aes_key()
        client.downloads["https://cdn.example/file"] = encrypt_media(b"report", file_key)
        client.downloads["https://cdn.example/video"] = encrypt_media(b"video", video_key)

        channel = WeixinChannel(
            config={"token_path": "memory/weixin_auth.json", "allowed_users": []},
            message_queue=queue,
            build_message=Message,
            client_factory=lambda: client,  # type: ignore[arg-type]
            uploads_dir=tmp_path,
        )
        channel.client = client  # type: ignore[assignment]

        await channel._handle_message(  # type: ignore[attr-defined]
            {
                "from_user_id": "alice@im.wechat",
                "item_list": [
                    {
                        "type": 4,
                        "file_item": {
                            "url": "https://cdn.example/file",
                            "aes_key": file_key.hex(),
                            "file_name": "report.txt",
                            "file_size": 6,
                        },
                    },
                    {
                        "type": 5,
                        "video_item": {
                            "url": "https://cdn.example/video",
                            "aes_key": video_key.hex(),
                            "file_name": "clip.mp4",
                            "file_size": 5,
                        },
                    },
                ],
            }
        )

        assert len(queue.events) == 1
        attachments = queue.events[0]["message"].attachments
        assert [item.type for item in attachments] == ["file", "video"]
        assert attachments[0].filename == "report.txt"
        assert attachments[1].filename == "clip.mp4"
        assert Path(attachments[0].url).read_bytes() == b"report"
        assert Path(attachments[1].url).read_bytes() == b"video"

    asyncio.run(_run())


def test_weixin_adapter_keeps_markdown_code_block_in_text_item(tmp_path: Path) -> None:
    async def _run() -> None:
        rendered_path = tmp_path / "rendered.png"
        rendered_path.write_bytes(_PNG_1X1)
        client = MediaClientStub()
        client.last_context_token = "ctx-markdown"
        renderer = ImageRendererStub(rendered_path)
        adapter = WeixinAdapter(
            client=client,  # type: ignore[arg-type]
            target_user_id="target@im.wechat",
            image_renderer=renderer,
            send_delay_seconds=0,
        )

        await adapter.push(
            Message(
                text="这里有代码：\n```python\nprint(1)\n```",
                sender="assistant",
                session_id="main",
                channel="qq",
            )
        )

        assert client.sent_text[0]["text"] == "这里有代码：\n\n```python\nprint(1)\n```"
        assert renderer.calls == []
        assert client.upload_requests == []
        assert client.sent_images == []

    asyncio.run(_run())

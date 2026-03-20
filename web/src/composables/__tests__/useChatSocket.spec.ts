import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { useChatSocket } from "../useChatSocket";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  sent: string[] = [];
  readyState = MockWebSocket.CONNECTING;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }

  emitOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  emitClose(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
});

describe("useChatSocket", () => {
  it("starts disconnected and connects with tokenized URL", () => {
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId: ref("s1"),
    });

    expect(socket.status.value).toBe("disconnected");
    socket.connect();

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0]?.url).toBe(
      "ws://localhost:8000/ws?token=abc123",
    );

    MockWebSocket.instances[0]?.emitOpen();
    expect(socket.status.value).toBe("connected");
  });

  it("serializes outbound message and stores inbound message", () => {
    const sessionId = ref("session-1");
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId,
    });
    socket.connect();

    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();

    socket.sendText("hello");
    const outbound = JSON.parse(ws.sent[0] ?? "{}");
    expect(outbound.text).toBe("hello");
    expect(outbound.sender).toBe("user");
    expect(outbound.session_id).toBe("session-1");

    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "**",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "ok",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "**",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_done",
        sender: "assistant",
        session_id: "session-1",
        attachments: [
          {
            type: "file",
            url: "/tmp/export.pdf",
            filename: "export.pdf",
            mime_type: "application/pdf",
          },
        ],
      }),
    );

    expect(socket.messages.value[0]?.text).toBe("hello");
    expect(socket.messages.value[1]?.text).toBe("**ok**");
    expect(socket.messages.value[1]?.attachments?.[0]?.filename).toBe("export.pdf");
    expect(socket.messages.value).toHaveLength(2);
  });

  it("uses latest session id when sending", () => {
    const sessionId = ref("s1");
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId,
    });

    socket.connect();
    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();

    sessionId.value = "s2";
    socket.sendText("hello");
    expect(JSON.parse(ws.sent[0] ?? "{}").session_id).toBe("s2");
  });

  it("sends attachment payloads and allows empty text when attachments exist", () => {
    const sessionId = ref("s1");
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId,
    });

    socket.connect();
    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();

    const sent = socket.sendMessage("", [
      {
        type: "image",
        url: "/tmp/cat.png",
        filename: "cat.png",
        mime_type: "image/png",
        size_bytes: 123,
      },
    ]);

    expect(sent).toBe(true);
    const outbound = JSON.parse(ws.sent[0] ?? "{}");
    expect(outbound.text).toBeNull();
    expect(outbound.attachments).toHaveLength(1);
    expect(outbound.attachments[0].filename).toBe("cat.png");
    expect(socket.messages.value[0]?.attachments?.[0]?.type).toBe("image");
  });

  it("replaces local messages when restoring session history", () => {
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId: ref("s1"),
    });

    socket.replaceMessages([
      { text: "old", sender: "user", session_id: "s1" },
      { text: "answer", sender: "assistant", session_id: "s1" },
    ]);
    expect(socket.messages.value).toHaveLength(2);
    expect(socket.messages.value[0]?.text).toBe("old");
  });

  it("keeps message_tag from proactive server message", () => {
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId: ref("s1"),
    });
    socket.connect();

    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();
    ws.emitMessage(
      JSON.stringify({
        text: "提醒：喝水",
        sender: "assistant",
        session_id: "s1",
        message_tag: "reminder",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        text: "邮件扫描完成",
        sender: "assistant",
        session_id: "s1",
        message_tag: "email_scan",
      }),
    );

    expect(socket.messages.value).toHaveLength(2);
    expect(socket.messages.value[0]?.message_tag).toBe("reminder");
    expect(socket.messages.value[1]?.message_tag).toBe("email_scan");
  });

  it("converts narration events into ephemeral chat messages", () => {
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId: ref("main"),
    });
    socket.connect();

    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();
    ws.emitMessage(
      JSON.stringify({
        type: "narration",
        text: "我去翻一下你的收件箱。",
        session_id: "main",
        timestamp: "2026-03-13T10:00:00+08:00",
      }),
    );

    expect(socket.messages.value).toHaveLength(1);
    expect(socket.messages.value[0]?.text).toBe("我去翻一下你的收件箱。");
    expect(socket.messages.value[0]?.message_tag).toBe("narration");
    expect(socket.messages.value[0]?.metadata?.ephemeral).toBe(true);
    expect(socket.messages.value[0]?.timestamp).toBe("2026-03-13T10:00:00+08:00");
  });

  it("schedules reconnect with exponential backoff after unexpected close", () => {
    vi.useFakeTimers();
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId: ref("s1"),
    });

    socket.connect();
    const first = MockWebSocket.instances[0];
    if (!first) {
      throw new Error("WebSocket was not created");
    }
    first.emitOpen();
    first.emitClose();

    expect(socket.status.value).toBe("reconnecting");
    expect(socket.reconnectDelayMs.value).toBe(1000);

    vi.advanceTimersByTime(1000);
    expect(MockWebSocket.instances).toHaveLength(2);

    vi.useRealTimers();
  });
});

import { describe, expect, it, beforeEach } from "vitest";

import { useChatSocket } from "../useChatSocket";

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.onclose?.({} as CloseEvent);
  }

  emitOpen(): void {
    this.onopen?.(new Event("open"));
  }

  emitMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
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
      sessionId: "s1",
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
    const socket = useChatSocket({
      url: "ws://localhost:8000/ws",
      token: "abc123",
      sessionId: "session-1",
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
        text: "**ok**",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    expect(socket.messages.value[0]?.text).toBe("hello");
    expect(socket.messages.value[1]?.text).toBe("**ok**");
  });
});

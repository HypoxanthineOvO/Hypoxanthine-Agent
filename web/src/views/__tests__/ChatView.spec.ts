import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatView from "../ChatView.vue";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(): void {}

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
}

beforeEach(() => {
  MockWebSocket.instances = [];
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

describe("ChatView", () => {
  it("auto-connects websocket on mount", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });
    await flushUi();
    expect(wrapper.text()).toContain("Connecting");
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("loads sessions and message history on mount", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ session_id: "s1" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ text: "old", sender: "user", session_id: "s1" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/s1/messages?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/s1/tool-invocations?token=test-token",
    );
    expect(wrapper.text()).toContain("old");
  });

  it("infers apiBase from wsUrl and renders session sidebar on mount", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ session_id: "session-100", message_count: 1 }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          { text: "restored", sender: "user", session_id: "session-100" },
        ],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://127.0.0.1:8000/ws",
        token: "test-token",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/sessions?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/sessions/session-100/messages?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/sessions/session-100/tool-invocations?token=test-token",
    );
    expect(
      wrapper.find('[data-testid="session-item-session-100"]').exists(),
    ).toBe(true);
    expect(wrapper.text()).toContain("restored");
  });

  it("switches session and renders the selected history", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ session_id: "s1" }, { session_id: "s2" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ text: "one", sender: "user", session_id: "s1" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ text: "two", sender: "user", session_id: "s2" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();
    expect(wrapper.text()).toContain("one");

    await wrapper.get('[data-testid="session-item-s2"]').trigger("click");
    await flushUi();
    await flushUi();
    await flushUi();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/s2/messages?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/s2/tool-invocations?token=test-token",
    );
    expect(wrapper.text()).toContain("two");
  });

  it("loads merged message and tool invocation history on session switch", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ session_id: "s1" }, { session_id: "s2" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ text: "hello", sender: "user", session_id: "s1" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            text: "请读取本系统的cpuinfo",
            sender: "user",
            session_id: "s2",
            timestamp: "2026-03-06T10:00:00+00:00",
          },
        ],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 42,
            session_id: "s2",
            tool_name: "run_command",
            skill_name: "tmux",
            params_json: "{\"command\":\"echo hi\"}",
            status: "success",
            result_summary: "{\"stdout\":\"ok\"}",
            duration_ms: 12.3,
            error_info: "",
            compressed_meta_json:
              "{\"cache_id\":\"cache-1\",\"original_chars\":1000,\"compressed_chars\":120}",
            created_at: "2026-03-06 10:02:00",
          },
        ],
      });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
        apiBase: "http://localhost:8000/api",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();
    await wrapper.get('[data-testid="session-item-s2"]').trigger("click");
    await flushUi();
    await flushUi();
    await flushUi();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/s2/messages?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/s2/tool-invocations?token=test-token",
    );
    expect(wrapper.text()).toContain("请读取本系统的cpuinfo");

    const vmMessages = (wrapper.vm as { messages?: unknown }).messages;
    const timeline = (
      Array.isArray(vmMessages)
        ? vmMessages
        : ((vmMessages as { value?: unknown[] } | undefined)?.value ?? [])
    ) as Array<Record<string, unknown>>;

    const startIndex = timeline.findIndex((item) => item.event_type === "tool_call_start");
    const resultIndex = timeline.findIndex((item) => item.event_type === "tool_call_result");
    const userIndex = timeline.findIndex(
      (item) => item.text === "请读取本系统的cpuinfo",
    );
    expect(userIndex).toBeGreaterThan(-1);
    expect(startIndex).toBeGreaterThan(-1);
    expect(startIndex).toBeGreaterThan(userIndex);
    expect(resultIndex).toBeGreaterThan(startIndex);

    const start = timeline[startIndex] ?? {};
    const result = timeline[resultIndex] ?? {};
    expect(start.tool_call_id).toBe("inv_42");
    expect(start.arguments).toEqual({ command: "echo hi" });
    expect(result.tool_call_id).toBe("inv_42");
    expect(result.result).toBe("{\"stdout\":\"ok\"}");
    expect(result.metadata).toEqual({});
    expect(result.compressed_meta).toEqual({
      cache_id: "cache-1",
      original_chars: 1000,
      compressed_chars: 120,
    });
  });
});

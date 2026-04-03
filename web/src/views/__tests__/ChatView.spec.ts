import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MockWebSocket, flushUi, installMockWebSocket } from "@/test/utils";
import ChatView from "../ChatView.vue";

beforeEach(() => {
  installMockWebSocket();
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

describe("ChatView", () => {
  it("auto-connects websocket on mount", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();
    expect(wrapper.text()).toContain("Connect");
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("loads main session history on mount without fetching session list", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ text: "old", sender: "user", session_id: "main" }],
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
      "http://localhost:8000/api/sessions/main/messages?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/main/tool-invocations?token=test-token",
    );
    expect(fetchMock).not.toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions?token=test-token",
    );
    expect(wrapper.text()).toContain("old");
  });

  it("renders feishu source as badge instead of legacy text prefix in restored history", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          { text: "[飞书] 同步一下", sender: "user", session_id: "main", channel: "feishu" },
        ],
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

    expect(wrapper.text()).toContain("📨 飞书");
    expect(wrapper.text()).toContain("同步一下");
    expect(wrapper.text()).not.toContain("[飞书] 同步一下");
  });

  it("uses explicit session id prop for debug loading", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ text: "restored", sender: "user", session_id: "debug-session" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });
    vi.stubGlobal("fetch", fetchMock);

    mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
        apiBase: "http://localhost:8000/api",
        sessionId: "debug-session",
      },
    });

    await flushUi();
    await flushUi();
    await flushUi();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/debug-session/messages?token=test-token",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/sessions/debug-session/tool-invocations?token=test-token",
    );
  });

  it("hides the session sidebar and new-session controls", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });

    await flushUi();
    expect(wrapper.find('[data-testid="new-session-button"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="session-sidebar"]').exists()).toBe(false);
  });

  it("renders welcome shortcuts when the main session is empty", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });

    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("Hi，我是 Hypo-Agent");
    expect(wrapper.text()).toContain("帮我看看邮件");
    expect(wrapper.text()).toContain("今天有什么任务");
  });

  it("fills the composer when a welcome shortcut is clicked", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });

    await flushUi();
    await flushUi();

    await wrapper.get('[data-testid="quick-prompt-0"]').trigger("click");

    const textarea = wrapper.get("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toBe("📧 帮我看看邮件");
  });

  it("loads merged message and tool invocation history for the active session", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            text: "请读取本系统的cpuinfo",
            sender: "user",
            session_id: "main",
            timestamp: "2026-03-06T10:00:00+00:00",
          },
        ],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 42,
            session_id: "main",
            tool_name: "exec_command",
            skill_name: "exec",
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
    expect(startIndex).toBeGreaterThan(userIndex);
    expect(resultIndex).toBeGreaterThan(startIndex);
  });

  it("does not render restored tool invocations as visible chat messages", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 42,
            session_id: "main",
            tool_name: "exec_command",
            skill_name: "exec",
            params_json: "{\"command\":\"echo hi\"}",
            status: "success",
            result_summary: "{\"stdout\":\"ok\"}",
            duration_ms: 12.3,
            error_info: "",
            compressed_meta_json: null,
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

    expect(wrapper.text()).not.toContain("🔧 执行了 exec_command");
    expect(wrapper.text()).toContain("Hi，我是 Hypo-Agent");
  });

  it("renders a QQ source badge for synced qq messages", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            text: "你好，来自 QQ",
            sender: "user",
            session_id: "main",
            channel: "qq",
          },
        ],
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

    expect(wrapper.text()).toContain("🐧 QQ");
    expect(wrapper.text()).toContain("你好，来自 QQ");
  });

  it("renders image attachments from restored session history", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            text: "看图",
            sender: "user",
            session_id: "main",
            attachments: [
              {
                type: "image",
                url: "/tmp/cat.png",
                filename: "cat.png",
                mime_type: "image/png",
                size_bytes: 12,
              },
            ],
          },
        ],
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

    const image = wrapper.get('img[alt="image attachment"]');
    expect(image.attributes("src")).toContain(
      "http://localhost:8000/api/files?path=%2Ftmp%2Fcat.png&token=test-token",
    );
  });

  it("hides runtime tool events when marked ephemeral", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();
    ws.emitMessage(
      JSON.stringify({
        type: "tool_call_start",
        tool_name: "run_code",
        tool_call_id: "call_1",
        arguments: { code: "print(1)" },
        session_id: "main",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "tool_call_result",
        tool_name: "run_code",
        tool_call_id: "call_1",
        status: "success",
        result: { stdout: "1" },
        error_info: "",
        metadata: { ephemeral: true },
        session_id: "main",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "现在是 10:00",
        sender: "assistant",
        session_id: "main",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_done",
        sender: "assistant",
        session_id: "main",
      }),
    );

    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("现在是 10:00");
    expect(wrapper.text()).not.toContain("🔧 执行了 run_code");
  });

  it("hides legacy tool status messages from the chat timeline", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();
    ws.emitMessage(
      JSON.stringify({
        text: "⏳ 正在处理...",
        sender: "assistant",
        session_id: "main",
        message_tag: "tool_status",
        metadata: { ephemeral: true },
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "narration",
        text: "我先去翻一下收件箱。",
        session_id: "main",
      }),
    );

    await flushUi();
    await flushUi();

    expect(wrapper.text()).toContain("我先去翻一下收件箱。");
    expect(wrapper.text()).not.toContain("⏳ 正在处理...");
  });

  it("renders narration messages with a weaker dedicated style and keeps them after reply", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });
    await flushUi();
    await flushUi();
    await flushUi();

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
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "已经帮你扫了一遍。",
        sender: "assistant",
        session_id: "main",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_done",
        sender: "assistant",
        session_id: "main",
      }),
    );

    await flushUi();
    await flushUi();

    const narration = wrapper.get('[data-message-tag="narration"]');
    expect(narration.text()).toContain("我去翻一下你的收件箱。");
    expect(narration.find(".bubble-avatar").exists()).toBe(false);
    expect(wrapper.text()).toContain("已经帮你扫了一遍。");
  });

  it("inserts a time separator when visible messages are more than five minutes apart", async () => {
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            text: "第一条",
            sender: "user",
            session_id: "main",
            timestamp: "2026-03-14T10:00:00Z",
          },
          {
            text: "第二条",
            sender: "assistant",
            session_id: "main",
            timestamp: "2026-03-14T10:06:00Z",
          },
        ],
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

    const separators = wrapper.findAll('[data-testid="message-time-separator"]');
    expect(separators.length).toBeGreaterThanOrEqual(2);
    expect(wrapper.text()).toContain("第一条");
    expect(wrapper.text()).toContain("第二条");
  });
});

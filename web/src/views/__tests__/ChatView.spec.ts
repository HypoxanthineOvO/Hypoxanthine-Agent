import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it } from "vitest";

import ChatView from "../ChatView.vue";

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
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

describe("ChatView", () => {
  it("shows disconnected status by default", () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });
    expect(wrapper.text()).toContain("Disconnected");
  });

  it("renders markdown in assistant message", async () => {
    const wrapper = mount(ChatView, {
      props: {
        wsUrl: "ws://localhost:8000/ws",
        token: "test-token",
      },
    });

    await wrapper.get('[data-testid="connect-button"]').trigger("click");
    const ws = MockWebSocket.instances[0];
    if (!ws) {
      throw new Error("WebSocket was not created");
    }
    ws.emitOpen();
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "**bo",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_chunk",
        text: "ld**",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    ws.emitMessage(
      JSON.stringify({
        type: "assistant_done",
        sender: "assistant",
        session_id: "session-1",
      }),
    );
    await nextTick();

    expect(wrapper.html()).toContain("<strong>bold</strong>");
  });
});

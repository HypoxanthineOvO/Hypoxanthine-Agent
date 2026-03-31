import { nextTick } from "vue";
import type { Message } from "@/types/message";

// ---------------------------------------------------------------------------
// MockWebSocket
// ---------------------------------------------------------------------------

export class MockWebSocket {
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

export function installMockWebSocket(): void {
  MockWebSocket.instances = [];
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
}

export function uninstallMockWebSocket(): void {
  MockWebSocket.instances = [];
}

// ---------------------------------------------------------------------------
// flushUi
// ---------------------------------------------------------------------------

export async function flushUi(): Promise<void> {
  await Promise.resolve();
  await nextTick();
}

// ---------------------------------------------------------------------------
// createMockMessage
// ---------------------------------------------------------------------------

export function createMockMessage(
  overrides: Partial<Message> & { sender: string; session_id: string },
): Message {
  return {
    text: null,
    ...overrides,
  };
}

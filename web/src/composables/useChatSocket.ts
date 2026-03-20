import { ref } from "vue";
import type { Ref } from "vue";

import type {
  Attachment,
  AssistantChunkEvent,
  AssistantDoneEvent,
  ConnectionStatus,
  IncomingWsEvent,
  Message,
  NarrationEvent,
  ToolCallResultEvent,
  ToolCallStartEvent,
  WsErrorEvent,
} from "../types/message";

interface UseChatSocketOptions {
  url: string;
  token: string;
  sessionId: Ref<string>;
}

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000] as const;

function withToken(url: string, token: string): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

export function useChatSocket(options: UseChatSocketOptions) {
  const status = ref<ConnectionStatus>("disconnected");
  const messages = ref<Message[]>([]);
  const lastError = ref<WsErrorEvent | null>(null);
  const reconnectDelayMs = ref<number | null>(null);

  let socket: WebSocket | null = null;
  let streamingAssistantIndex: number | null = null;
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let shouldReconnect = true;

  const isMessage = (payload: IncomingWsEvent): payload is Message =>
    "sender" in payload &&
    "session_id" in payload &&
    !("type" in payload);

  const isAssistantChunkEvent = (
    payload: IncomingWsEvent,
  ): payload is AssistantChunkEvent =>
    "type" in payload && payload.type === "assistant_chunk";

  const isAssistantDoneEvent = (
    payload: IncomingWsEvent,
  ): payload is AssistantDoneEvent =>
    "type" in payload && payload.type === "assistant_done";

  const isWsErrorEvent = (payload: IncomingWsEvent): payload is WsErrorEvent =>
    "type" in payload && payload.type === "error";

  const isNarrationEvent = (
    payload: IncomingWsEvent,
  ): payload is NarrationEvent =>
    "type" in payload && payload.type === "narration";

  const isToolCallStartEvent = (
    payload: IncomingWsEvent,
  ): payload is ToolCallStartEvent =>
    "type" in payload && payload.type === "tool_call_start";

  const isToolCallResultEvent = (
    payload: IncomingWsEvent,
  ): payload is ToolCallResultEvent =>
    "type" in payload && payload.type === "tool_call_result";

  const clearReconnectTimer = (): void => {
    if (reconnectTimer === null) {
      return;
    }
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    reconnectDelayMs.value = null;
  };

  const scheduleReconnect = (): void => {
    if (!shouldReconnect || reconnectTimer !== null) {
      return;
    }
    const index = Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1);
    const delay = RECONNECT_DELAYS_MS[index] ?? RECONNECT_DELAYS_MS[0];
    reconnectDelayMs.value = delay;
    reconnectAttempt += 1;
    status.value = "reconnecting";

    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      reconnectDelayMs.value = null;
      connect();
    }, delay);
  };

  const connect = (): void => {
    shouldReconnect = true;
    const readyState = socket?.readyState;
    if (
      readyState === WebSocket.CONNECTING ||
      readyState === WebSocket.OPEN
    ) {
      return;
    }

    clearReconnectTimer();
    status.value = reconnectAttempt > 0 ? "reconnecting" : "connecting";
    socket = new WebSocket(withToken(options.url, options.token));

    socket.onopen = () => {
      status.value = "connected";
      reconnectAttempt = 0;
      reconnectDelayMs.value = null;
      lastError.value = null;
    };

    socket.onclose = () => {
      streamingAssistantIndex = null;
      socket = null;
      if (!shouldReconnect) {
        reconnectAttempt = 0;
        reconnectDelayMs.value = null;
        status.value = "disconnected";
        return;
      }
      scheduleReconnect();
    };

    socket.onerror = () => {
      status.value = "error";
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as IncomingWsEvent;

        if (isWsErrorEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          lastError.value = payload;
          status.value = "error";
          return;
        }

        if (isNarrationEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          messages.value.push({
            text: payload.text,
            sender: "assistant",
            session_id: payload.session_id,
            timestamp: payload.timestamp,
            message_tag: "narration",
            metadata: {
              ephemeral: true,
              narration: true,
            },
          });
          return;
        }

        if (isAssistantChunkEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }

          const chunkText = payload.text ?? "";
          const isExistingStreamSlot =
            streamingAssistantIndex !== null &&
            streamingAssistantIndex >= 0 &&
            streamingAssistantIndex < messages.value.length &&
            messages.value[streamingAssistantIndex]?.sender === "assistant" &&
            messages.value[streamingAssistantIndex]?.session_id ===
              payload.session_id;

          if (!isExistingStreamSlot) {
            messages.value.push({
              text: chunkText,
              sender: "assistant",
              session_id: payload.session_id,
              timestamp: payload.timestamp,
            });
            streamingAssistantIndex = messages.value.length - 1;
            return;
          }

          const targetIndex = streamingAssistantIndex;
          if (targetIndex === null) {
            return;
          }
          const existing = messages.value[targetIndex];
          if (!existing) {
            return;
          }
          existing.text = `${existing.text ?? ""}${chunkText}`;
          existing.timestamp = existing.timestamp ?? payload.timestamp;
          return;
        }

        if (isAssistantDoneEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          if (
            streamingAssistantIndex !== null &&
            streamingAssistantIndex >= 0 &&
            streamingAssistantIndex < messages.value.length
          ) {
            const existing = messages.value[streamingAssistantIndex];
            if (existing) {
              existing.timestamp = existing.timestamp ?? payload.timestamp;
              if (Array.isArray(payload.attachments) && payload.attachments.length > 0) {
                existing.attachments = payload.attachments.map((attachment) => ({ ...attachment }));
              }
            }
          }
          streamingAssistantIndex = null;
          return;
        }

        if (isToolCallStartEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          messages.value.push({
            sender: "assistant",
            session_id: payload.session_id,
            event_type: "tool_call_start",
            tool_name: payload.tool_name,
            tool_call_id: payload.tool_call_id,
            arguments: payload.arguments,
          });
          return;
        }

        if (isToolCallResultEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          messages.value.push({
            sender: "assistant",
            session_id: payload.session_id,
            event_type: "tool_call_result",
            tool_name: payload.tool_name,
            tool_call_id: payload.tool_call_id,
            status: payload.status,
            result: payload.result,
            error_info: payload.error_info,
            metadata: payload.metadata,
            compressed_meta: payload.compressed_meta,
            attachments: payload.attachments,
          });
          return;
        }

        if (!isMessage(payload) || !payload.sender || !payload.session_id) {
          return;
        }
        if (payload.session_id !== options.sessionId.value) {
          return;
        }
        messages.value.push(payload);
      } catch {
        status.value = "error";
      }
    };
  };

  const reconnectNow = (): void => {
    clearReconnectTimer();
    connect();
  };

  const disconnect = (): void => {
    shouldReconnect = false;
    clearReconnectTimer();
    reconnectAttempt = 0;
    reconnectDelayMs.value = null;
    streamingAssistantIndex = null;
    if (socket) {
      socket.close();
      socket = null;
    }
    status.value = "disconnected";
  };

  const sendMessage = (text: string, attachments: Attachment[] = []): boolean => {
    const trimmed = text.trim();
    const normalizedAttachments = attachments.map((attachment) => ({ ...attachment }));
    if (!trimmed && normalizedAttachments.length === 0) {
      return false;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    const message: Message = {
      text: trimmed || null,
      attachments: normalizedAttachments,
      sender: "user",
      session_id: options.sessionId.value,
      timestamp: new Date().toISOString(),
    };
    socket.send(JSON.stringify(message));
    messages.value.push(message);
    return true;
  };

  const sendText = (text: string): boolean => sendMessage(text);

  const replaceMessages = (nextMessages: Message[]): void => {
    messages.value = nextMessages.map((item) => ({ ...item }));
    streamingAssistantIndex = null;
  };

  return {
    connect,
    disconnect,
    lastError,
    messages,
    reconnectDelayMs,
    reconnectNow,
    replaceMessages,
    sendMessage,
    sendText,
    status,
  };
}

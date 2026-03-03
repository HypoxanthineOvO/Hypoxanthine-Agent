import { ref } from "vue";

import type {
  AssistantChunkEvent,
  AssistantDoneEvent,
  ConnectionStatus,
  IncomingWsEvent,
  Message,
} from "../types/message";

interface UseChatSocketOptions {
  url: string;
  token: string;
  sessionId: string;
}

function withToken(url: string, token: string): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

export function useChatSocket(options: UseChatSocketOptions) {
  const status = ref<ConnectionStatus>("disconnected");
  const messages = ref<Message[]>([]);
  let socket: WebSocket | null = null;
  let streamingAssistantIndex: number | null = null;

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

  const connect = (): void => {
    if (
      socket &&
      (socket.readyState === WebSocket.CONNECTING ||
        socket.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    status.value = "connecting";
    socket = new WebSocket(withToken(options.url, options.token));

    socket.onopen = () => {
      status.value = "connected";
    };

    socket.onclose = () => {
      status.value = "disconnected";
      streamingAssistantIndex = null;
      socket = null;
    };

    socket.onerror = () => {
      status.value = "error";
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as IncomingWsEvent;

        if (isAssistantChunkEvent(payload)) {
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
            });
            streamingAssistantIndex = messages.value.length - 1;
            return;
          }

          const existing = messages.value[streamingAssistantIndex];
          if (!existing) {
            return;
          }
          existing.text = `${existing.text ?? ""}${chunkText}`;
          return;
        }

        if (isAssistantDoneEvent(payload)) {
          streamingAssistantIndex = null;
          return;
        }

        if (!isMessage(payload) || !payload.sender || !payload.session_id) {
          return;
        }
        messages.value.push(payload);
      } catch {
        status.value = "error";
      }
    };
  };

  const disconnect = (): void => {
    if (!socket) {
      return;
    }
    socket.close();
    streamingAssistantIndex = null;
    socket = null;
    status.value = "disconnected";
  };

  const sendText = (text: string): boolean => {
    const trimmed = text.trim();
    if (!trimmed) {
      return false;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }

    const message: Message = {
      text: trimmed,
      sender: "user",
      session_id: options.sessionId,
    };
    socket.send(JSON.stringify(message));
    messages.value.push(message);
    return true;
  };

  return {
    connect,
    disconnect,
    messages,
    sendText,
    status,
  };
}

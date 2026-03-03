import { ref } from "vue";

import type { ConnectionStatus, Message } from "../types/message";

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
      socket = null;
    };

    socket.onerror = () => {
      status.value = "error";
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as Message;
        if (!payload.sender || !payload.session_id) {
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

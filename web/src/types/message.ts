export interface Message {
  text?: string | null;
  image?: string | null;
  file?: string | null;
  audio?: string | null;
  sender: string;
  timestamp?: string;
  session_id: string;
}

export interface AssistantChunkEvent {
  type: "assistant_chunk";
  text: string;
  sender: "assistant";
  session_id: string;
}

export interface AssistantDoneEvent {
  type: "assistant_done";
  sender: "assistant";
  session_id: string;
}

export type IncomingWsEvent = Message | AssistantChunkEvent | AssistantDoneEvent;

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "disconnected"
  | "error";

export type MessageTag =
  | "reminder"
  | "heartbeat"
  | "email_scan"
  | "tool_status"
  | "narration"
  | string;

export interface Attachment {
  type: "image" | "file" | "audio" | "video";
  url: string;
  filename?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
}

export interface Message {
  text?: string | null;
  image?: string | null;
  file?: string | null;
  audio?: string | null;
  attachments?: Attachment[];
  sender: string;
  timestamp?: string | null;
  session_id: string;
  senderName?: string;
  senderAvatar?: string;
  message_tag?: MessageTag;
  channel?: string;
  event_type?: "tool_call_start" | "tool_call_result";
  tool_name?: string;
  tool_call_id?: string;
  arguments?: Record<string, unknown>;
  status?: string;
  result?: unknown;
  error_info?: string;
  metadata?: Record<string, unknown>;
  compressed_meta?: CompressedMeta;
}

export interface SessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface AssistantChunkEvent {
  type: "assistant_chunk";
  text: string;
  sender: "assistant";
  session_id: string;
  timestamp?: string;
}

export interface AssistantDoneEvent {
  type: "assistant_done";
  sender: "assistant";
  session_id: string;
  timestamp?: string;
  attachments?: Attachment[];
}

export interface CompressedMeta {
  cache_id: string;
  original_chars: number;
  compressed_chars: number;
}

export interface ToolCallStartEvent {
  type: "tool_call_start";
  tool_name: string;
  tool_call_id: string;
  arguments: Record<string, unknown>;
  session_id: string;
}

export interface ToolCallResultEvent {
  type: "tool_call_result";
  tool_name: string;
  tool_call_id: string;
  status: string;
  result: unknown;
  error_info: string;
  metadata: Record<string, unknown>;
  session_id: string;
  compressed_meta?: CompressedMeta;
  attachments?: Attachment[];
}

export interface WsErrorEvent {
  type: "error";
  code: string;
  message: string;
  retryable: boolean;
  session_id: string;
}

export interface NarrationEvent {
  type: "narration";
  text: string;
  session_id: string;
  timestamp?: string | null;
}

export type IncomingWsEvent =
  | Message
  | AssistantChunkEvent
  | AssistantDoneEvent
  | ToolCallStartEvent
  | ToolCallResultEvent
  | NarrationEvent
  | WsErrorEvent;

export type ConnectionStatus =
  | "connecting"
  | "reconnecting"
  | "connected"
  | "disconnected"
  | "error";

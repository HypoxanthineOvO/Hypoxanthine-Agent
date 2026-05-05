export type MessageTag =
  | "reminder"
  | "heartbeat"
  | "email_scan"
  | "tool_status"
  | "hypo_info"
  | "narration";

export type MessageKind =
  | "text"
  | "tool_call"
  | "error"
  | "narration"
  | "pipeline_event"
  | "system";

export type MessageEventType = "tool_call_start" | "tool_call_result";

export interface Attachment {
  type: "image" | "file" | "audio" | "video";
  url: string;
  filename?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
}

export interface PipelineProgressItem {
  event_type:
    | "pipeline_stage"
    | "thinking_delta"
    | "react_iteration"
    | "react_complete"
    | "compression"
    | "model_fallback"
    | "model_fallback_exhausted"
    | "tool_call_start"
    | "tool_call_result"
    | "tool_call_error";
  text: string;
  timestamp?: string;
  stage?: string;
  status?: string;
  tool?: string;
  key?: string;
  attempts?: number;
  outcome_class?: string;
}

export interface Message {
  kind?: MessageKind;
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
  event_type?: MessageEventType;
  tool_name?: string;
  tool_call_id?: string;
  arguments?: Record<string, unknown>;
  status?: string;
  result?: unknown;
  error_info?: string | null;
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

export interface PipelineStageEvent {
  type: "pipeline_stage";
  stage: string;
  detail?: string;
  model?: string;
  session_id: string;
  timestamp?: string;
}

export interface ThinkingDeltaEvent {
  type: "thinking_delta";
  content: string;
  session_id: string;
  timestamp?: string;
}

export interface ReactIterationEvent {
  type: "react_iteration";
  iteration: number;
  max_iterations: number;
  status?: string;
  session_id: string;
  timestamp?: string;
}

export interface ReactCompleteEvent {
  type: "react_complete";
  total_iterations: number;
  total_tool_calls: number;
  session_id: string;
  timestamp?: string;
}

export interface CompressionEvent {
  type: "compression";
  original_chars: number;
  compressed_chars: number;
  tool?: string;
  tool_call_id?: string;
  session_id: string;
  timestamp?: string;
}

export interface ModelFallbackEvent {
  type: "model_fallback";
  failed_model: string;
  reason: string;
  fallback_model: string;
  requested_model?: string;
  attempted_chain?: ModelAttempt[];
  user_message?: string;
  session_id: string;
  timestamp?: string;
}

export interface ModelFallbackExhaustedEvent {
  type: "model_fallback_exhausted";
  failed_model?: string;
  reason?: string;
  requested_model?: string;
  attempted_chain?: ModelAttempt[];
  user_message?: string;
  session_id: string;
  timestamp?: string;
}

export interface ModelAttempt {
  model: string;
  provider?: string | null;
  error_class?: string;
  reason?: string;
  retryable?: boolean;
  latency_ms?: number;
}

export interface ToolCallErrorEvent {
  type: "tool_call_error";
  tool: string;
  error: string;
  will_retry: boolean;
  display_name?: string;
  running_text?: string;
  summary?: string;
  attempts?: number;
  outcome_class?: string;
  retryable?: boolean;
  iteration?: number;
  session_id: string;
  timestamp?: string;
}

export interface ToolCallStartEvent {
  type: "tool_call_start";
  tool_name: string;
  tool_call_id: string;
  arguments: Record<string, unknown>;
  display_name?: string;
  running_text?: string;
  session_id: string;
  iteration?: number;
  timestamp?: string;
}

export interface ToolCallResultEvent {
  type: "tool_call_result";
  tool_name: string;
  tool_call_id: string;
  status: string;
  result: unknown;
  error_info: string | null;
  metadata: Record<string, unknown>;
  display_name?: string;
  success_text?: string;
  failure_prefix?: string;
  attempts?: number;
  outcome_class?: string;
  retryable?: boolean;
  session_id: string;
  compressed_meta?: CompressedMeta;
  attachments?: Attachment[];
  summary?: string;
  duration_ms?: number;
  iteration?: number;
  timestamp?: string;
}

export interface WsErrorEvent {
  type: "error";
  code: string;
  message: string;
  retryable: boolean;
  session_id: string;
  requested_model?: string;
  task_type?: string;
  attempted_chain?: ModelAttempt[];
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
  | PipelineStageEvent
  | ThinkingDeltaEvent
  | ReactIterationEvent
  | ReactCompleteEvent
  | CompressionEvent
  | ModelFallbackEvent
  | ModelFallbackExhaustedEvent
  | ToolCallStartEvent
  | ToolCallResultEvent
  | ToolCallErrorEvent
  | NarrationEvent
  | WsErrorEvent;

export type ConnectionStatus =
  | "connecting"
  | "reconnecting"
  | "connected"
  | "disconnected"
  | "error";

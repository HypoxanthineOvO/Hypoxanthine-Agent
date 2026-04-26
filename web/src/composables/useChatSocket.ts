import { ref } from "vue";
import type { Ref } from "vue";

import type {
  Attachment,
  AssistantChunkEvent,
  AssistantDoneEvent,
  CompressionEvent,
  ConnectionStatus,
  IncomingWsEvent,
  Message,
  ModelFallbackEvent,
  ModelFallbackExhaustedEvent,
  NarrationEvent,
  PipelineProgressItem,
  PipelineStageEvent,
  ReactCompleteEvent,
  ReactIterationEvent,
  ThinkingDeltaEvent,
  ToolCallErrorEvent,
  ToolCallResultEvent,
  ToolCallStartEvent,
  WsErrorEvent,
} from "../types/message";
import { stripLegacySourcePrefix } from "../utils/messageRouting";

interface UseChatSocketOptions {
  url: string;
  token: string;
  sessionId: Ref<string>;
}

interface PendingOutboundMessage {
  text: string;
  attachments: Attachment[];
}

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000] as const;
const PIPELINE_STAGE_DELAY_MS = 800;
const PIPELINE_STAGE_MERGE_WINDOW_MS = 500;
const TOOL_RESULT_SUCCESS_TTL_MS = 1000;
const ASSISTANT_CHUNK_FLUSH_MS = 33;

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
  let assistantChunkBuffer = "";
  let assistantChunkFlushTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let shouldReconnect = true;
  let pendingRetryMessage: PendingOutboundMessage | null = null;
  const lastOutboundBySession = new Map<string, PendingOutboundMessage>();
  const activePipelineIndexBySession = new Map<string, number>();
  const pendingPipelineStageTimerBySession = new Map<string, ReturnType<typeof setTimeout>>();
  const pendingPipelineStageBySession = new Map<string, PipelineStageEvent>();
  const lastPipelineStageAtBySession = new Map<string, number>();
  const transientPipelineItemTimerByKey = new Map<string, ReturnType<typeof setTimeout>>();

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

  const isPipelineStageEvent = (
    payload: IncomingWsEvent,
  ): payload is PipelineStageEvent =>
    "type" in payload && payload.type === "pipeline_stage";

  const isThinkingDeltaEvent = (
    payload: IncomingWsEvent,
  ): payload is ThinkingDeltaEvent =>
    "type" in payload && payload.type === "thinking_delta";

  const isReactIterationEvent = (
    payload: IncomingWsEvent,
  ): payload is ReactIterationEvent =>
    "type" in payload && payload.type === "react_iteration";

  const isReactCompleteEvent = (
    payload: IncomingWsEvent,
  ): payload is ReactCompleteEvent =>
    "type" in payload && payload.type === "react_complete";

  const isCompressionEvent = (
    payload: IncomingWsEvent,
  ): payload is CompressionEvent =>
    "type" in payload && payload.type === "compression";

  const isModelFallbackEvent = (
    payload: IncomingWsEvent,
  ): payload is ModelFallbackEvent =>
    "type" in payload && payload.type === "model_fallback";

  const isModelFallbackExhaustedEvent = (
    payload: IncomingWsEvent,
  ): payload is ModelFallbackExhaustedEvent =>
    "type" in payload && payload.type === "model_fallback_exhausted";

  const isToolCallStartEvent = (
    payload: IncomingWsEvent,
  ): payload is ToolCallStartEvent =>
    "type" in payload && payload.type === "tool_call_start";

  const isToolCallResultEvent = (
    payload: IncomingWsEvent,
  ): payload is ToolCallResultEvent =>
    "type" in payload && payload.type === "tool_call_result";

  const isToolCallErrorEvent = (
    payload: IncomingWsEvent,
  ): payload is ToolCallErrorEvent =>
    "type" in payload && payload.type === "tool_call_error";

  const clearReconnectTimer = (): void => {
    if (reconnectTimer === null) {
      return;
    }
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    reconnectDelayMs.value = null;
  };

  const clearAssistantChunkFlushTimer = (): void => {
    if (assistantChunkFlushTimer === null) {
      return;
    }
    clearTimeout(assistantChunkFlushTimer);
    assistantChunkFlushTimer = null;
  };

  const clearAssistantChunkBuffer = (): void => {
    clearAssistantChunkFlushTimer();
    assistantChunkBuffer = "";
  };

  const flushAssistantChunkBuffer = (): void => {
    clearAssistantChunkFlushTimer();
    if (!assistantChunkBuffer || streamingAssistantIndex === null) {
      return;
    }
    const targetIndex = streamingAssistantIndex;
    if (targetIndex < 0 || targetIndex >= messages.value.length) {
      assistantChunkBuffer = "";
      return;
    }
    const existing = messages.value[targetIndex];
    if (!existing) {
      assistantChunkBuffer = "";
      return;
    }
    existing.text = `${existing.text ?? ""}${assistantChunkBuffer}`;
    existing.metadata = {
      ...(existing.metadata ?? {}),
      streaming: true,
      render_version: `${String(existing.text ?? "").length}:streaming`,
    };
    assistantChunkBuffer = "";
  };

  const scheduleAssistantChunkFlush = (): void => {
    if (assistantChunkFlushTimer !== null) {
      return;
    }
    assistantChunkFlushTimer = setTimeout(flushAssistantChunkBuffer, ASSISTANT_CHUNK_FLUSH_MS);
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

  const reindexActivePipelineMessages = (): void => {
    activePipelineIndexBySession.clear();
    messages.value.forEach((message, index) => {
      if (message.kind !== "pipeline_event" || message.metadata?.pipeline_collapsed === true) {
        return;
      }
      activePipelineIndexBySession.set(message.session_id, index);
    });
  };

  const resolveActivePipelineMessage = (sessionId: string): Message | null => {
    const index = activePipelineIndexBySession.get(sessionId);
    if (index === undefined) {
      return null;
    }
    const existing = messages.value[index];
    if (!existing || existing.kind !== "pipeline_event" || existing.session_id !== sessionId) {
      activePipelineIndexBySession.delete(sessionId);
      return null;
    }
    return existing;
  };

  const findLatestPipelineMessage = (sessionId: string): Message | null => {
    for (let index = messages.value.length - 1; index >= 0; index -= 1) {
      const message = messages.value[index];
      if (message?.kind === "pipeline_event" && message.session_id === sessionId) {
        return message;
      }
    }
    return null;
  };

  const ensurePipelineMessage = (sessionId: string, timestamp?: string): Message => {
    const existing = resolveActivePipelineMessage(sessionId);
    if (existing) {
      return existing;
    }

    const message: Message = {
      kind: "pipeline_event",
      text: "",
      sender: "assistant",
      session_id: sessionId,
      timestamp: timestamp ?? new Date().toISOString(),
      metadata: {
        ephemeral: true,
        pipeline_collapsed: false,
        pipeline_items: [] as PipelineProgressItem[],
      },
    };
    messages.value.push(message);
    activePipelineIndexBySession.set(sessionId, messages.value.length - 1);
    return message;
  };

  const clearPendingPipelineStage = (sessionId: string): void => {
    const timer = pendingPipelineStageTimerBySession.get(sessionId);
    if (timer !== undefined) {
      clearTimeout(timer);
      pendingPipelineStageTimerBySession.delete(sessionId);
    }
    pendingPipelineStageBySession.delete(sessionId);
  };

  const clearAllPendingPipelineStages = (): void => {
    pendingPipelineStageTimerBySession.forEach((timer) => clearTimeout(timer));
    pendingPipelineStageTimerBySession.clear();
    pendingPipelineStageBySession.clear();
    lastPipelineStageAtBySession.clear();
  };

  const transientPipelineItemKey = (sessionId: string, itemKey: string): string =>
    `${sessionId}:${itemKey}`;

  const clearTransientPipelineItemTimer = (sessionId: string, itemKey: string): void => {
    const compositeKey = transientPipelineItemKey(sessionId, itemKey);
    const timer = transientPipelineItemTimerByKey.get(compositeKey);
    if (timer !== undefined) {
      clearTimeout(timer);
      transientPipelineItemTimerByKey.delete(compositeKey);
    }
  };

  const clearAllTransientPipelineItemTimers = (): void => {
    transientPipelineItemTimerByKey.forEach((timer) => clearTimeout(timer));
    transientPipelineItemTimerByKey.clear();
  };

  const updatePipelineMessage = (
    sessionId: string,
    item: PipelineProgressItem,
  ): void => {
    const message = ensurePipelineMessage(sessionId, item.timestamp);
    const metadata = { ...(message.metadata ?? {}) };
    const currentItems = Array.isArray(metadata.pipeline_items)
      ? [...(metadata.pipeline_items as PipelineProgressItem[])]
      : [];
    let nextItems = [...currentItems];
    if (item.event_type === "pipeline_stage") {
      nextItems = nextItems.filter((existing) => existing.event_type !== "pipeline_stage");
      nextItems.push(item);
    } else if (item.event_type === "react_iteration") {
      nextItems = nextItems.filter(
        (existing) =>
          existing.event_type !== "pipeline_stage" && existing.event_type !== "react_iteration",
      );
      nextItems.push(item);
    } else if (item.key) {
      const index = nextItems.findIndex((existing) => existing.key === item.key);
      if (index >= 0) {
        nextItems[index] = item;
      } else if (item.event_type === "tool_call_start") {
        nextItems = nextItems.filter((existing) => existing.event_type !== "pipeline_stage");
        nextItems.push(item);
      }
    } else {
      nextItems.push(item);
    }
    metadata.pipeline_items = nextItems.slice(-6);
    metadata.pipeline_collapsed = false;
    metadata.ephemeral = true;
    message.metadata = metadata;
    message.text = item.text;
    message.timestamp = message.timestamp ?? item.timestamp ?? new Date().toISOString();
  };

  const removePipelineItem = (sessionId: string, itemKey: string): void => {
    const message = findLatestPipelineMessage(sessionId);
    if (!message) {
      return;
    }
    const metadata = { ...(message.metadata ?? {}) };
    const currentItems = Array.isArray(metadata.pipeline_items)
      ? [...(metadata.pipeline_items as PipelineProgressItem[])]
      : [];
    const nextItems = currentItems.filter((item) => item.key !== itemKey);
    if (nextItems.length === currentItems.length) {
      return;
    }
    if (nextItems.length === 0) {
      messages.value = messages.value.filter((item) => item !== message);
      reindexActivePipelineMessages();
      return;
    }
    metadata.pipeline_items = nextItems;
    message.metadata = metadata;
    message.text = nextItems.at(-1)?.text ?? "";
  };

  const scheduleTransientPipelineItemRemoval = (sessionId: string, itemKey: string): void => {
    clearTransientPipelineItemTimer(sessionId, itemKey);
    const compositeKey = transientPipelineItemKey(sessionId, itemKey);
    const timer = setTimeout(() => {
      transientPipelineItemTimerByKey.delete(compositeKey);
      removePipelineItem(sessionId, itemKey);
    }, TOOL_RESULT_SUCCESS_TTL_MS);
    transientPipelineItemTimerByKey.set(compositeKey, timer);
  };

  const schedulePipelineStageDisplay = (payload: PipelineStageEvent): void => {
    if (payload.stage === "preprocessing") {
      return;
    }
    const now = Date.now();
    const previousAt = lastPipelineStageAtBySession.get(payload.session_id);
    const isBurst =
      previousAt !== undefined &&
      now - previousAt <= PIPELINE_STAGE_MERGE_WINDOW_MS;
    lastPipelineStageAtBySession.set(payload.session_id, now);
    if (isBurst || pendingPipelineStageTimerBySession.has(payload.session_id)) {
      clearPendingPipelineStage(payload.session_id);
    }
    pendingPipelineStageBySession.set(payload.session_id, payload);
    const timer = setTimeout(() => {
      const pending = pendingPipelineStageBySession.get(payload.session_id);
      pendingPipelineStageBySession.delete(payload.session_id);
      if (!pending || pending !== payload) {
        return;
      }
      pendingPipelineStageBySession.delete(payload.session_id);
      const item = buildProgressItem(payload);
      if (item) {
        updatePipelineMessage(payload.session_id, item);
      }
    }, PIPELINE_STAGE_DELAY_MS);
    pendingPipelineStageTimerBySession.set(payload.session_id, timer);
  };

  const collapsePipelineMessage = (sessionId: string, timestamp?: string): void => {
    const message = resolveActivePipelineMessage(sessionId);
    if (!message) {
      return;
    }
    message.metadata = {
      ...(message.metadata ?? {}),
      pipeline_collapsed: true,
      ephemeral: true,
      pipeline_completed_at: timestamp ?? new Date().toISOString(),
    };
    activePipelineIndexBySession.delete(sessionId);
  };

  const stageText = (payload: PipelineStageEvent): string =>
    payload.detail?.trim() || payload.model?.trim() || payload.stage.trim();

  const toolResultText = (payload: ToolCallResultEvent): string => {
    const summary = payload.summary?.trim();
    if (summary) {
      return summary;
    }
    if (payload.status === "success") {
      return `${payload.tool_name} 已完成`;
    }
    return `${payload.tool_name} 调用失败`;
  };

  const buildProgressItem = (payload: IncomingWsEvent): PipelineProgressItem | null => {
    if (isPipelineStageEvent(payload)) {
      if (payload.stage === "preprocessing") {
        return null;
      }
      return {
        event_type: payload.type,
        text: stageText(payload),
        timestamp: payload.timestamp,
        stage: payload.stage,
        status: "running",
      };
    }

    if (isThinkingDeltaEvent(payload)) {
      return {
        event_type: payload.type,
        text: payload.content,
        timestamp: payload.timestamp,
        status: "running",
      };
    }

    if (isReactIterationEvent(payload)) {
      return {
        event_type: payload.type,
        text: `推理中 (${payload.iteration}/${payload.max_iterations})`,
        timestamp: payload.timestamp,
        status: "running",
      };
    }

    if (isReactCompleteEvent(payload)) {
      return {
        event_type: payload.type,
        text: `推理完成，共 ${payload.total_iterations} 轮，调用 ${payload.total_tool_calls} 次工具`,
        timestamp: payload.timestamp,
        status: "success",
      };
    }

    if (isCompressionEvent(payload)) {
      return {
        event_type: payload.type,
        text: `压缩工具输出 ${payload.original_chars} -> ${payload.compressed_chars} chars`,
        timestamp: payload.timestamp,
        status: "info",
        tool: payload.tool,
      };
    }

    if (isModelFallbackEvent(payload)) {
      return {
        event_type: payload.type,
        text: `⚠️ ${payload.failed_model} 不可用（${payload.reason}），已切换到 ${payload.fallback_model}`,
        timestamp: payload.timestamp,
        status: "warning",
      };
    }

    if (isModelFallbackExhaustedEvent(payload)) {
      return {
        event_type: payload.type,
        text: "❌ 所有模型均不可用，请稍后再试",
        timestamp: payload.timestamp,
        status: "error",
      };
    }

    if (isToolCallStartEvent(payload)) {
      return {
        event_type: payload.type,
        text: `正在调用 ${payload.tool_name}`,
        timestamp: payload.timestamp,
        status: "running",
        tool: payload.tool_name,
        key: `tool:${payload.tool_call_id}`,
      };
    }

    if (isToolCallResultEvent(payload)) {
      return {
        event_type: payload.type,
        text: toolResultText(payload),
        timestamp: payload.timestamp,
        status: payload.status === "success" ? "success" : "error",
        tool: payload.tool_name,
        key: `tool:${payload.tool_call_id}`,
      };
    }

    if (isToolCallErrorEvent(payload)) {
      return {
        event_type: payload.type,
        text: payload.will_retry
          ? `${payload.tool} 调用失败，正在重试`
          : `${payload.tool} 调用失败：${payload.error}`,
        timestamp: payload.timestamp,
        status: payload.will_retry ? "running" : "error",
        tool: payload.tool,
      };
    }

    return null;
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
      if (pendingRetryMessage) {
        const next = pendingRetryMessage;
        pendingRetryMessage = null;
        sendMessage(next.text, next.attachments);
      }
    };

    socket.onclose = () => {
      flushAssistantChunkBuffer();
      streamingAssistantIndex = null;
      clearAssistantChunkBuffer();
      clearAllPendingPipelineStages();
      clearAllTransientPipelineItemTimers();
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
          messages.value.push({
            kind: "error",
            sender: "assistant",
            session_id: payload.session_id,
            timestamp: new Date().toISOString(),
            text: payload.message,
            error_info: payload.message,
            metadata: {
              error_card: true,
              retryable: payload.retryable,
              error_code: payload.code,
              error_detail: payload.message,
            },
          });
          status.value = "error";
          return;
        }

        if (isNarrationEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          messages.value.push({
            kind: "narration",
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

        const progressItem = buildProgressItem(payload);
        if ("session_id" in payload && payload.session_id === options.sessionId.value) {
          if (isPipelineStageEvent(payload)) {
            schedulePipelineStageDisplay(payload);
          } else if (
            progressItem &&
            (
              isThinkingDeltaEvent(payload) ||
              isReactIterationEvent(payload) ||
              isReactCompleteEvent(payload) ||
              isCompressionEvent(payload) ||
              isModelFallbackEvent(payload) ||
              isModelFallbackExhaustedEvent(payload) ||
              isToolCallErrorEvent(payload)
            )
          ) {
            clearPendingPipelineStage(payload.session_id);
            updatePipelineMessage(payload.session_id, progressItem);
          }
        }

        if (isAssistantChunkEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          clearPendingPipelineStage(payload.session_id);

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
              kind: "text",
              text: "",
              sender: "assistant",
              session_id: payload.session_id,
              timestamp: payload.timestamp,
              metadata: {
                streaming: true,
                render_key: `assistant-stream-${payload.session_id}-${Date.now()}`,
                render_version: "0:streaming",
              },
            });
            streamingAssistantIndex = messages.value.length - 1;
          }

          assistantChunkBuffer += chunkText;
          const targetIndex = streamingAssistantIndex;
          if (targetIndex !== null) {
            const existing = messages.value[targetIndex];
            if (existing) {
              existing.timestamp = existing.timestamp ?? payload.timestamp;
              existing.metadata = {
                ...(existing.metadata ?? {}),
                streaming: true,
              };
            }
          }
          scheduleAssistantChunkFlush();
          return;
        }

        if (isAssistantDoneEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          clearPendingPipelineStage(payload.session_id);
          flushAssistantChunkBuffer();
          if (
            streamingAssistantIndex !== null &&
            streamingAssistantIndex >= 0 &&
            streamingAssistantIndex < messages.value.length
          ) {
            const existing = messages.value[streamingAssistantIndex];
            if (existing) {
              existing.timestamp = existing.timestamp ?? payload.timestamp;
              existing.metadata = {
                ...(existing.metadata ?? {}),
                streaming: false,
                render_version: `${String(existing.text ?? "").length}:final`,
              };
              if (Array.isArray(payload.attachments) && payload.attachments.length > 0) {
                existing.attachments = payload.attachments.map((attachment) => ({ ...attachment }));
              }
            }
          }
          collapsePipelineMessage(payload.session_id, payload.timestamp);
          streamingAssistantIndex = null;
          return;
        }

        if (isToolCallStartEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          clearPendingPipelineStage(payload.session_id);
          const progress = buildProgressItem(payload);
          if (progress) {
            updatePipelineMessage(payload.session_id, progress);
          }
          messages.value.push({
            kind: "tool_call",
            sender: "assistant",
            session_id: payload.session_id,
            event_type: "tool_call_start",
            tool_name: payload.tool_name,
            tool_call_id: payload.tool_call_id,
            arguments: payload.arguments,
            metadata: payload.iteration !== undefined ? { iteration: payload.iteration } : undefined,
            timestamp: payload.timestamp,
          });
          return;
        }

        if (isToolCallResultEvent(payload)) {
          if (payload.session_id !== options.sessionId.value) {
            return;
          }
          clearPendingPipelineStage(payload.session_id);
          const progress = buildProgressItem(payload);
          if (progress) {
            updatePipelineMessage(payload.session_id, progress);
            if (payload.status === "success" && progress.key) {
              scheduleTransientPipelineItemRemoval(payload.session_id, progress.key);
            }
          }
          messages.value.push({
            kind: "tool_call",
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
            timestamp: payload.timestamp,
          });
          return;
        }

        if (
          isPipelineStageEvent(payload) ||
          isThinkingDeltaEvent(payload) ||
          isReactIterationEvent(payload) ||
          isReactCompleteEvent(payload) ||
          isCompressionEvent(payload) ||
          isModelFallbackEvent(payload) ||
          isModelFallbackExhaustedEvent(payload) ||
          isToolCallErrorEvent(payload)
        ) {
          return;
        }

        if (!isMessage(payload) || !payload.sender || !payload.session_id) {
          return;
        }
        if (payload.session_id !== options.sessionId.value) {
          return;
        }
        if (
          payload.message_tag === "tool_status" &&
          resolveActivePipelineMessage(payload.session_id) !== null
        ) {
          return;
        }
        messages.value.push({
          ...stripLegacySourcePrefix(payload),
          kind:
            payload.kind ??
            (payload.metadata?.error_card === true
              ? "error"
              : payload.message_tag === "narration"
                ? "narration"
                : payload.event_type
                  ? "tool_call"
                  : "text"),
          error_info: payload.error_info ?? null,
        });
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
    flushAssistantChunkBuffer();
    clearAssistantChunkBuffer();
    clearAllPendingPipelineStages();
    clearAllTransientPipelineItemTimers();
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
      kind: "text",
      text: trimmed || null,
      attachments: normalizedAttachments,
      sender: "user",
      session_id: options.sessionId.value,
      timestamp: new Date().toISOString(),
    };
    lastOutboundBySession.set(options.sessionId.value, {
      text: trimmed,
      attachments: normalizedAttachments,
    });
    messages.value = messages.value.filter(
      (item) => !(item.metadata?.error_card === true && item.session_id === options.sessionId.value),
    );
    reindexActivePipelineMessages();
    socket.send(JSON.stringify(message));
    messages.value.push(message);
    return true;
  };

  const sendText = (text: string): boolean => sendMessage(text);

  const replaceMessages = (nextMessages: Message[]): void => {
    messages.value = nextMessages.map((item) => stripLegacySourcePrefix({ ...item }));
    streamingAssistantIndex = null;
    clearAssistantChunkBuffer();
    clearAllPendingPipelineStages();
    clearAllTransientPipelineItemTimers();
    reindexActivePipelineMessages();
  };

  const retryLastMessage = (): boolean => {
    const previous = lastOutboundBySession.get(options.sessionId.value);
    if (!previous) {
      return false;
    }
    if (socket?.readyState === WebSocket.OPEN) {
      return sendMessage(previous.text, previous.attachments);
    }
    pendingRetryMessage = {
      text: previous.text,
      attachments: previous.attachments.map((attachment) => ({ ...attachment })),
    };
    connect();
    return true;
  };

  return {
    connect,
    disconnect,
    lastError,
    messages,
    reconnectDelayMs,
    reconnectNow,
    replaceMessages,
    retryLastMessage,
    sendMessage,
    sendText,
    status,
  };
}

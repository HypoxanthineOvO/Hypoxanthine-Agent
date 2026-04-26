import type { Message } from "@/types/message";
import { toTimestampMs } from "@/utils/timeFormat";
import { normalizeTimestamp, parseCompressedMeta, parseJsonObject } from "@/utils/jsonParsers";
import { makeApiUrl, stripLegacySourcePrefix, withApiToken } from "@/utils/messageRouting";

export interface ToolInvocationRow {
  id: number;
  session_id: string;
  tool_name: string;
  skill_name?: string | null;
  params_json?: string | null;
  status: string;
  result_summary?: string | null;
  duration_ms?: number | null;
  error_info?: string | null;
  compressed_meta_json?: string | null;
  created_at: string;
}

export interface CoderTaskRow {
  id?: number;
  task_id: string;
  session_id: string;
  working_directory: string;
  prompt_summary?: string | null;
  model?: string | null;
  status: string;
  attached?: number | boolean;
  done?: number | boolean;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface LoadSessionMessagesOptions {
  apiBase: string;
  token: string;
  sessionId: string;
  fetchImpl?: typeof fetch;
}

const toTimelineValue = (value: string | null | undefined): number => {
  const epoch = toTimestampMs(normalizeTimestamp(value));
  return epoch ?? Number.POSITIVE_INFINITY;
};

export function toToolInvocationMessages(rows: ToolInvocationRow[]): Message[] {
  const messagesFromRows: Message[] = [];

  for (const row of rows) {
    const toolCallId = `inv_${row.id}`;
    const params = parseJsonObject(row.params_json);
    const compressedMeta = parseCompressedMeta(row.compressed_meta_json);
    const timestamp = normalizeTimestamp(row.created_at);

    messagesFromRows.push({
      kind: "tool_call",
      sender: "assistant",
      session_id: row.session_id,
      timestamp,
      event_type: "tool_call_start",
      tool_name: row.tool_name,
      tool_call_id: toolCallId,
      arguments: params,
      metadata: { ephemeral: true },
    });
    messagesFromRows.push({
      kind: "tool_call",
      sender: "assistant",
      session_id: row.session_id,
      timestamp,
      event_type: "tool_call_result",
      tool_name: row.tool_name,
      tool_call_id: toolCallId,
      status: row.status,
      result: row.result_summary ?? "",
      error_info: row.error_info ?? null,
      metadata: { ephemeral: true },
      compressed_meta: compressedMeta,
    });
  }

  return messagesFromRows;
}

export function mergeTimelineMessages(history: Message[], invocations: Message[]): Message[] {
  return [...history, ...invocations]
    .map((message, index) => {
      const sortPhase =
        message.event_type === "tool_call_start"
          ? 1
          : message.event_type === "tool_call_result"
            ? 2
            : 0;

      return {
        message: stripLegacySourcePrefix({
          ...message,
          kind:
            message.kind ??
            (message.event_type
              ? "tool_call"
              : message.metadata?.error_card === true
                ? "error"
                : message.message_tag === "narration"
                  ? "narration"
                  : "text"),
          timestamp: normalizeTimestamp(message.timestamp),
          error_info: message.error_info ?? null,
        }),
        timestamp: toTimelineValue(message.timestamp),
        sortPhase,
        index,
      };
    })
    .sort((left, right) => {
      if (left.timestamp !== right.timestamp) {
        return left.timestamp - right.timestamp;
      }
      if (left.sortPhase !== right.sortPhase) {
        return left.sortPhase - right.sortPhase;
      }
      return left.index - right.index;
    })
    .map((item) => item.message);
}

export async function loadSessionMessages(
  options: LoadSessionMessagesOptions,
): Promise<Message[]> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const messagesUrl = withApiToken(
    makeApiUrl(`sessions/${encodeURIComponent(options.sessionId)}/messages`, options.apiBase),
    options.token,
  );
  const invocationsUrl = withApiToken(
    makeApiUrl(
      `sessions/${encodeURIComponent(options.sessionId)}/tool-invocations`,
      options.apiBase,
    ),
    options.token,
  );

  const [messagesResponse, invocationsResponse] = await Promise.all([
    fetchImpl(messagesUrl),
    fetchImpl(invocationsUrl),
  ]);

  if (!messagesResponse.ok || !invocationsResponse.ok) {
    return [];
  }

  const history = (await messagesResponse.json()) as Message[];
  const invocations = (await invocationsResponse.json()) as ToolInvocationRow[];
  return mergeTimelineMessages(history, toToolInvocationMessages(invocations));
}

export async function loadSessionCoderTasks(
  options: LoadSessionMessagesOptions,
): Promise<CoderTaskRow[]> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const tasksUrl = withApiToken(
    makeApiUrl(`sessions/${encodeURIComponent(options.sessionId)}/coder-tasks`, options.apiBase),
    options.token,
  );
  try {
    const response = await fetchImpl(tasksUrl);
    if (!response.ok) {
      return [];
    }
    const rows = (await response.json()) as CoderTaskRow[];
    return Array.isArray(rows) ? rows : [];
  } catch {
    return [];
  }
}

<script setup lang="ts">
import { useNotification } from "naive-ui";
import { computed, h, nextTick, onMounted, ref, watch } from "vue";

import CompressedMessage from "../components/chat/CompressedMessage.vue";
import FileAttachment from "../components/chat/FileAttachment.vue";
import MarkdownPreview from "../components/chat/MarkdownPreview.vue";
import MediaMessage from "../components/chat/MediaMessage.vue";
import MessageBubble from "../components/chat/MessageBubble.vue";
import TextMessage from "../components/chat/TextMessage.vue";
import ToolCallMessage from "../components/chat/ToolCallMessage.vue";
import ConnectionStatus from "../components/ConnectionStatus.vue";
import ReconnectBanner from "../components/layout/ReconnectBanner.vue";
import { useHotkey } from "../composables/useHotkey";
import { useChatSocket } from "../composables/useChatSocket";
import type { Message, SessionSummary } from "../types/message";

const props = withDefaults(
  defineProps<{
    wsUrl: string;
    token: string;
    sessionId?: string;
    apiBase?: string;
  }>(),
  {
    sessionId: "session-1",
    apiBase: "",
  },
);

const draft = ref("");
const composerExpanded = ref(false);
const composerRef = ref<HTMLTextAreaElement | null>(null);
const messagesRef = ref<HTMLElement | null>(null);
const sessions = ref<SessionSummary[]>([]);
const activeSessionId = ref(props.sessionId);

const normalizedApiBase = computed(() => {
  const explicitBase = props.apiBase.trim();
  if (explicitBase) {
    return explicitBase.replace(/\/+$/, "");
  }

  try {
    const parsed = new URL(props.wsUrl);
    const httpProtocol = parsed.protocol === "wss:" ? "https:" : "http:";
    return `${httpProtocol}//${parsed.host}/api`;
  } catch {
    return "/api";
  }
});

const {
  connect,
  disconnect,
  lastError,
  messages,
  reconnectDelayMs,
  reconnectNow,
  replaceMessages,
  sendText,
  status,
} =
  useChatSocket({
  url: props.wsUrl,
  token: props.token,
  sessionId: activeSessionId,
});

const notification = (() => {
  try {
    return useNotification();
  } catch {
    return null;
  }
})();

const canSend = computed(
  () => status.value === "connected" && draft.value.trim().length > 0,
);
const showReconnectBanner = computed(
  () => status.value === "reconnecting" || reconnectDelayMs.value !== null,
);

const makeApiUrl = (path: string): string =>
  `${normalizedApiBase.value}/${path.replace(/^\/+/, "")}`;

const withApiToken = (url: string): string => {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(props.token)}`;
};

interface ToolInvocationRow {
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

const parseJsonObject = (value: string | null | undefined): Record<string, unknown> => {
  if (!value) {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Keep fallback below.
  }
  return {};
};

const parseCompressedMeta = (
  value: string | null | undefined,
): Message["compressed_meta"] | undefined => {
  if (!value) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(value);
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Message["compressed_meta"];
    }
  } catch {
    // Keep fallback below.
  }
  return undefined;
};

const toTimelineValue = (value: string | undefined): number => {
  if (!value) {
    return Number.POSITIVE_INFINITY;
  }
  const normalized = value.trim();

  // SQLite datetime('now') commonly uses "YYYY-MM-DD HH:MM:SS" (UTC, no timezone).
  // Normalize it to ISO UTC to avoid locale-dependent parsing shifts.
  const sqliteUtcPattern = /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(\.\d+)?$/;
  const candidate = sqliteUtcPattern.test(normalized)
    ? `${normalized.replace(/\s+/, "T")}Z`
    : normalized;

  const epoch = Date.parse(candidate);
  return Number.isNaN(epoch) ? Number.POSITIVE_INFINITY : epoch;
};

const toToolInvocationMessages = (
  rows: ToolInvocationRow[],
): Message[] => {
  const messagesFromRows: Message[] = [];
  for (const row of rows) {
    const toolCallId = `inv_${row.id}`;
    const params = parseJsonObject(row.params_json);
    const compressedMeta = parseCompressedMeta(row.compressed_meta_json);

    messagesFromRows.push({
      sender: "assistant",
      session_id: row.session_id,
      timestamp: row.created_at,
      event_type: "tool_call_start",
      tool_name: row.tool_name,
      tool_call_id: toolCallId,
      arguments: params,
    });
    messagesFromRows.push({
      sender: "assistant",
      session_id: row.session_id,
      timestamp: row.created_at,
      event_type: "tool_call_result",
      tool_name: row.tool_name,
      tool_call_id: toolCallId,
      status: row.status,
      result: row.result_summary ?? "",
      error_info: row.error_info ?? "",
      metadata: {},
      compressed_meta: compressedMeta,
    });
  }
  return messagesFromRows;
};

const ensureSessionPresent = (sessionId: string): void => {
  const existingIndex = sessions.value.findIndex(
    (item) => item.session_id === sessionId,
  );
  const now = new Date().toISOString();
  if (existingIndex === -1) {
    sessions.value.unshift({
      session_id: sessionId,
      created_at: now,
      updated_at: now,
      message_count: messages.value.length,
    });
    return;
  }

  const existing = sessions.value[existingIndex];
  if (!existing) {
    return;
  }
  existing.updated_at = now;
  existing.message_count = Math.max(existing.message_count + 1, messages.value.length);
  sessions.value = [...sessions.value].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at),
  );
};

const loadSessionMessages = async (sessionId: string): Promise<void> => {
  const messagesUrl = withApiToken(
    makeApiUrl(`sessions/${encodeURIComponent(sessionId)}/messages`),
  );
  const invocationsUrl = withApiToken(
    makeApiUrl(`sessions/${encodeURIComponent(sessionId)}/tool-invocations`),
  );
  const [messagesResponse, invocationsResponse] = await Promise.all([
    fetch(messagesUrl),
    fetch(invocationsUrl),
  ]);
  if (!messagesResponse.ok || !invocationsResponse.ok) {
    replaceMessages([]);
    return;
  }
  const history = (await messagesResponse.json()) as Message[];
  const invocations = (await invocationsResponse.json()) as ToolInvocationRow[];
  const invocationMessages = toToolInvocationMessages(invocations);

  const timeline = [...history, ...invocationMessages]
    .map((message, index) => {
      const sortPhase =
        message.event_type === "tool_call_start"
          ? 1
          : message.event_type === "tool_call_result"
            ? 2
            : 0;
      return {
        message,
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

  replaceMessages(timeline);
};

const loadSessions = async (): Promise<void> => {
  const response = await fetch(withApiToken(makeApiUrl("sessions")));
  if (!response.ok) {
    sessions.value = [];
    replaceMessages([]);
    return;
  }

  const payload = (await response.json()) as SessionSummary[];
  if (payload.length === 0) {
    sessions.value = [
      {
        session_id: activeSessionId.value,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        message_count: 0,
      },
    ];
    replaceMessages([]);
    return;
  }

  sessions.value = payload;
  const firstSession = payload[0];
  if (!firstSession) {
    replaceMessages([]);
    return;
  }
  activeSessionId.value = firstSession.session_id;
  await loadSessionMessages(activeSessionId.value);
};

const selectSession = async (sessionId: string): Promise<void> => {
  if (activeSessionId.value === sessionId) {
    return;
  }
  activeSessionId.value = sessionId;
  await loadSessionMessages(sessionId);
};

const newSession = (): void => {
  const sessionId = `session-${Date.now()}`;
  activeSessionId.value = sessionId;
  sessions.value.unshift({
    session_id: sessionId,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    message_count: 0,
  });
  replaceMessages([]);
};

const clearCurrentConversation = (): void => {
  replaceMessages([]);
  const existing = sessions.value.find((item) => item.session_id === activeSessionId.value);
  if (existing) {
    existing.message_count = 0;
    existing.updated_at = new Date().toISOString();
  }
};

const adjustComposerHeight = (): void => {
  const input = composerRef.value;
  if (!input) {
    return;
  }
  input.style.height = "auto";
  const maxHeight = 200;
  const nextHeight = Math.min(input.scrollHeight, maxHeight);
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
};

const toggleComposerExpanded = (): void => {
  composerExpanded.value = !composerExpanded.value;
  void nextTick(() => {
    adjustComposerHeight();
    composerRef.value?.focus();
  });
};

const hasMarkdownPreview = (message: Message): boolean =>
  Boolean(
    message.file?.toLowerCase().endsWith(".md") &&
      typeof message.text === "string" &&
      message.text.length > 0,
  );

const codeFilePreviewExt = /\.(py|ya?ml|json|ts|js|sh|toml|ini)$/i;

const resolveAssetUrl = (rawPath: string | null | undefined): string => {
  if (!rawPath) {
    return "";
  }
  if (/^https?:\/\//i.test(rawPath)) {
    return rawPath;
  }
  return makeApiUrl(
    `files?path=${encodeURIComponent(rawPath)}&token=${encodeURIComponent(props.token)}`,
  );
};

const mediaSource = (message: Message): string =>
  resolveAssetUrl(message.image ?? message.file ?? "");

const hasMedia = (message: Message): boolean => {
  const source = message.image ?? message.file ?? "";
  return /\.(png|jpe?g|gif|svg|webp|mp4|webm)$/i.test(source);
};

const hasCodeFilePreview = (message: Message): boolean =>
  Boolean(
    message.file &&
      codeFilePreviewExt.test(message.file) &&
      typeof message.text === "string" &&
      message.text.length > 0,
  );

const hasFileAttachment = (message: Message): boolean =>
  Boolean(
    message.file &&
      !hasMarkdownPreview(message) &&
      !hasMedia(message) &&
      !hasCodeFilePreview(message),
  );

const isToolCall = (message: Message): boolean =>
  message.event_type === "tool_call_result";

const isEphemeralToolResult = (message: Message): boolean =>
  message.event_type === "tool_call_result" &&
  message.metadata?.ephemeral === true;

const isHiddenSystemToolEvent = (message: Message): boolean =>
  message.event_type === "tool_call_start" || isEphemeralToolResult(message);

const displayedMessages = computed(() =>
  messages.value.filter((message) => !isHiddenSystemToolEvent(message)),
);

const isCompressedToolResult = (message: Message): boolean =>
  message.event_type === "tool_call_result" &&
  Boolean(message.compressed_meta) &&
  typeof message.result === "string";

const resolveCompressedFilePath = (message: Message): string => {
  const params = message.arguments;
  if (params && typeof params.path === "string") {
    return params.path;
  }
  return "";
};

const onSubmit = (): void => {
  if (!sendText(draft.value)) {
    return;
  }
  ensureSessionPresent(activeSessionId.value);
  draft.value = "";
  void nextTick(() => {
    adjustComposerHeight();
  });
};

const scrollToBottom = (): void => {
  const element = messagesRef.value;
  if (!element) {
    return;
  }
  element.scrollTop = element.scrollHeight;
};

onMounted(() => {
  void loadSessions();
  connect();
  void nextTick(() => {
    adjustComposerHeight();
  });
});

watch(draft, () => {
  adjustComposerHeight();
});

watch(
  () => messages.value.length,
  () => {
    void nextTick(() => {
      scrollToBottom();
    });
  },
);

watch(
  () => {
    const last = messages.value[messages.value.length - 1];
    return last?.text?.length ?? 0;
  },
  () => {
    void nextTick(() => {
      scrollToBottom();
    });
  },
);

watch(lastError, (error) => {
  if (!error || error.session_id !== activeSessionId.value || notification === null) {
    return;
  }

  notification.error({
    title: `连接异常 · ${error.code}`,
    content: error.message,
    duration: 4000,
    action: error.retryable
      ? () =>
          h(
            "button",
            {
              class: "retry-action",
              onClick: () => reconnectNow(),
              type: "button",
            },
            "立即重试",
          )
      : undefined,
  });
});

useHotkey([
  {
    combo: "ctrlOrMeta+enter",
    handler: () => {
      onSubmit();
    },
  },
  {
    combo: "escape",
    handler: () => {
      if (composerExpanded.value) {
        composerExpanded.value = false;
        return;
      }
      window.dispatchEvent(new Event("hypo:sidebar-collapse"));
    },
  },
  {
    combo: "ctrlOrMeta+l",
    handler: () => {
      clearCurrentConversation();
    },
  },
  {
    combo: "ctrlOrMeta+n",
    handler: () => {
      newSession();
    },
  },
  {
    combo: "ctrlOrMeta+d",
    handler: () => {
      window.dispatchEvent(new Event("hypo:theme-toggle"));
    },
  },
  {
    combo: "ctrlOrMeta+k",
    handler: () => {
      // Reserved for M7b command palette.
    },
  },
]);
</script>

<template>
  <section class="chat-shell">
    <aside class="session-sidebar">
      <header class="session-header">
        <p class="eyebrow">Sessions</p>
        <button
          type="button"
          class="ghost-button"
          data-testid="new-session-button"
          @click="newSession"
        >
          New Chat
        </button>
      </header>
      <div class="session-list">
        <button
          v-for="session in sessions"
          :key="session.session_id"
          type="button"
          class="session-item"
          :data-active="session.session_id === activeSessionId"
          :data-testid="`session-item-${session.session_id}`"
          @click="selectSession(session.session_id)"
        >
          <span class="session-name">{{ session.session_id }}</span>
          <span class="session-meta">{{ session.message_count }} msgs</span>
        </button>
      </div>
    </aside>

    <div class="chat-main">
      <header class="chat-header">
        <div class="title-wrap">
          <p class="eyebrow">Hypo-Agent</p>
          <h1>Gateway LLM Console</h1>
        </div>
        <div class="status-wrap">
          <ConnectionStatus :status="status" />
          <button
            type="button"
            class="ghost-button"
            data-testid="connect-button"
            @click="connect"
          >
            Connect
          </button>
          <button type="button" class="ghost-button" @click="disconnect">
            Disconnect
          </button>
        </div>
      </header>

      <ReconnectBanner
        :visible="showReconnectBanner"
        :retry-after-ms="reconnectDelayMs"
        @retry="reconnectNow"
      />

      <main ref="messagesRef" class="messages" aria-live="polite">
        <MessageBubble
          v-for="(message, index) in displayedMessages"
          :key="`${message.session_id}-${message.sender}-${index}`"
          :message="message"
        >
          <CompressedMessage
            v-if="isCompressedToolResult(message)"
            :summary="String(message.result ?? '')"
            :compressed-meta="message.compressed_meta"
            :api-base="normalizedApiBase"
            :token="token"
            :tool-name="message.tool_name"
            :file-path="resolveCompressedFilePath(message)"
          />
          <ToolCallMessage
            v-else-if="isToolCall(message)"
            :tool-name="message.tool_name ?? ''"
            :status="message.status"
            :params="message.arguments"
            :result="message.result"
          />
          <MarkdownPreview
            v-else-if="hasMarkdownPreview(message)"
            :content="message.text ?? ''"
          />
          <MediaMessage
            v-else-if="hasMedia(message)"
            :src="mediaSource(message)"
          />
          <FileAttachment
            v-else-if="hasCodeFilePreview(message)"
            :path="message.file ?? ''"
            :content="message.text ?? ''"
          />
          <FileAttachment
            v-else-if="hasFileAttachment(message)"
            :path="resolveAssetUrl(message.file)"
          />
          <TextMessage
            v-else
            :text="message.text ?? ''"
          />
        </MessageBubble>
        <p v-if="displayedMessages.length === 0" class="empty-tip">
          No messages yet. Connect and send your first line.
        </p>
      </main>

      <form class="composer" :data-expanded="composerExpanded" @submit.prevent="onSubmit">
        <textarea
          ref="composerRef"
          v-model="draft"
          name="message"
          autocomplete="off"
          placeholder="输入消息（Ctrl/Cmd+Enter 发送，Enter 换行）"
          class="composer-input"
        />
        <div class="composer-actions">
          <button type="button" class="ghost-button" @click="toggleComposerExpanded">
            {{ composerExpanded ? "收起输入框" : "展开输入框" }}
          </button>
          <button type="submit" :disabled="!canSend">Send</button>
        </div>
      </form>
    </div>
  </section>
</template>

<style scoped>
.chat-shell {
  backdrop-filter: blur(6px);
  background:
    radial-gradient(120% 120% at 0% 0%, color-mix(in srgb, var(--brand) 22%, transparent), transparent 52%),
    radial-gradient(130% 130% at 100% 100%, color-mix(in srgb, var(--brand-2) 18%, transparent), transparent 60%),
    var(--panel);
  border: 1px solid var(--panel-edge);
  border-radius: 1.35rem;
  box-shadow: 0 24px 60px color-mix(in srgb, black 50%, transparent);
  display: grid;
  gap: 1rem;
  grid-template-columns: 260px 1fr;
  height: 100%;
  margin: 0 auto;
  min-height: 0;
  max-width: 1040px;
  padding: 1.2rem;
}

.session-sidebar {
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 0.95rem;
  display: grid;
  gap: 0.75rem;
  grid-template-rows: auto 1fr;
  min-height: 0;
  padding: 0.8rem;
}

.session-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.session-list {
  display: grid;
  gap: 0.5rem;
  min-height: 0;
  overflow-y: auto;
}

.session-item {
  align-items: baseline;
  background: color-mix(in srgb, var(--surface) 76%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 0.7rem;
  color: var(--text);
  cursor: pointer;
  display: grid;
  gap: 0.1rem;
  justify-items: start;
  padding: 0.55rem 0.65rem;
  text-align: left;
}

.session-item[data-active="true"] {
  border-color: color-mix(in srgb, var(--brand) 66%, var(--panel-edge));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--brand-2) 35%, transparent);
}

.session-name {
  font-size: 0.9rem;
  font-weight: 600;
}

.session-meta {
  color: var(--muted);
  font-size: 0.75rem;
}

.chat-main {
  display: grid;
  gap: 1rem;
  grid-template-rows: auto auto 1fr auto;
  min-height: 0;
}

.chat-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}

.title-wrap h1 {
  font-size: clamp(1.2rem, 2.2vw, 1.7rem);
  letter-spacing: 0.02em;
  margin: 0;
}

.eyebrow {
  color: var(--muted);
  font-size: 0.82rem;
  letter-spacing: 0.12em;
  margin: 0 0 0.25rem;
  text-transform: uppercase;
}

.status-wrap {
  align-items: center;
  display: flex;
  gap: 0.5rem;
}

.ghost-button {
  background: transparent;
  border: 1px solid var(--panel-edge);
  border-radius: 0.7rem;
  color: var(--text);
  cursor: pointer;
  font-weight: 600;
  padding: 0.45rem 0.75rem;
}

.ghost-button:hover {
  border-color: color-mix(in srgb, var(--brand-2) 50%, var(--panel-edge));
  transform: translateY(-1px);
}

.retry-action {
  background: color-mix(in srgb, var(--error) 18%, transparent);
  border: 1px solid color-mix(in srgb, var(--error) 56%, transparent);
  border-radius: 0.45rem;
  color: var(--text);
  cursor: pointer;
  font-size: 0.76rem;
  font-weight: 600;
  padding: 0.15rem 0.45rem;
}

.messages {
  background: color-mix(in srgb, var(--surface) 90%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.bubble {
  border-radius: 1rem;
  max-width: min(74ch, 88%);
  padding: 0.65rem 0.85rem;
}

.bubble[data-sender="user"] {
  align-self: flex-end;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--brand) 35%, transparent),
    color-mix(in srgb, var(--brand-2) 42%, transparent)
  );
  border: 1px solid color-mix(in srgb, var(--brand) 60%, transparent);
}

.bubble[data-sender="assistant"] {
  align-self: flex-start;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 95%, transparent);
}

.bubble-head {
  color: var(--muted);
  font-size: 0.78rem;
  letter-spacing: 0.08em;
  margin-bottom: 0.35rem;
  text-transform: uppercase;
}

.bubble-body {
  line-height: 1.45;
}

.empty-tip {
  color: var(--muted);
  margin: auto;
}

.composer {
  align-items: flex-end;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.composer[data-expanded="true"] {
  min-height: 42vh;
}

.composer-input {
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 0.85rem;
  color: var(--text);
  font-family: inherit;
  font-size: 1rem;
  min-height: 2.8rem;
  resize: none;
  width: 100%;
  padding: 0.75rem 0.9rem;
}

.composer[data-expanded="true"] .composer-input {
  flex: 1;
  max-height: 60vh;
}

.composer-actions {
  display: flex;
  gap: 0.55rem;
  justify-content: flex-end;
  width: 100%;
}

.composer-actions button {
  background: linear-gradient(125deg, var(--brand), var(--brand-2));
  border: 0;
  border-radius: 0.85rem;
  color: #071018;
  cursor: pointer;
  font-family: inherit;
  font-weight: 700;
  min-width: 5.3rem;
}

.composer-actions .ghost-button {
  background: transparent;
  border: 1px solid var(--panel-edge);
  color: var(--text);
}

.composer-actions button:disabled {
  cursor: not-allowed;
  filter: grayscale(0.8);
  opacity: 0.6;
}

.markdown-body :deep(p) {
  margin: 0;
}

@media (max-width: 1023px) {
  .chat-shell {
    border-radius: 0.9rem;
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
    padding: 0.85rem;
  }

  .session-sidebar {
    max-height: clamp(8rem, 24vh, 12rem);
  }

  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .composer {
    align-items: stretch;
  }
}
</style>

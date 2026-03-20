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
import type { Attachment, Message } from "../types/message";
import {
  formatTimeSeparatorLabel,
  shouldInsertTimeSeparator,
  toTimestampMs,
} from "../utils/timeFormat";

const props = withDefaults(
  defineProps<{
    wsUrl: string;
    token: string;
    sessionId?: string;
    apiBase?: string;
  }>(),
  {
    sessionId: "main",
    apiBase: "",
  },
);

const resolveInitialSessionId = (): string => {
  const querySession = new URLSearchParams(window.location.search).get("session");
  const normalizedQuery = (querySession ?? "").trim();
  if (normalizedQuery) {
    return normalizedQuery;
  }
  return props.sessionId.trim() || "main";
};

const quickPrompts = [
  "📧 帮我看看邮件",
  "📁 今天有什么任务？",
  "🔧 检查系统状态",
  "💬 随便聊聊",
] as const;

const capabilitySummary = [
  "邮件扫描与优先级摘要",
  "文件管理与代码仓库检索",
  "QQ 消息同步与通知镜像",
  "定时提醒与系统巡检",
] as const;

const draft = ref("");
const fileInputRef = ref<HTMLInputElement | null>(null);
const composerExpanded = ref(false);
const composerRef = ref<HTMLTextAreaElement | null>(null);
const messagesRef = ref<HTMLElement | null>(null);
const activeSessionId = ref(resolveInitialSessionId());
const pendingAttachments = ref<Attachment[]>([]);
const isComposerDragActive = ref(false);
const isUploadingAttachments = ref(false);
const MAX_ATTACHMENTS_PER_MESSAGE = 5;

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
  sendMessage,
  status,
} = useChatSocket({
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
  () =>
    status.value === "connected" &&
    !isUploadingAttachments.value &&
    (draft.value.trim().length > 0 || pendingAttachments.value.length > 0),
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

const normalizeTimestamp = (value: string | null | undefined): string | undefined => {
  if (!value) {
    return undefined;
  }
  const normalized = value.trim();
  const sqliteUtcPattern = /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(\.\d+)?$/;
  if (sqliteUtcPattern.test(normalized)) {
    return `${normalized.replace(/\s+/, "T")}Z`;
  }
  return normalized;
};

const toTimelineValue = (value: string | null | undefined): number => {
  const epoch = toTimestampMs(normalizeTimestamp(value));
  return epoch ?? Number.POSITIVE_INFINITY;
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
      timestamp: normalizeTimestamp(row.created_at),
      event_type: "tool_call_start",
      tool_name: row.tool_name,
      tool_call_id: toolCallId,
      arguments: params,
      metadata: { ephemeral: true },
    });
    messagesFromRows.push({
      sender: "assistant",
      session_id: row.session_id,
      timestamp: normalizeTimestamp(row.created_at),
      event_type: "tool_call_result",
      tool_name: row.tool_name,
      tool_call_id: toolCallId,
      status: row.status,
      result: row.result_summary ?? "",
      error_info: row.error_info ?? "",
      metadata: { ephemeral: true },
      compressed_meta: compressedMeta,
    });
  }
  return messagesFromRows;
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

const applyQuickPrompt = (prompt: string): void => {
  draft.value = prompt;
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

const mediaType = (message: Message): "image" | "video" =>
  /\.(mp4|webm)$/i.test(message.image ?? message.file ?? "") ? "video" : "image";

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
  message.event_type === "tool_call_start" ||
  isEphemeralToolResult(message) ||
  message.message_tag === "tool_status";

const displayedMessages = computed(() =>
  messages.value.filter((message) => !isHiddenSystemToolEvent(message)),
);

type TimelineItem =
  | {
      kind: "separator";
      key: string;
      label: string;
    }
  | {
      kind: "message";
      key: string;
      message: Message;
    };

const timelineItems = computed<TimelineItem[]>(() => {
  const items: TimelineItem[] = [];
  let previousVisibleTimestamp: string | undefined;

  displayedMessages.value.forEach((message, index) => {
    const currentTimestamp = normalizeTimestamp(message.timestamp);
    if (currentTimestamp && shouldInsertTimeSeparator(currentTimestamp, previousVisibleTimestamp)) {
      items.push({
        kind: "separator",
        key: `separator-${index}-${currentTimestamp}`,
        label: formatTimeSeparatorLabel(currentTimestamp, previousVisibleTimestamp),
      });
      previousVisibleTimestamp = currentTimestamp;
    } else if (currentTimestamp) {
      previousVisibleTimestamp = currentTimestamp;
    }

    items.push({
      kind: "message",
      key: `${message.session_id}-${message.sender}-${index}`,
      message,
    });
  });

  return items;
});

const welcomeVisible = computed(() => displayedMessages.value.length === 0);

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
  if (!sendMessage(draft.value, pendingAttachments.value)) {
    return;
  }
  draft.value = "";
  pendingAttachments.value = [];
  if (fileInputRef.value) {
    fileInputRef.value.value = "";
  }
  void nextTick(() => {
    adjustComposerHeight();
  });
};

const normalizeSelectedFiles = (files: FileList | File[] | null | undefined): File[] =>
  Array.from(files ?? []).filter((file) => file.size >= 0);

const formatAttachmentSize = (sizeBytes: number | null | undefined): string => {
  if (typeof sizeBytes !== "number" || !Number.isFinite(sizeBytes) || sizeBytes < 0) {
    return "";
  }
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
};

const attachmentPreviewUrl = (attachment: Attachment): string =>
  resolveAssetUrl(attachment.url);

const attachmentLabel = (attachment: Attachment): string =>
  attachment.filename || attachment.url.split("/").pop() || attachment.url;

const openFilePicker = (): void => {
  fileInputRef.value?.click();
};

const removePendingAttachment = (index: number): void => {
  pendingAttachments.value = pendingAttachments.value.filter((_, itemIndex) => itemIndex !== index);
};

const uploadFiles = async (incomingFiles: File[]): Promise<void> => {
  const remainingSlots = MAX_ATTACHMENTS_PER_MESSAGE - pendingAttachments.value.length;
  if (remainingSlots <= 0) {
    notification?.warning({
      title: "附件已达上限",
      content: "每条消息最多上传 5 个附件。",
      duration: 3000,
    });
    return;
  }

  const files = incomingFiles.slice(0, remainingSlots);
  if (!files.length) {
    return;
  }

  const formData = new FormData();
  files.forEach((file) => {
    formData.append("file", file);
  });

  isUploadingAttachments.value = true;
  try {
    const response = await fetch(withApiToken(makeApiUrl("upload")), {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const message =
        response.status === 413
          ? "单个文件不能超过 100MB。"
          : "上传失败，请稍后重试。";
      throw new Error(message);
    }
    const payload = (await response.json()) as { attachments?: Attachment[] };
    const uploaded = Array.isArray(payload.attachments) ? payload.attachments : [];
    pendingAttachments.value = [...pendingAttachments.value, ...uploaded];
  } catch (error) {
    notification?.error({
      title: "附件上传失败",
      content: error instanceof Error ? error.message : "上传失败，请稍后重试。",
      duration: 3500,
    });
  } finally {
    isUploadingAttachments.value = false;
  }
};

const onFileInputChange = async (event: Event): Promise<void> => {
  const input = event.target as HTMLInputElement | null;
  await uploadFiles(normalizeSelectedFiles(input?.files));
  if (input) {
    input.value = "";
  }
};

const onComposerDragOver = (event: DragEvent): void => {
  event.preventDefault();
  isComposerDragActive.value = true;
};

const onComposerDragLeave = (event: DragEvent): void => {
  event.preventDefault();
  isComposerDragActive.value = false;
};

const onComposerDrop = async (event: DragEvent): Promise<void> => {
  event.preventDefault();
  isComposerDragActive.value = false;
  await uploadFiles(normalizeSelectedFiles(event.dataTransfer?.files));
};

const onComposerPaste = async (event: ClipboardEvent): Promise<void> => {
  const files = normalizeSelectedFiles(event.clipboardData?.files);
  if (!files.length) {
    return;
  }
  event.preventDefault();
  await uploadFiles(files);
};

const scrollToBottom = (): void => {
  const element = messagesRef.value;
  if (!element) {
    return;
  }
  element.scrollTop = element.scrollHeight;
};

onMounted(() => {
  void (async () => {
    await loadSessionMessages(activeSessionId.value);
    connect();
    await nextTick();
    adjustComposerHeight();
  })();
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
    combo: "enter",
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
      replaceMessages([]);
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
      // Reserved for future command palette work.
    },
  },
]);
</script>

<template>
  <section class="chat-shell">
    <div class="chat-main">
      <header class="chat-header">
        <div class="title-wrap">
          <p class="eyebrow">Hypo-Agent</p>
          <h1>Personal Assistant Workspace</h1>
          <p class="chat-subtitle">邮件、QQ、文件和提醒汇聚在同一个主会话里。</p>
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
        <section v-if="welcomeVisible" class="welcome-state">
          <div class="welcome-copy">
            <p class="eyebrow">Welcome</p>
            <h2>Hi，我是 Hypo-Agent</h2>
            <p>
              你的个人智能助手。你可以直接让我查邮件、同步 QQ、读取文件、检查系统状态，
              或者像平常聊天一样把任务交给我。
            </p>
          </div>

          <div class="quick-prompts">
            <button
              v-for="(prompt, index) in quickPrompts"
              :key="prompt"
              type="button"
              class="quick-prompt"
              :data-testid="`quick-prompt-${index}`"
              @click="applyQuickPrompt(prompt)"
            >
              {{ prompt }}
            </button>
          </div>

          <div class="capability-list">
            <span
              v-for="item in capabilitySummary"
              :key="item"
              class="capability-chip"
            >
              {{ item }}
            </span>
          </div>
        </section>

        <template v-else>
          <template v-for="item in timelineItems" :key="item.key">
            <div
              v-if="item.kind === 'separator'"
              class="message-time-separator"
              data-testid="message-time-separator"
            >
              <span>{{ item.label }}</span>
            </div>
            <MessageBubble
              v-else
              :message="item.message"
              :asset-url-resolver="resolveAssetUrl"
            >
            <CompressedMessage
              v-if="isCompressedToolResult(item.message)"
              :summary="String(item.message.result ?? '')"
              :compressed-meta="item.message.compressed_meta"
              :api-base="normalizedApiBase"
              :token="token"
              :tool-name="item.message.tool_name"
              :file-path="resolveCompressedFilePath(item.message)"
            />
            <ToolCallMessage
              v-else-if="isToolCall(item.message)"
              :tool-name="item.message.tool_name ?? ''"
              :status="item.message.status"
              :params="item.message.arguments"
              :result="item.message.result"
            />
            <MarkdownPreview
              v-else-if="hasMarkdownPreview(item.message)"
              :content="item.message.text ?? ''"
            />
            <MediaMessage
              v-else-if="hasMedia(item.message)"
              :src="mediaSource(item.message)"
              :media-type="mediaType(item.message)"
            />
            <FileAttachment
              v-else-if="hasCodeFilePreview(item.message)"
              :path="item.message.file ?? ''"
              :content="item.message.text ?? ''"
            />
            <FileAttachment
              v-else-if="hasFileAttachment(item.message)"
              :path="resolveAssetUrl(item.message.file)"
            />
            <TextMessage
              v-else-if="(item.message.text ?? '').trim().length > 0"
              :text="item.message.text ?? ''"
            />
            </MessageBubble>
          </template>
        </template>
      </main>

      <form
        class="composer"
        :data-drag-active="isComposerDragActive"
        :data-expanded="composerExpanded"
        @dragenter.prevent="isComposerDragActive = true"
        @dragleave="onComposerDragLeave"
        @dragover="onComposerDragOver"
        @drop="onComposerDrop"
        @submit.prevent="onSubmit"
      >
        <input
          ref="fileInputRef"
          data-testid="attachment-input"
          type="file"
          multiple
          class="composer-file-input"
          @change="onFileInputChange"
        />
        <textarea
          ref="composerRef"
          v-model="draft"
          name="message"
          autocomplete="off"
          placeholder="输入消息（Enter 发送）"
          class="composer-input"
          @paste="onComposerPaste"
        />
        <div v-if="pendingAttachments.length" class="composer-attachments">
          <article
            v-for="(attachment, index) in pendingAttachments"
            :key="`${attachment.url}-${index}`"
            class="composer-attachment"
          >
            <img
              v-if="attachment.type === 'image'"
              :src="attachmentPreviewUrl(attachment)"
              :alt="attachment.filename ?? 'image preview'"
              class="composer-attachment-thumb"
            />
            <div class="composer-attachment-meta">
              <strong>{{ attachmentLabel(attachment) }}</strong>
              <span>{{ formatAttachmentSize(attachment.size_bytes) }}</span>
            </div>
            <button
              type="button"
              class="attachment-remove"
              @click="removePendingAttachment(index)"
            >
              ×
            </button>
          </article>
        </div>
        <div class="composer-actions">
          <button type="button" class="ghost-button" @click="toggleComposerExpanded">
            {{ composerExpanded ? "收起输入框" : "展开输入框" }}
          </button>
          <button type="button" class="ghost-button" @click="openFilePicker">
            📎
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
  height: 100%;
  margin: 0 auto;
  min-height: 0;
  max-width: 1120px;
  padding: 1.2rem;
}

.chat-main {
  display: grid;
  gap: 1rem;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  min-height: 0;
}

.chat-header {
  align-items: center;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
}

.title-wrap h1 {
  font-size: clamp(1.2rem, 2.2vw, 1.8rem);
  letter-spacing: 0.01em;
  margin: 0;
}

.chat-subtitle {
  color: var(--muted);
  margin: 0.4rem 0 0;
  max-width: 40rem;
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
  flex-wrap: wrap;
  gap: 0.5rem;
  justify-content: flex-end;
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
  gap: 0.9rem;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.message-time-separator {
  align-items: center;
  color: color-mix(in srgb, var(--muted) 90%, transparent);
  display: grid;
  font-size: 0.75rem;
  gap: 0.7rem;
  grid-template-columns: 1fr auto 1fr;
  margin: 0.1rem 0;
}

.message-time-separator::before,
.message-time-separator::after {
  border-top: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  content: "";
}

.welcome-state {
  display: grid;
  gap: 1.2rem;
  margin: auto 0;
  min-height: 100%;
  place-content: center;
}

.welcome-copy h2 {
  font-size: clamp(1.6rem, 4vw, 2.35rem);
  margin: 0;
}

.welcome-copy p:last-child {
  color: var(--muted);
  line-height: 1.6;
  margin: 0.75rem 0 0;
  max-width: 42rem;
}

.quick-prompts {
  display: grid;
  gap: 0.8rem;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.quick-prompt {
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--brand) 18%, transparent), transparent),
    color-mix(in srgb, var(--surface) 95%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 1rem;
  color: var(--text);
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  padding: 1rem 1.05rem;
  text-align: left;
}

.quick-prompt:hover {
  border-color: color-mix(in srgb, var(--brand) 54%, var(--panel-edge));
  transform: translateY(-1px);
}

.capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.capability-chip {
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 999px;
  color: var(--muted);
  font-size: 0.84rem;
  padding: 0.42rem 0.72rem;
}

.composer {
  backdrop-filter: blur(8px);
  background: color-mix(in srgb, var(--panel) 90%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  padding: 0.85rem;
  position: sticky;
  bottom: 0;
}

.composer[data-drag-active="true"] {
  border-color: color-mix(in srgb, var(--brand) 72%, var(--panel-edge));
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--brand) 36%, transparent);
}

.composer-file-input {
  display: none;
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
  padding: 0.75rem 0.9rem;
  resize: none;
  width: 100%;
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

.composer-attachments {
  display: grid;
  gap: 0.55rem;
}

.composer-attachment {
  align-items: center;
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 0.85rem;
  display: grid;
  gap: 0.75rem;
  grid-template-columns: auto minmax(0, 1fr) auto;
  padding: 0.55rem 0.7rem;
}

.composer-attachment-thumb {
  border-radius: 0.65rem;
  height: 3rem;
  object-fit: cover;
  width: 3rem;
}

.composer-attachment-meta {
  display: grid;
  gap: 0.15rem;
  min-width: 0;
}

.composer-attachment-meta strong {
  font-size: 0.84rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-attachment-meta span {
  color: var(--muted);
  font-size: 0.75rem;
}

.attachment-remove {
  align-items: center;
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 999px;
  color: var(--muted);
  cursor: pointer;
  display: inline-flex;
  height: 1.8rem;
  justify-content: center;
  width: 1.8rem;
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
  padding: 0.55rem 0.9rem;
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
    padding: 0.9rem;
  }

  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .status-wrap {
    justify-content: flex-start;
  }
}

@media (max-width: 767px) {
  .chat-shell {
    border-radius: 0;
    box-shadow: none;
    padding: 0.75rem;
  }

  .messages {
    padding: 0.8rem;
  }

  .quick-prompts {
    grid-template-columns: 1fr;
  }

  .composer {
    padding: 0.7rem;
  }

  .composer-actions {
    flex-direction: column;
  }

  .composer-actions button {
    width: 100%;
  }
}
</style>

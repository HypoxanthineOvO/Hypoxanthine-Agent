<script setup lang="ts">
import { useNotification } from "naive-ui";
import { computed, h, nextTick, onMounted, onUnmounted, ref, watch } from "vue";

import ChatComposer from "@/components/chat/ChatComposer.vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import ConnectionStatus from "@/components/ConnectionStatus.vue";
import ReconnectBanner from "@/components/layout/ReconnectBanner.vue";
import { useChatSocket } from "@/composables/useChatSocket";
import { useHotkey } from "@/composables/useHotkey";
import { loadSessionMessages } from "@/composables/useSessionHistory";
import { useThemeMode } from "@/composables/useThemeMode";
import type { Message } from "@/types/message";
import { isHiddenSystemToolEvent, resolveAssetUrl } from "@/utils/messageRouting";
import { formatTimeSeparatorLabel, shouldInsertTimeSeparator } from "@/utils/timeFormat";
import { normalizeTimestamp } from "@/utils/jsonParsers";

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

interface TimelineSeparatorItem { kind: "separator"; key: string; label: string }
interface TimelineMessageItem { kind: "message"; key: string; message: Message }
type TimelineItem = TimelineSeparatorItem | TimelineMessageItem;
interface ChatComposerExpose { collapseExpanded: () => boolean; focusComposer: () => void; submitMessage: () => boolean }

const quickPrompts = ["📧 帮我看看邮件", "📁 今天有什么任务？", "🔧 检查系统状态", "💬 随便聊聊"] as const;
const capabilitySummary = ["邮件扫描与优先级摘要", "文件管理与代码仓库检索", "QQ 消息同步与通知镜像", "定时提醒与系统巡检"] as const;

const resolveInitialSessionId = (): string => {
  const querySession = new URLSearchParams(window.location.search).get("session");
  return (querySession ?? "").trim() || props.sessionId.trim() || "main";
};

const draft = ref("");
const composerExpanded = ref(false);
const messagesRef = ref<HTMLElement | null>(null);
const composerComponentRef = ref<ChatComposerExpose | null>(null);
const activeSessionId = ref(resolveInitialSessionId());
const retryingFailedMessage = ref(false);
const { toggleMode } = useThemeMode();

const normalizedApiBase = computed(() => {
  const explicitBase = props.apiBase.trim();
  if (explicitBase) {
    return explicitBase.replace(/\/+$/, "");
  }
  try {
    const parsed = new URL(props.wsUrl);
    return `${parsed.protocol === "wss:" ? "https:" : "http:"}//${parsed.host}/api`;
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
  retryLastMessage,
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

const showReconnectBanner = computed(() => status.value === "reconnecting" || reconnectDelayMs.value !== null);
const displayedMessages = computed(() => messages.value.filter((message) => !isHiddenSystemToolEvent(message)));

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
const assetUrlResolver = (rawPath: string): string => resolveAssetUrl(rawPath, normalizedApiBase.value, props.token);

let scrollFrame: number | null = null;

const scheduleScrollToBottom = (): void => {
  if (scrollFrame !== null) {
    cancelAnimationFrame(scrollFrame);
  }
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null;
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight;
    }
  });
};

const restoreSessionMessages = async (sessionId: string): Promise<void> => {
  try {
    replaceMessages(
      await loadSessionMessages({
        apiBase: normalizedApiBase.value,
        token: props.token,
        sessionId,
      }),
    );
  } catch {
    replaceMessages([]);
  }
};

const applyQuickPrompt = (prompt: string): void => {
  draft.value = prompt;
  void nextTick(() => composerComponentRef.value?.focusComposer());
};

const retryFailedMessage = async (): Promise<void> => {
  retryingFailedMessage.value = true;
  const didRetry = retryLastMessage();
  if (!didRetry) {
    notification?.warning({
      title: "无法重试",
      content: "当前没有可重试的消息。",
      duration: 2500,
    });
  }
  await new Promise((resolve) => window.setTimeout(resolve, 300));
  retryingFailedMessage.value = false;
};

watch(() => `${messages.value.length}:${messages.value[messages.value.length - 1]?.text?.length ?? 0}`, scheduleScrollToBottom);

watch(
  () => props.sessionId,
  (newId) => {
    const normalized = (newId ?? "").trim() || "main";
    if (normalized !== activeSessionId.value) {
      activeSessionId.value = normalized;
      replaceMessages([]);
      void restoreSessionMessages(normalized);
    }
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
  { combo: "enter", handler: () => composerComponentRef.value?.submitMessage() },
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
  { combo: "ctrlOrMeta+l", handler: () => replaceMessages([]) },
  { combo: "ctrlOrMeta+d", handler: () => toggleMode() },
  { combo: "ctrlOrMeta+k", handler: () => undefined },
]);

onMounted(() => {
  void (async () => {
    await restoreSessionMessages(activeSessionId.value);
    connect();
  })();
});

onUnmounted(() => {
  if (scrollFrame !== null) {
    cancelAnimationFrame(scrollFrame);
  }
});
</script>

<template>
  <section class="chat-page">
    <header class="chat-header">
      <div class="title-wrap">
        <p class="eyebrow">Hypo-Agent</p>
        <h1>Personal Assistant Workspace</h1>
        <p class="chat-subtitle">邮件、QQ、文件和提醒汇聚在同一个主会话里。</p>
      </div>
      <div class="status-wrap">
        <ConnectionStatus :status="status" />
        <button type="button" class="ghost-button" data-testid="connect-button" @click="connect">
          Connect
        </button>
        <button type="button" class="ghost-button" @click="disconnect">Disconnect</button>
      </div>
    </header>

    <ReconnectBanner
      :visible="showReconnectBanner"
      :retry-after-ms="reconnectDelayMs"
      @retry="reconnectNow"
    />

    <main ref="messagesRef" class="message-list" aria-live="polite">
      <ChatMessageList
        :timeline-items="timelineItems"
        :welcome-visible="welcomeVisible"
        :quick-prompts="quickPrompts"
        :capability-summary="capabilitySummary"
        :api-base="normalizedApiBase"
        :token="props.token"
        :retrying-failed-message="retryingFailedMessage"
        :asset-url-resolver="assetUrlResolver"
        @prompt="applyQuickPrompt"
        @retry-failed="void retryFailedMessage()"
      />
    </main>

    <ChatComposer
      ref="composerComponentRef"
      v-model="draft"
      :expanded="composerExpanded"
      :api-base="normalizedApiBase"
      :token="props.token"
      :connection-status="status"
      :send-message="sendMessage"
      @update:expanded="composerExpanded = $event"
    />
  </section>
</template>

<style scoped>
.chat-page {
  backdrop-filter: blur(6px);
  background:
    radial-gradient(120% 120% at 0% 0%, color-mix(in srgb, var(--brand) 22%, transparent), transparent 52%),
    radial-gradient(130% 130% at 100% 100%, color-mix(in srgb, var(--brand-2) 18%, transparent), transparent 60%),
    var(--panel);
  border: 1px solid var(--panel-edge);
  border-radius: 1.35rem;
  box-shadow: 0 24px 60px color-mix(in srgb, black 50%, transparent);
  display: flex;
  flex-direction: column;
  gap: 1rem;
  height: 100%;
  margin: 0 auto;
  max-width: 1120px;
  min-height: 0;
  overflow: hidden;
  padding: 1.2rem;
  width: 100%;
}
.chat-header,
.status-wrap {
  align-items: center;
  display: flex;
  gap: 1rem;
}
.chat-header {
  flex-shrink: 0;
  justify-content: space-between;
}
.title-wrap h1 {
  font-size: clamp(1.2rem, 2.2vw, 1.8rem);
  letter-spacing: 0.01em;
  margin: 0;
}
.chat-subtitle,
.eyebrow {
  color: var(--muted);
}
.chat-subtitle {
  margin: 0.4rem 0 0;
  max-width: 40rem;
}
.eyebrow {
  font-size: 0.82rem;
  letter-spacing: 0.12em;
  margin: 0 0 0.25rem;
  text-transform: uppercase;
}
.status-wrap {
  flex-wrap: wrap;
  justify-content: flex-end;
}
.ghost-button {
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 80%, transparent);
  border-radius: 0.75rem;
  color: var(--text);
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  padding: 0.45rem 0.8rem;
}
.message-list {
  display: grid;
  gap: 1rem;
  min-height: 0;
  overflow-y: auto;
  padding-right: 0.2rem;
}
:global(.retry-action) {
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 80%, transparent);
  border-radius: 0.6rem;
  color: inherit;
  cursor: pointer;
  font: inherit;
  padding: 0.32rem 0.65rem;
}
@media (max-width: 767px) {
  .chat-page {
    border-radius: 0;
    box-shadow: none;
    max-width: none;
    padding: 1rem;
  }
  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .status-wrap {
    justify-content: flex-start;
  }
}
</style>

<script setup lang="ts">
import MarkdownIt from "markdown-it";
import { computed, onMounted, ref } from "vue";

import ConnectionStatus from "../components/ConnectionStatus.vue";
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

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const draft = ref("");
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

const { connect, disconnect, messages, replaceMessages, sendText, status } =
  useChatSocket({
  url: props.wsUrl,
  token: props.token,
  sessionId: activeSessionId,
});

const canSend = computed(
  () => status.value === "connected" && draft.value.trim().length > 0,
);

const makeApiUrl = (path: string): string =>
  `${normalizedApiBase.value}/${path.replace(/^\/+/, "")}`;

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
  const response = await fetch(
    makeApiUrl(`sessions/${encodeURIComponent(sessionId)}/messages`),
  );
  if (!response.ok) {
    replaceMessages([]);
    return;
  }
  const history = (await response.json()) as Message[];
  replaceMessages(history);
};

const loadSessions = async (): Promise<void> => {
  const response = await fetch(makeApiUrl("sessions"));
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

const renderMarkdown = (message: Message): string =>
  markdown.render(message.text ?? "");

const onSubmit = (): void => {
  if (!sendText(draft.value)) {
    return;
  }
  ensureSessionPresent(activeSessionId.value);
  draft.value = "";
};

onMounted(() => {
  void loadSessions();
});
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

      <main class="messages" aria-live="polite">
        <article
          v-for="(message, index) in messages"
          :key="`${message.session_id}-${message.sender}-${index}`"
          class="bubble"
          :data-sender="message.sender"
        >
          <header class="bubble-head">{{ message.sender }}</header>
          <div class="bubble-body markdown-body" v-html="renderMarkdown(message)" />
        </article>
        <p v-if="messages.length === 0" class="empty-tip">
          No messages yet. Connect and send your first line.
        </p>
      </main>

      <form class="composer" @submit.prevent="onSubmit">
        <input
          v-model="draft"
          type="text"
          name="message"
          autocomplete="off"
          placeholder="Type a message and press Enter..."
        />
        <button type="submit" :disabled="!canSend">Send</button>
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
  margin: 0 auto;
  max-width: 1040px;
  padding: 1.2rem;
}

.session-sidebar {
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 0.95rem;
  display: grid;
  gap: 0.75rem;
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
  max-height: 60vh;
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

.messages {
  background: color-mix(in srgb, var(--surface) 90%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-height: 56vh;
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
  display: grid;
  gap: 0.7rem;
  grid-template-columns: 1fr auto;
}

.composer input {
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 0.85rem;
  color: var(--text);
  font-family: inherit;
  font-size: 1rem;
  padding: 0.75rem 0.9rem;
}

.composer button {
  background: linear-gradient(125deg, var(--brand), var(--brand-2));
  border: 0;
  border-radius: 0.85rem;
  color: #071018;
  cursor: pointer;
  font-family: inherit;
  font-weight: 700;
  min-width: 5.3rem;
}

.composer button:disabled {
  cursor: not-allowed;
  filter: grayscale(0.8);
  opacity: 0.6;
}

.markdown-body :deep(p) {
  margin: 0;
}

@media (max-width: 860px) {
  .chat-shell {
    border-radius: 0.9rem;
    grid-template-columns: 1fr;
    padding: 0.85rem;
  }

  .session-list {
    max-height: 22vh;
  }

  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .composer {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
import MarkdownIt from "markdown-it";
import { computed, ref } from "vue";

import ConnectionStatus from "../components/ConnectionStatus.vue";
import { useChatSocket } from "../composables/useChatSocket";
import type { Message } from "../types/message";

const props = withDefaults(
  defineProps<{
    wsUrl: string;
    token: string;
    sessionId?: string;
  }>(),
  {
    sessionId: "session-1",
  },
);

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const draft = ref("");
const { connect, disconnect, messages, sendText, status } = useChatSocket({
  url: props.wsUrl,
  token: props.token,
  sessionId: props.sessionId,
});

const canSend = computed(
  () => status.value === "connected" && draft.value.trim().length > 0,
);

const renderMarkdown = (message: Message): string =>
  markdown.render(message.text ?? "");

const onSubmit = (): void => {
  if (!sendText(draft.value)) {
    return;
  }
  draft.value = "";
};
</script>

<template>
  <section class="chat-shell">
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
  margin: 0 auto;
  max-width: 980px;
  padding: 1.2rem;
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

@media (max-width: 720px) {
  .chat-shell {
    border-radius: 0.9rem;
    padding: 0.85rem;
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

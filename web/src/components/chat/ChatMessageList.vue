<script setup lang="ts">
import MessageBubble from "./MessageBubble.vue";
import MessageRenderer from "./MessageRenderer.vue";
import type { Message } from "@/types/message";

interface TimelineSeparatorItem {
  kind: "separator";
  key: string;
  label: string;
}

interface TimelineMessageItem {
  kind: "message";
  key: string;
  message: Message;
}

type TimelineItem = TimelineSeparatorItem | TimelineMessageItem;

defineProps<{
  timelineItems: TimelineItem[];
  welcomeVisible: boolean;
  quickPrompts: readonly string[];
  capabilitySummary: readonly string[];
  apiBase: string;
  token: string;
  retryingFailedMessage: boolean;
  assetUrlResolver: (rawPath: string) => string;
}>();

const emit = defineEmits<{
  prompt: [prompt: string];
  retryFailed: [];
}>();
</script>

<template>
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
        @click="emit('prompt', prompt)"
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
        :asset-url-resolver="assetUrlResolver"
      >
        <MessageRenderer
          :message="item.message"
          :api-base="apiBase"
          :token="token"
          :retrying-failed-message="retryingFailedMessage"
          @retry="emit('retryFailed')"
        />
      </MessageBubble>
    </template>
  </template>
</template>

<style scoped>
.message-time-separator {
  align-items: center;
  color: var(--muted);
  display: grid;
  gap: 0.7rem;
  grid-template-columns: 1fr auto 1fr;
  margin: 0.35rem 0;
}

.message-time-separator::before,
.message-time-separator::after {
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--panel-edge) 82%, transparent),
    transparent
  );
  content: "";
  height: 1px;
}

.message-time-separator span {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 70%, transparent);
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.16rem 0.6rem;
}

.welcome-state {
  display: grid;
  gap: 1.2rem;
  padding: 1rem 0;
}

.welcome-copy h2 {
  font-size: clamp(1.4rem, 2.4vw, 2rem);
  margin: 0 0 0.45rem;
}

.welcome-copy p {
  color: var(--muted);
  margin: 0;
  max-width: 38rem;
}

.eyebrow {
  color: var(--muted);
  font-size: 0.82rem;
  letter-spacing: 0.12em;
  margin: 0 0 0.25rem;
  text-transform: uppercase;
}

.quick-prompts {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.quick-prompt {
  background: linear-gradient(
    145deg,
    color-mix(in srgb, var(--surface) 90%, transparent),
    color-mix(in srgb, var(--panel) 82%, transparent)
  );
  border: 1px solid color-mix(in srgb, var(--panel-edge) 80%, transparent);
  border-radius: 1rem;
  color: var(--text);
  cursor: pointer;
  font: inherit;
  min-height: 4.5rem;
  padding: 0.95rem 1rem;
  text-align: left;
  transition:
    transform 0.16s ease,
    border-color 0.16s ease,
    box-shadow 0.16s ease;
}

.quick-prompt:hover {
  border-color: color-mix(in srgb, var(--brand) 56%, transparent);
  box-shadow: 0 14px 24px color-mix(in srgb, var(--brand) 14%, transparent);
  transform: translateY(-2px);
}

.capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.capability-chip {
  background: color-mix(in srgb, var(--surface) 85%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 78%, transparent);
  border-radius: 999px;
  color: var(--text-soft);
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0.4rem 0.7rem;
}

@media (max-width: 767px) {
  .quick-prompts {
    grid-template-columns: 1fr;
  }
}
</style>

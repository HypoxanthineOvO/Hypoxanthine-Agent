<script setup lang="ts">
import type { Message } from "../../types/message";

const props = defineProps<{
  message: Message;
}>();

const avatarLabel = (): string => {
  if (props.message.senderAvatar) {
    return props.message.senderAvatar;
  }
  return props.message.sender === "user" ? "U" : "A";
};
</script>

<template>
  <article class="message-bubble" :data-sender="message.sender">
    <div class="bubble-avatar">{{ avatarLabel() }}</div>
    <div class="bubble-content">
      <header class="bubble-meta">
        <span class="sender-name">{{ message.senderName ?? message.sender }}</span>
        <span v-if="message.timestamp" class="sender-time">{{ message.timestamp }}</span>
      </header>
      <slot />
    </div>
  </article>
</template>

<style scoped>
.message-bubble {
  align-items: flex-start;
  display: grid;
  gap: 0.55rem;
  grid-template-columns: auto 1fr;
  max-width: min(84ch, 100%);
}

.message-bubble[data-sender="user"] {
  margin-left: auto;
}

.message-bubble[data-sender="assistant"] {
  margin-right: auto;
}

.bubble-avatar {
  align-items: center;
  background: color-mix(in srgb, var(--panel) 82%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 50%;
  display: inline-flex;
  font-size: 0.75rem;
  font-weight: 700;
  height: 1.8rem;
  justify-content: center;
  width: 1.8rem;
}

.bubble-content {
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 0.9rem;
  min-width: 0;
  padding: 0.6rem 0.72rem;
}

.message-bubble[data-sender="user"] .bubble-content {
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--brand) 26%, transparent),
    color-mix(in srgb, var(--brand-2) 30%, transparent)
  );
}

.bubble-meta {
  align-items: baseline;
  color: var(--muted);
  display: flex;
  font-size: 0.75rem;
  justify-content: space-between;
  margin-bottom: 0.35rem;
}

.sender-name {
  font-weight: 700;
  text-transform: capitalize;
}

.sender-time {
  font-size: 0.7rem;
}
</style>

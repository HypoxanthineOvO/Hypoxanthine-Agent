<script setup lang="ts">
import { computed } from "vue";

import FileAttachment from "./FileAttachment.vue";
import MediaMessage from "./MediaMessage.vue";
import type { Message } from "../../types/message";
import { formatMessageTime } from "../../utils/timeFormat";

const props = defineProps<{
  message: Message;
  assetUrlResolver?: ((rawPath: string) => string) | undefined;
}>();

const isNarration = (): boolean => props.message.message_tag === "narration";

const sourceLabel = (): string => {
  const channel = String(props.message.channel ?? "").trim().toLowerCase();
  if (channel === "qq") {
    return "🐧 QQ";
  }
  if (channel === "weixin") {
    return "💬 微信";
  }
  if (channel === "system") {
    return "系统";
  }
  return "";
};

const avatarLabel = (): string => {
  if (props.message.senderAvatar) {
    return props.message.senderAvatar;
  }
  return props.message.sender === "user" ? "U" : "A";
};

const formattedTime = computed(() => {
  const raw = props.message.timestamp;
  if (!raw || isNarration()) {
    return "";
  }
  return formatMessageTime(raw);
});

const messageAttachments = computed(() => props.message.attachments ?? []);
const imageAttachments = computed(() =>
  messageAttachments.value.filter((attachment) => attachment.type === "image"),
);
const fileAttachments = computed(() =>
  messageAttachments.value.filter((attachment) => attachment.type !== "image"),
);

const resolveAttachmentUrl = (rawPath: string): string => {
  if (typeof props.assetUrlResolver === "function") {
    return props.assetUrlResolver(rawPath);
  }
  return rawPath;
};

const attachmentLabel = (rawPath: string, filename?: string | null): string =>
  filename || rawPath.split("/").pop() || rawPath;
</script>

<template>
  <article
    class="message-bubble"
    :data-sender="message.sender"
    :data-message-tag="message.message_tag"
  >
    <div v-if="!isNarration()" class="bubble-avatar">{{ avatarLabel() }}</div>
    <div class="bubble-content">
      <header class="bubble-meta">
        <span class="sender-name">
          {{ isNarration() ? "旁白" : (message.senderName ?? message.sender) }}
        </span>
        <span v-if="message.message_tag === 'reminder'" class="message-tag">🔔 提醒</span>
        <span v-else-if="message.message_tag === 'heartbeat'" class="message-tag">💓 巡检</span>
        <span v-else-if="sourceLabel()" class="message-tag">{{ sourceLabel() }}</span>
      </header>
      <div v-if="imageAttachments.length || fileAttachments.length" class="bubble-attachments">
        <MediaMessage
          v-for="(attachment, index) in imageAttachments"
          :key="`${attachment.url}-${index}`"
          :src="resolveAttachmentUrl(attachment.url)"
          media-type="image"
        />
        <FileAttachment
          v-for="(attachment, index) in fileAttachments"
          :key="`${attachment.url}-${index}`"
          :path="resolveAttachmentUrl(attachment.url)"
          :label="attachmentLabel(attachment.url, attachment.filename)"
        />
      </div>
      <slot />
      <div v-if="formattedTime" class="bubble-time">{{ formattedTime }}</div>
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
  min-width: 0;
  width: 100%;
}

.message-bubble[data-sender="user"] {
  margin-left: auto;
}

.message-bubble[data-sender="assistant"] {
  margin-right: auto;
}

.message-bubble[data-message-tag="narration"] {
  gap: 0.25rem;
  grid-template-columns: minmax(0, 1fr);
  max-width: min(70ch, 100%);
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
  display: grid;
  gap: 0.55rem;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  overflow-wrap: anywhere;
  word-break: break-word;
  padding: 0.6rem 0.72rem;
}

.message-bubble[data-message-tag="narration"] .bubble-content {
  background: transparent;
  border: none;
  padding: 0.1rem 0.2rem;
}

.message-bubble[data-sender="user"] .bubble-content {
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--brand) 26%, transparent),
    color-mix(in srgb, var(--brand-2) 30%, transparent)
  );
}

.message-bubble[data-message-tag="reminder"] .bubble-content {
  border-left: 3px solid color-mix(in srgb, var(--brand) 78%, transparent);
}

.message-bubble[data-message-tag="heartbeat"] .bubble-content {
  border-left: 3px solid color-mix(in srgb, var(--brand-2) 78%, transparent);
}

.bubble-meta {
  align-items: baseline;
  color: var(--muted);
  display: flex;
  font-size: 0.75rem;
  margin-bottom: 0.35rem;
}

.sender-name {
  font-weight: 700;
  text-transform: capitalize;
}

.message-tag {
  color: var(--muted);
  font-size: 0.7rem;
  font-weight: 600;
  margin-left: 0.4rem;
}

.bubble-time {
  color: color-mix(in srgb, var(--muted) 90%, transparent);
  display: flex;
  font-size: 0.75rem;
  margin-top: 0.45rem;
}

.bubble-attachments {
  display: grid;
  gap: 0.55rem;
  max-width: 100%;
  min-width: 0;
}

.message-bubble[data-sender="user"] .bubble-time {
  justify-content: flex-end;
}

.message-bubble[data-message-tag="narration"] .bubble-meta {
  font-size: 0.7rem;
  margin-bottom: 0.15rem;
}

.message-bubble[data-message-tag="narration"] .sender-name,
.message-bubble[data-message-tag="narration"] .message-tag,
.message-bubble[data-message-tag="narration"] .bubble-time {
  color: color-mix(in srgb, var(--muted) 85%, transparent);
}

.message-bubble[data-message-tag="narration"] :deep(.text-message) {
  color: var(--muted);
  font-size: 0.94rem;
  font-style: italic;
}

@media (max-width: 767px) {
  .message-bubble {
    max-width: 100%;
  }
}
</style>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useNotification } from "naive-ui";

import type { Attachment, ConnectionStatus } from "@/types/message";
import { useFileUpload } from "@/composables/useFileUpload";
import { resolveAssetUrl } from "@/utils/messageRouting";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    expanded?: boolean;
    apiBase: string;
    token: string;
    connectionStatus: ConnectionStatus;
    sendMessage: (text: string, attachments?: Attachment[]) => boolean;
  }>(),
  {
    expanded: false,
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  "update:expanded": [value: boolean];
}>();

const fileInputRef = ref<HTMLInputElement | null>(null);
const composerRef = ref<HTMLTextAreaElement | null>(null);

const notification = (() => {
  try {
    return useNotification();
  } catch {
    return null;
  }
})();

const {
  isComposerDragActive,
  isUploadingAttachments,
  onComposerDragLeave,
  onComposerDragOver,
  onComposerDrop,
  onComposerPaste,
  onFileInputChange,
  openFilePicker,
  pendingAttachments,
  removePendingAttachment,
} = useFileUpload({
  apiBase: props.apiBase,
  token: props.token,
  notification,
  fileInputRef,
});

const canSend = computed(
  () =>
    props.connectionStatus === "connected" &&
    !isUploadingAttachments.value &&
    (props.modelValue.trim().length > 0 || pendingAttachments.value.length > 0),
);

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

const focusComposer = (): void => {
  composerRef.value?.focus();
};

const submitMessage = (): boolean => {
  const didSend = props.sendMessage(props.modelValue, pendingAttachments.value);
  if (!didSend) {
    return false;
  }

  emit("update:modelValue", "");
  pendingAttachments.value = [];
  if (fileInputRef.value) {
    fileInputRef.value.value = "";
  }
  void nextTick(() => {
    adjustComposerHeight();
  });
  return true;
};

const toggleComposerExpanded = (): void => {
  emit("update:expanded", !props.expanded);
  void nextTick(() => {
    adjustComposerHeight();
    focusComposer();
  });
};

const collapseExpanded = (): boolean => {
  if (!props.expanded) {
    return false;
  }
  emit("update:expanded", false);
  return true;
};

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
  resolveAssetUrl(attachment.url, props.apiBase, props.token);

const attachmentLabel = (attachment: Attachment): string =>
  attachment.filename || attachment.url.split("/").pop() || attachment.url;

watch(
  () => props.modelValue,
  () => {
    void nextTick(() => {
      adjustComposerHeight();
    });
  },
  { immediate: true },
);

watch(
  () => props.expanded,
  () => {
    void nextTick(() => {
      adjustComposerHeight();
    });
  },
);

defineExpose({
  collapseExpanded,
  focusComposer,
  submitMessage,
});
</script>

<template>
  <form
    class="input-area"
    :data-drag-active="isComposerDragActive"
    :data-expanded="expanded"
    @dragenter.prevent="isComposerDragActive = true"
    @dragleave="onComposerDragLeave"
    @dragover="onComposerDragOver"
    @drop="onComposerDrop"
    @submit.prevent="submitMessage"
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
      :value="modelValue"
      name="message"
      autocomplete="off"
      placeholder="输入消息（Enter 发送）"
      class="composer-input"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
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
        {{ expanded ? "收起输入框" : "展开输入框" }}
      </button>
      <button type="button" class="ghost-button" @click="openFilePicker">
        📎
      </button>
      <button type="submit" :disabled="!canSend">Send</button>
    </div>
  </form>
</template>

<style scoped>
.input-area {
  background: color-mix(in srgb, var(--surface) 90%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 85%, transparent);
  border-radius: 1rem;
  display: grid;
  gap: 0.8rem;
  padding: 0.85rem;
}

.input-area[data-drag-active="true"] {
  border-color: color-mix(in srgb, var(--brand) 70%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand) 18%, transparent);
}

.composer-file-input {
  height: 0;
  opacity: 0;
  pointer-events: none;
  position: absolute;
  width: 0;
}

.composer-input {
  background: transparent;
  border: none;
  color: var(--text);
  font: inherit;
  line-height: 1.65;
  min-height: 3.6rem;
  outline: none;
  resize: none;
  width: 100%;
}

.input-area[data-expanded="true"] .composer-input {
  min-height: 8rem;
}

.composer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
  justify-content: flex-end;
}

.composer-attachments {
  display: grid;
  gap: 0.55rem;
}

.composer-attachment {
  align-items: center;
  background: color-mix(in srgb, var(--panel) 78%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 72%, transparent);
  border-radius: 0.85rem;
  display: grid;
  gap: 0.65rem;
  grid-template-columns: auto 1fr auto;
  padding: 0.55rem 0.7rem;
}

.composer-attachment-thumb {
  border-radius: 0.55rem;
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-attachment-meta span {
  color: var(--muted);
  font-size: 0.78rem;
}

.attachment-remove,
.composer-actions button {
  align-items: center;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 82%, transparent);
  border-radius: 0.7rem;
  color: var(--text);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 0.82rem;
  font-weight: 600;
  gap: 0.35rem;
  justify-content: center;
  min-height: 2.25rem;
  padding: 0.4rem 0.8rem;
}

.composer-actions .ghost-button {
  background: transparent;
}

.composer-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

@media (max-width: 767px) {
  .composer-actions {
    justify-content: stretch;
  }

  .composer-actions button {
    flex: 1 1 0;
  }
}
</style>

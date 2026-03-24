<script setup lang="ts">
import { computed, ref } from "vue";

import TextMessage from "./TextMessage.vue";

const props = defineProps<{
  content: string;
  showSourceToggle?: boolean;
}>();

const showSource = ref(false);
const toggleSource = (): void => {
  showSource.value = !showSource.value;
};

const buttonLabel = computed(() => (showSource.value ? "预览" : "<> 源码"));
</script>

<template>
  <section class="markdown-preview">
    <header v-if="showSourceToggle" class="preview-head">
      <button type="button" class="toggle-button" @click="toggleSource">
        {{ buttonLabel }}
      </button>
    </header>

    <pre v-if="showSource" class="source-content"><code>{{ content }}</code></pre>
    <TextMessage v-else :text="content" />
  </section>
</template>

<style scoped>
.preview-head {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 0.35rem;
}

.markdown-preview {
  max-width: 100%;
  min-width: 0;
}

.toggle-button {
  background: color-mix(in srgb, var(--surface) 72%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 85%, transparent);
  border-radius: 0.45rem;
  color: var(--text);
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 600;
  opacity: 0.82;
  padding: 0.18rem 0.46rem;
  transition:
    opacity 0.2s ease,
    border-color 0.2s ease,
    background-color 0.2s ease;
}

.toggle-button:hover {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border-color: color-mix(in srgb, var(--brand) 55%, var(--panel-edge));
  opacity: 1;
}

.source-content {
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 0.8rem;
  margin: 0;
  max-height: 24rem;
  max-width: 100%;
  overflow: auto;
  padding: 0.8rem 0.9rem;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>

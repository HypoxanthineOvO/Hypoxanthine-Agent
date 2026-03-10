<script setup lang="ts">
import { computed, ref } from "vue";

import TextMessage from "./TextMessage.vue";

const props = defineProps<{
  content: string;
}>();

const showSource = ref(false);
const toggleSource = (): void => {
  showSource.value = !showSource.value;
};

const buttonLabel = computed(() => (showSource.value ? "预览" : "<> 源码"));
</script>

<template>
  <section class="markdown-preview">
    <header class="preview-head">
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

.toggle-button {
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 85%, transparent);
  border-radius: 0.45rem;
  color: var(--text);
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.15rem 0.4rem;
}

.source-content {
  margin: 0;
  max-height: 24rem;
  overflow: auto;
}
</style>

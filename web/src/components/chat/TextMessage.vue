<script setup lang="ts">
import { onMounted, ref, watch } from "vue";

import { renderMarkdown, renderMermaidIn } from "../../utils/markdownRenderer";

const props = defineProps<{
  text: string;
}>();

const root = ref<HTMLElement | null>(null);

const renderMermaidIfNeeded = async (): Promise<void> => {
  if (!root.value) {
    return;
  }
  await renderMermaidIn(root.value);
};

onMounted(() => {
  void renderMermaidIfNeeded();
});

watch(
  () => props.text,
  () => {
    void renderMermaidIfNeeded();
  },
);
</script>

<template>
  <div ref="root" class="text-message markdown-body" v-html="renderMarkdown(text)" />
</template>

<style scoped>
.text-message {
  line-height: 1.5;
}

.text-message :deep(p) {
  margin: 0.2rem 0;
}
</style>

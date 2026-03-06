<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import { renderMarkdown, renderMermaidIn } from "../../utils/markdownRenderer";

const props = defineProps<{
  text: string;
}>();

const root = ref<HTMLElement | null>(null);

const onRootClick = (event: MouseEvent): void => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const button = target.closest(".copy-btn");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  const text = button.dataset.code ?? "";
  if (!navigator.clipboard?.writeText) {
    return;
  }

  void navigator.clipboard.writeText(text);
};

const renderMermaidIfNeeded = async (): Promise<void> => {
  if (!root.value) {
    return;
  }
  await renderMermaidIn(root.value);
};

onMounted(() => {
  root.value?.addEventListener("click", onRootClick);
  void renderMermaidIfNeeded();
});

onBeforeUnmount(() => {
  root.value?.removeEventListener("click", onRootClick);
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

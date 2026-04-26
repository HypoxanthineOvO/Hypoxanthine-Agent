<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import {
  renderMarkdown,
  renderMathIn,
  renderMermaidIn,
  shouldRenderEnhancedMarkdown,
} from "../../utils/markdownRenderer";

const props = defineProps<{
  text: string;
  cacheKey?: string;
  cacheVersion?: number | string;
  streaming?: boolean;
}>();

const root = ref<HTMLElement | null>(null);
const renderedHtml = computed(() =>
  renderMarkdown(props.text, {
    cacheKey: props.cacheKey,
    version: props.cacheVersion,
    streaming: props.streaming === true,
  }),
);

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
  if (!shouldRenderEnhancedMarkdown(props.text, { streaming: props.streaming === true })) {
    return;
  }
  await renderMathIn(root.value);
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
  () => [props.text, props.streaming] as const,
  () => {
    void renderMermaidIfNeeded();
  },
  { flush: "post" },
);
</script>

<template>
  <div ref="root" class="text-message markdown-body" v-html="renderedHtml" />
</template>

<style scoped>
.text-message {
  line-height: 1.5;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  overflow-wrap: break-word;
  word-break: break-word;
}

.text-message :deep(p) {
  margin: 0.2rem 0;
}

.text-message :deep(a) {
  overflow-wrap: anywhere;
  word-break: break-word;
}
</style>

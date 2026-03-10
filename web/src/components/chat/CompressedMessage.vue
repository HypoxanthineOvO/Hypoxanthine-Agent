<script setup lang="ts">
import { computed, ref } from "vue";

import CodeBlock from "./CodeBlock.vue";
import MarkdownPreview from "./MarkdownPreview.vue";
import type { CompressedMeta } from "../../types/message";

const props = withDefaults(
  defineProps<{
    summary: string;
    compressedMeta?: CompressedMeta;
    apiBase?: string;
    token?: string;
    toolName?: string;
    filePath?: string;
  }>(),
  {
    apiBase: "/api",
    token: "",
    toolName: "",
    filePath: "",
  },
);

const expanded = ref(false);
const loading = ref(false);
const originalContent = ref<string>("");
const loadError = ref<string>("");
const hasLoaded = ref(false);

const toggleExpanded = (): void => {
  expanded.value = !expanded.value;
  if (expanded.value) {
    void loadOriginal();
  }
};

const metaText = computed(() => {
  if (!props.compressedMeta) {
    return "";
  }
  return `${props.compressedMeta.original_chars} → ${props.compressedMeta.compressed_chars} 字符`;
});

const fileExt = computed(() => {
  const lowered = props.filePath.toLowerCase();
  const parts = lowered.split(".");
  return parts.length > 1 ? parts[parts.length - 1] ?? "" : "";
});

const renderMode = computed<"markdown" | "code">(() => {
  if (fileExt.value === "md") {
    return "markdown";
  }
  return "code";
});

const language = computed(() => {
  if (props.toolName === "run_command") {
    return "bash";
  }
  if (props.toolName === "run_code") {
    return "python";
  }
  if (fileExt.value === "py") {
    return "python";
  }
  if (fileExt.value === "yaml" || fileExt.value === "yml") {
    return "yaml";
  }
  if (fileExt.value === "json") {
    return "json";
  }
  return "text";
});

const loadOriginal = async (): Promise<void> => {
  if (!props.compressedMeta || hasLoaded.value || loading.value) {
    return;
  }

  loading.value = true;
  loadError.value = "";
  try {
    const response = await fetch(
      `${props.apiBase}/compressed/${encodeURIComponent(props.compressedMeta.cache_id)}?token=${encodeURIComponent(props.token)}`,
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = (await response.json()) as { original_output?: unknown };
    originalContent.value =
      typeof payload.original_output === "string" ? payload.original_output : "";
    hasLoaded.value = true;
  } catch {
    loadError.value = "原文加载失败，请稍后重试。";
  } finally {
    loading.value = false;
  }
};
</script>

<template>
  <section class="compressed-message">
    <p class="summary-text">{{ summary }}</p>
    <p v-if="metaText" class="summary-meta">{{ metaText }}</p>
    <button type="button" class="source-button" @click="toggleExpanded">
      📄 查看原文
    </button>
    <div v-if="expanded" class="original-slot">
      <p v-if="loading" class="placeholder">正在加载原文...</p>
      <p v-else-if="loadError" class="error-text">{{ loadError }}</p>
      <MarkdownPreview
        v-else-if="renderMode === 'markdown' && originalContent"
        :content="originalContent"
      />
      <CodeBlock
        v-else-if="originalContent"
        :code="originalContent"
        :language="language"
      />
      <p v-else class="placeholder">暂无原文内容。</p>
    </div>
  </section>
</template>

<style scoped>
.summary-text {
  margin: 0 0 0.2rem;
}

.summary-meta {
  color: var(--muted);
  font-size: 0.78rem;
  margin: 0 0 0.35rem;
}

.source-button {
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-radius: 0.45rem;
  color: var(--text);
  cursor: pointer;
  font-size: 0.76rem;
  font-weight: 600;
  padding: 0.16rem 0.45rem;
}

.original-slot {
  margin-top: 0.45rem;
  max-height: 40vh;
  overflow-y: auto;
  border: 1px solid var(--panel-edge);
  border-radius: 0.5rem;
  padding: 0.5rem;
}

.original-slot :deep(pre) {
  white-space: pre-wrap;
  word-break: break-all;
}

.placeholder {
  color: var(--muted);
  font-size: 0.8rem;
  margin: 0;
}

.error-text {
  color: var(--error);
  font-size: 0.8rem;
  margin: 0;
}
</style>

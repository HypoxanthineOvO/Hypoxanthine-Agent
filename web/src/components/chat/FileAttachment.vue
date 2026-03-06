<script setup lang="ts">
import { computed } from "vue";

import CodeBlock from "./CodeBlock.vue";

const props = defineProps<{
  path: string;
  content?: string;
}>();

const fileName = computed(() => props.path.split("/").pop() || props.path);
const ext = computed(() => {
  const parts = fileName.value.toLowerCase().split(".");
  return parts.length > 1 ? parts[parts.length - 1] ?? "" : "";
});

const isCodeFile = computed(() =>
  /^(py|ya?ml|json|ts|js|sh|toml|ini)$/.test(ext.value),
);

const language = computed(() => {
  if (ext.value === "py") {
    return "python";
  }
  if (ext.value === "yaml" || ext.value === "yml") {
    return "yaml";
  }
  if (ext.value === "json") {
    return "json";
  }
  if (ext.value === "sh") {
    return "bash";
  }
  return ext.value || "text";
});
</script>

<template>
  <section class="file-attachment">
    <header v-if="isCodeFile && content" class="file-head">
      <span class="file-title">{{ fileName }}</span>
    </header>
    <CodeBlock
      v-if="isCodeFile && content"
      :code="content"
      :language="language"
    />
    <a v-else :href="path" target="_blank" rel="noopener" class="file-link">
      📎 {{ fileName }}
    </a>
  </section>
</template>

<style scoped>
.file-head {
  margin-bottom: 0.35rem;
}

.file-title {
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 700;
}

.file-link {
  color: var(--brand);
  font-size: 0.84rem;
  font-weight: 600;
  text-decoration: none;
}

.file-link:hover {
  text-decoration: underline;
}
</style>

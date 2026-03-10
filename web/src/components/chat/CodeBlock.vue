<script setup lang="ts">
import { computed, ref } from "vue";

const props = withDefaults(
  defineProps<{
    code: string;
    language?: string;
  }>(),
  {
    language: "text",
  },
);

const copied = ref(false);

const normalizedLanguage = computed(() => {
  const raw = props.language?.trim() || "text";
  return raw.toUpperCase();
});

const codeLines = computed(() => {
  if (props.code.length === 0) {
    return [""];
  }
  return props.code.split("\n");
});

const copyCode = async (): Promise<void> => {
  if (!navigator.clipboard?.writeText) {
    return;
  }
  await navigator.clipboard.writeText(props.code);
  copied.value = true;
  setTimeout(() => {
    copied.value = false;
  }, 1200);
};
</script>

<template>
  <section class="code-block">
    <header class="code-header">
      <span class="language-label">{{ normalizedLanguage }}</span>
      <button
        type="button"
        class="copy-button"
        data-testid="copy-code-button"
        @click="copyCode"
      >
        {{ copied ? "已复制" : "复制" }}
      </button>
    </header>

    <pre class="code-content"><code>
<span
  v-for="(line, index) in codeLines"
  :key="`${index}-${line}`"
  class="code-line"
><span class="line-number">{{ index + 1 }}</span><span class="line-text">{{ line }}</span></span>
    </code></pre>
  </section>
</template>

<style scoped>
.code-block {
  background: color-mix(in srgb, var(--surface) 75%, #000 25%);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 0.75rem;
  overflow: hidden;
}

.code-header {
  align-items: center;
  background: color-mix(in srgb, var(--panel) 84%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--panel-edge) 92%, transparent);
  display: flex;
  justify-content: space-between;
  padding: 0.42rem 0.55rem;
}

.language-label {
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.copy-button {
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 80%, transparent);
  border-radius: 0.45rem;
  color: var(--text);
  cursor: pointer;
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.12rem 0.4rem;
}

.copy-button:hover {
  border-color: color-mix(in srgb, var(--brand) 50%, var(--panel-edge));
}

.code-content {
  margin: 0;
  overflow: auto;
  padding: 0.6rem 0.6rem 0.7rem;
}

.code-line {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: 2.4rem 1fr;
  min-height: 1.4rem;
}

.line-number {
  color: color-mix(in srgb, var(--muted) 74%, transparent);
  text-align: right;
  user-select: none;
}

.line-text {
  white-space: pre;
}
</style>

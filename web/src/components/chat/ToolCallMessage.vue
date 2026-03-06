<script setup lang="ts">
import { computed, ref } from "vue";

const props = withDefaults(
  defineProps<{
    toolName: string;
    status?: string;
    params?: Record<string, unknown>;
    result?: unknown;
  }>(),
  {
    status: "success",
    params: () => ({}),
    result: undefined,
  },
);

const expanded = ref(false);

const toggleExpanded = (): void => {
  expanded.value = !expanded.value;
};

const statusLabel = computed(() => (props.status === "success" ? "成功" : "失败"));

const summary = computed(() => {
  const rawArgs = JSON.stringify(props.params);
  const argsPreview =
    rawArgs.length > 120 ? `${rawArgs.slice(0, 120)}…` : rawArgs;
  return `🔧 执行了 ${props.toolName}(${argsPreview}) → ${statusLabel.value}`;
});

const resultText = computed(() => {
  if (typeof props.result === "string") {
    return props.result;
  }
  if (props.result === undefined) {
    return "";
  }
  return JSON.stringify(props.result, null, 2);
});
</script>

<template>
  <section class="tool-call-message">
    <button
      type="button"
      class="summary-button"
      :aria-expanded="expanded"
      @click="toggleExpanded"
    >
      {{ summary }}
    </button>

    <div v-if="expanded" class="details">
      <h5 class="detail-title">参数</h5>
      <pre class="detail-block"><code>{{ JSON.stringify(params, null, 2) }}</code></pre>
      <h5 class="detail-title">输出</h5>
      <pre class="detail-block"><code>{{ resultText }}</code></pre>
    </div>
  </section>
</template>

<style scoped>
.summary-button {
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 88%, transparent);
  border-left: 3px solid color-mix(in srgb, var(--brand) 65%, transparent);
  border-radius: 0.65rem;
  color: var(--text);
  cursor: pointer;
  display: flex;
  font-size: 0.82rem;
  font-weight: 600;
  padding: 0.35rem 0.5rem;
  position: relative;
  text-align: left;
  width: 100%;
}

.summary-button::after {
  content: "▶";
  font-size: 0.65rem;
  margin-left: auto;
  opacity: 0.5;
  transition: transform 0.2s;
}

.summary-button[aria-expanded="true"]::after {
  transform: rotate(90deg);
}

.details {
  margin-top: 0.42rem;
}

.detail-title {
  font-size: 0.74rem;
  margin: 0.15rem 0;
}

.detail-block {
  margin: 0;
  max-height: 14rem;
  overflow: auto;
}
</style>

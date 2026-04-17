<script setup lang="ts">
import { computed } from "vue";

import type { Message, PipelineProgressItem } from "@/types/message";

const props = defineProps<{
  message: Message;
}>();

const items = computed<PipelineProgressItem[]>(() => {
  const raw = props.message.metadata?.pipeline_items;
  if (!Array.isArray(raw)) {
    return [];
  }
  return (raw as PipelineProgressItem[]).filter((item) => item.stage !== "preprocessing");
});

const collapsed = computed(() => props.message.metadata?.pipeline_collapsed === true);
const summary = computed(() => String(props.message.text ?? "").trim());
</script>

<template>
  <section
    class="pipeline-progress"
    :class="{ 'is-collapsed': collapsed }"
    data-testid="pipeline-progress-card"
  >
    <header class="progress-header">
      <span class="progress-label">Pipeline</span>
      <span class="progress-state">{{ collapsed ? "已折叠" : "进行中" }}</span>
    </header>
    <p v-if="summary" class="progress-summary">{{ summary }}</p>
    <ul v-if="items.length" class="progress-list">
      <li
        v-for="(item, index) in items"
        :key="`${item.event_type}-${item.timestamp ?? index}-${index}`"
        class="progress-item"
        :data-status="item.status"
      >
        <span class="progress-dot" />
        <span class="progress-text">{{ item.text }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.pipeline-progress {
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--surface) 94%, transparent), transparent),
    color-mix(in srgb, var(--panel) 86%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 76%, transparent);
  border-radius: 0.95rem;
  display: grid;
  gap: 0.6rem;
  padding: 0.72rem 0.82rem;
  transition:
    opacity 0.24s ease,
    transform 0.24s ease,
    border-color 0.24s ease;
}

.pipeline-progress.is-collapsed {
  opacity: 0.72;
}

.progress-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.progress-label,
.progress-state {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.progress-summary {
  color: var(--text);
  font-size: 0.92rem;
  font-weight: 600;
  margin: 0;
}

.progress-list {
  display: grid;
  gap: 0.42rem;
  list-style: none;
  margin: 0;
  padding: 0;
}

.progress-item {
  align-items: center;
  color: var(--text-soft);
  display: grid;
  gap: 0.55rem;
  grid-template-columns: auto 1fr;
  line-height: 1.4;
}

.progress-item[data-status="warning"] {
  background: color-mix(in srgb, #f08c00 10%, transparent);
  border: 1px solid color-mix(in srgb, #f08c00 28%, transparent);
  border-radius: 0.72rem;
  padding: 0.5rem 0.6rem;
}

.progress-item[data-status="error"] {
  background: color-mix(in srgb, #d9480f 9%, transparent);
  border: 1px solid color-mix(in srgb, #d9480f 24%, transparent);
  border-radius: 0.72rem;
  padding: 0.5rem 0.6rem;
}

.progress-dot {
  background: color-mix(in srgb, var(--brand) 72%, transparent);
  border-radius: 999px;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand) 14%, transparent);
  height: 0.45rem;
  margin-top: 0.2rem;
  width: 0.45rem;
}

.progress-item[data-status="success"] .progress-dot {
  background: color-mix(in srgb, #2f9e44 76%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, #2f9e44 14%, transparent);
}

.progress-item[data-status="error"] .progress-dot {
  background: color-mix(in srgb, #d9480f 78%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, #d9480f 12%, transparent);
}

.progress-item[data-status="warning"] .progress-dot {
  background: color-mix(in srgb, #f08c00 80%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, #f08c00 14%, transparent);
}

.progress-text {
  font-size: 0.84rem;
}
</style>

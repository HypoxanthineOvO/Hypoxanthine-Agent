<script setup lang="ts">
defineProps<{
  taskId: string;
  status: string;
  summary: string;
}>();
</script>

<template>
  <section class="codex-status-card" data-testid="codex-status-card">
    <div class="status-head">
      <span class="status-title">Codex 任务</span>
      <span class="status-pill">{{ status }}</span>
    </div>
    <div class="task-id">{{ taskId }}</div>
    <p>{{ summary }}</p>
  </section>
</template>

<style scoped>
.codex-status-card {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 86%, transparent);
  border-radius: 8px;
  display: grid;
  gap: 0.35rem;
  max-width: 100%;
  min-width: 0;
  padding: 0.62rem 0.72rem;
}

.status-head {
  align-items: center;
  display: flex;
  gap: 0.55rem;
  justify-content: space-between;
  min-width: 0;
}

.status-title {
  font-size: 0.78rem;
  font-weight: 700;
}

.status-pill {
  border: 1px solid color-mix(in srgb, var(--brand) 44%, var(--panel-edge));
  border-radius: 999px;
  color: var(--text-soft);
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.12rem 0.45rem;
  text-transform: lowercase;
}

.task-id {
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 0.76rem;
  overflow-wrap: anywhere;
}

p {
  color: var(--text);
  margin: 0;
  overflow-wrap: anywhere;
}
</style>

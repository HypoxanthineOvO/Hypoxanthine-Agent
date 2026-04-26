<script setup lang="ts">
import type { CoderTaskRow } from "@/composables/useSessionHistory";

defineProps<{
  tasks: CoderTaskRow[];
}>();

const isAttached = (task: CoderTaskRow): boolean => task.attached === true || task.attached === 1;
const isDone = (task: CoderTaskRow): boolean => task.done === true || task.done === 1;
</script>

<template>
  <aside v-if="tasks.length > 0" class="codex-job-panel" data-testid="codex-job-panel">
    <header>
      <div>
        <p>Codex Jobs</p>
        <strong>{{ tasks.length }} active/history</strong>
      </div>
    </header>
    <div class="job-list">
      <article v-for="task in tasks.slice(0, 4)" :key="task.task_id" class="job-row">
        <div class="job-main">
          <span class="job-id">{{ task.task_id }}</span>
          <span class="job-summary">{{ task.prompt_summary || task.working_directory }}</span>
          <small>{{ task.working_directory }}</small>
        </div>
        <div class="job-state">
          <span class="status-pill">{{ task.status }}</span>
          <span v-if="isAttached(task)" class="state-mark">attached</span>
          <span v-if="isDone(task)" class="state-mark">done</span>
        </div>
      </article>
    </div>
  </aside>
</template>

<style scoped>
.codex-job-panel {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 85%, transparent);
  border-radius: 8px;
  display: grid;
  gap: 0.65rem;
  padding: 0.75rem;
}

header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

header p,
header strong {
  margin: 0;
}

header p {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
}

header strong {
  font-size: 0.88rem;
}

.job-list {
  display: grid;
  gap: 0.45rem;
}

.job-row {
  align-items: center;
  border-top: 1px solid color-mix(in srgb, var(--panel-edge) 70%, transparent);
  display: grid;
  gap: 0.65rem;
  grid-template-columns: minmax(0, 1fr) auto;
  padding-top: 0.5rem;
}

.job-main {
  display: grid;
  gap: 0.16rem;
  min-width: 0;
}

.job-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}

.job-summary,
small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-summary {
  color: var(--text);
  font-size: 0.82rem;
}

small {
  color: var(--muted);
}

.job-state {
  align-items: flex-end;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.status-pill,
.state-mark {
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 700;
  padding: 0.12rem 0.45rem;
}

.status-pill {
  border: 1px solid color-mix(in srgb, var(--brand) 48%, var(--panel-edge));
}

.state-mark {
  color: var(--muted);
}

@media (max-width: 767px) {
  .job-row {
    grid-template-columns: 1fr;
  }

  .job-state {
    align-items: flex-start;
    flex-direction: row;
    flex-wrap: wrap;
  }
}
</style>

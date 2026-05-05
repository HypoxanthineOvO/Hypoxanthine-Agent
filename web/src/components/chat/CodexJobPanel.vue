<script setup lang="ts">
import type { CoderTaskRow } from "@/composables/useSessionHistory";

defineProps<{
  tasks: CoderTaskRow[];
  open: boolean;
}>();

defineEmits<{
  close: [];
  open: [];
}>();

const isAttached = (task: CoderTaskRow): boolean => task.attached === true || task.attached === 1;
const isDone = (task: CoderTaskRow): boolean => task.done === true || task.done === 1;
</script>

<template>
  <button
    v-if="tasks.length > 0 && !open"
    type="button"
    class="codex-job-trigger"
    data-testid="codex-job-trigger"
    aria-haspopup="dialog"
    aria-controls="codex-job-panel"
    @click="$emit('open')"
  >
    <span>Codex Jobs</span>
    <strong>{{ tasks.length }}</strong>
  </button>

  <aside
    v-if="tasks.length > 0 && open"
    id="codex-job-panel"
    class="codex-job-panel"
    data-testid="codex-job-panel"
    role="dialog"
    aria-label="Codex Jobs"
  >
    <header>
      <div>
        <p>Codex Jobs</p>
        <strong>{{ tasks.length }} active/history</strong>
      </div>
      <button
        type="button"
        class="codex-job-close"
        data-testid="codex-job-close"
        aria-label="Close Codex Jobs"
        @click="$emit('close')"
      >
        ×
      </button>
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
  box-shadow: 0 18px 50px color-mix(in srgb, black 38%, transparent);
  display: grid;
  gap: 0.65rem;
  max-height: min(520px, calc(100vh - 10rem));
  overflow: auto;
  padding: 0.75rem;
  position: absolute;
  right: 1.2rem;
  top: 5.8rem;
  width: min(420px, calc(100% - 2.4rem));
  z-index: 15;
}

.codex-job-trigger {
  align-items: center;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 78%, transparent);
  border-radius: 999px;
  box-shadow: 0 12px 32px color-mix(in srgb, black 28%, transparent);
  color: var(--text);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 800;
  gap: 0.45rem;
  padding: 0.42rem 0.72rem;
  position: absolute;
  right: 1.2rem;
  top: 5.8rem;
  z-index: 10;
}

.codex-job-trigger strong {
  align-items: center;
  background: color-mix(in srgb, var(--brand) 32%, transparent);
  border-radius: 999px;
  display: inline-flex;
  height: 1.25rem;
  justify-content: center;
  min-width: 1.25rem;
  padding: 0 0.32rem;
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

.codex-job-close {
  align-items: center;
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--panel-edge) 78%, transparent);
  border-radius: 999px;
  color: var(--text);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 1rem;
  height: 1.8rem;
  justify-content: center;
  line-height: 1;
  width: 1.8rem;
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
  .codex-job-panel,
  .codex-job-trigger {
    right: 1rem;
    top: 8.25rem;
  }

  .codex-job-panel {
    max-height: min(460px, calc(100vh - 11rem));
    width: calc(100% - 2rem);
  }

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

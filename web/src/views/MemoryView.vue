<script setup lang="ts">
import {
  NButton,
  NCard,
  NDataTable,
  NInput,
  NInputGroup,
  NInputNumber,
  NSelect,
  NTabPane,
  NTabs,
  useMessage,
} from "naive-ui";
import { computed, onMounted, ref, watch } from "vue";

import MonacoEditor from "../components/editor/MonacoEditor.vue";
import { apiGetJson } from "../utils/apiClient";

const props = withDefaults(
  defineProps<{
    token: string;
    apiBase?: string;
  }>(),
  {
    apiBase: "",
  },
);

interface SessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface MemoryTableSummary {
  name: string;
  row_count: number;
  writable: boolean;
}

interface MemoryTableData {
  table: string;
  page: number;
  size: number;
  total: number;
  writable: boolean;
  rows: Array<Record<string, unknown>>;
}

const message = (() => {
  try {
    return useMessage();
  } catch {
    return {
      success: () => undefined,
      error: () => undefined,
      warning: () => undefined,
    };
  }
})();
const activeTab = ref<"l1" | "l2" | "l3">("l1");

const normalizedApiBase = computed(() => {
  const explicitBase = props.apiBase.trim();
  if (explicitBase) {
    return explicitBase.replace(/\/+$/, "");
  }
  return "/api";
});

const withToken = (path: string): string => {
  const base = `${normalizedApiBase.value}/${path.replace(/^\/+/, "")}`;
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}token=${encodeURIComponent(props.token)}`;
};

// L1 sessions
const sessions = ref<SessionSummary[]>([]);
const activeSessionId = ref("");
const sessionMessages = ref<Array<Record<string, unknown>>>([]);
const exportFormat = ref<"json" | "markdown">("json");

// L2 sqlite
const tables = ref<MemoryTableSummary[]>([]);
const activeTable = ref("preferences");
const tablePage = ref(1);
const tableSize = ref(50);
const tableData = ref<MemoryTableData | null>(null);
const editingPrefKey = ref("");
const editingPrefValue = ref("");

// L3 knowledge
const files = ref<string[]>([]);
const fileFilter = ref("");
const activeFile = ref("");
const fileContent = ref("");

const filteredFiles = computed(() =>
  files.value.filter((item) => item.toLowerCase().includes(fileFilter.value.toLowerCase())),
);

const tableRows = computed(() => tableData.value?.rows ?? []);
const tableColumns = computed(() => {
  if (tableRows.value.length === 0) return [];
  const firstRow = tableRows.value[0];
  if (!firstRow) return [];
  return Object.keys(firstRow).map((key) => ({
    title: key,
    key,
    ellipsis: { tooltip: true },
  }));
});

const loadSessions = async (): Promise<void> => {
  const data = await apiGetJson<SessionSummary[]>(withToken("sessions"));
  sessions.value = data;
  if (!activeSessionId.value && data.length > 0) {
    activeSessionId.value = data[0]?.session_id ?? "";
  }
};

const loadSessionMessages = async (): Promise<void> => {
  if (!activeSessionId.value) {
    sessionMessages.value = [];
    return;
  }
  const data = await apiGetJson<Array<Record<string, unknown>>>(
    withToken(`sessions/${encodeURIComponent(activeSessionId.value)}/messages`),
  );
  sessionMessages.value = data;
};

const deleteSession = async (): Promise<void> => {
  if (!activeSessionId.value) {
    return;
  }
  const response = await fetch(
    withToken(`sessions/${encodeURIComponent(activeSessionId.value)}`),
    { method: "DELETE" },
  );
  if (!response.ok) {
    message.error("删除会话失败");
    return;
  }
  message.success("会话已删除");
  activeSessionId.value = "";
  await loadSessions();
  await loadSessionMessages();
};

const exportSession = async (): Promise<void> => {
  if (!activeSessionId.value) {
    return;
  }
  const url = withToken(
    `sessions/${encodeURIComponent(activeSessionId.value)}/export?format=${exportFormat.value}`,
  );
  const response = await fetch(url);
  if (!response.ok) {
    message.error("导出失败");
    return;
  }
  const content = await response.text();
  fileContent.value = content;
  activeTab.value = "l3";
  activeFile.value = `export-${activeSessionId.value}.${exportFormat.value === "json" ? "json" : "md"}`;
  message.success("导出内容已加载到编辑区");
};

const loadTables = async (): Promise<void> => {
  const data = await apiGetJson<{ tables: MemoryTableSummary[] }>(withToken("memory/tables"));
  tables.value = data.tables;
  if (!tables.value.some((item) => item.name === activeTable.value) && tables.value.length > 0) {
    activeTable.value = tables.value[0]?.name ?? "preferences";
  }
};

const loadTableData = async (): Promise<void> => {
  if (!activeTable.value) {
    tableData.value = null;
    return;
  }
  tableData.value = await apiGetJson<MemoryTableData>(
    withToken(
      `memory/tables/${encodeURIComponent(activeTable.value)}?page=${tablePage.value}&size=${tableSize.value}`,
    ),
  );
  if (activeTable.value === "preferences" && tableData.value.rows.length > 0) {
    const first = tableData.value.rows[0];
    editingPrefKey.value = String(first?.pref_key ?? "");
    editingPrefValue.value = String(first?.pref_value ?? "");
  }
};

const savePreference = async (): Promise<void> => {
  if (!editingPrefKey.value) {
    return;
  }
  const response = await fetch(
    withToken(`memory/tables/preferences/${encodeURIComponent(editingPrefKey.value)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        values: {
          pref_value: editingPrefValue.value,
        },
      }),
    },
  );
  if (!response.ok) {
    message.error("保存 preference 失败");
    return;
  }
  message.success("preference 已更新");
  await loadTableData();
};

const loadFiles = async (): Promise<void> => {
  const data = await apiGetJson<{ files: string[] }>(withToken("memory/files"));
  files.value = data.files;
  if (!activeFile.value && files.value.length > 0) {
    activeFile.value = files.value[0] ?? "";
  }
};

const loadFileContent = async (): Promise<void> => {
  if (!activeFile.value || activeFile.value.startsWith("export-")) {
    return;
  }
  const data = await apiGetJson<{ content: string }>(
    withToken(`memory/files/${activeFile.value}`),
  );
  fileContent.value = data.content;
};

const saveFileContent = async (): Promise<void> => {
  if (!activeFile.value || activeFile.value.startsWith("export-")) {
    message.warning("导出缓冲文件不可直接保存到知识库");
    return;
  }
  const response = await fetch(withToken(`memory/files/${activeFile.value}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: fileContent.value }),
  });
  if (!response.ok) {
    message.error("文件保存失败");
    return;
  }
  message.success("文件已保存");
};

onMounted(async () => {
  await Promise.all([loadSessions(), loadTables(), loadFiles()]);
  await Promise.all([loadSessionMessages(), loadTableData(), loadFileContent()]);
});

watch(activeSessionId, () => {
  void loadSessionMessages();
});

watch([activeTable, tablePage, tableSize], () => {
  void loadTableData();
});

watch(activeFile, () => {
  void loadFileContent();
});
</script>

<template>
  <section class="memory-view">
    <n-tabs v-model:value="activeTab" type="line" animated>
      <n-tab-pane name="l1" tab="L1 会话">
        <n-card title="会话管理">
          <div class="l1-layout">
            <aside class="session-list">
              <button
                v-for="item in sessions"
                :key="item.session_id"
                type="button"
                class="session-item"
                :data-active="item.session_id === activeSessionId"
                @click="activeSessionId = item.session_id"
              >
                <span>{{ item.session_id }}</span>
                <small>{{ item.message_count }} msgs</small>
              </button>
            </aside>
            <main class="session-main">
              <div class="actions">
                <n-select
                  v-model:value="exportFormat"
                  :options="[
                    { label: 'JSON', value: 'json' },
                    { label: 'Markdown', value: 'markdown' },
                  ]"
                  style="width: 140px"
                />
                <n-button @click="exportSession">导出</n-button>
                <n-button type="error" secondary @click="deleteSession">删除会话</n-button>
              </div>
              <MonacoEditor
                :model-value="JSON.stringify(sessionMessages, null, 2)"
                language="json"
                height="360px"
                :options="{ readOnly: true }"
              />
            </main>
          </div>
        </n-card>
      </n-tab-pane>

      <n-tab-pane name="l2" tab="L2 SQLite">
        <n-card title="表浏览器">
          <div class="l2-layout">
            <aside class="table-list">
              <button
                v-for="table in tables"
                :key="table.name"
                type="button"
                class="table-item"
                :data-active="table.name === activeTable"
                @click="activeTable = table.name"
              >
                <span>{{ table.name }}</span>
                <small>{{ table.row_count }} rows</small>
              </button>
            </aside>
            <main class="table-main">
              <div class="actions">
                <n-input-number v-model:value="tablePage" :min="1" />
                <n-input-number v-model:value="tableSize" :min="1" :max="200" />
              </div>
              <n-data-table
                :columns="tableColumns"
                :data="tableRows"
                :max-height="360"
                virtual-scroll
              />
              <div v-if="activeTable === 'preferences'" class="edit-box">
                <h4>编辑 preferences</h4>
                <n-input-group>
                  <n-input v-model:value="editingPrefKey" placeholder="pref_key" />
                  <n-input v-model:value="editingPrefValue" placeholder="pref_value" />
                  <n-button type="primary" @click="savePreference">保存</n-button>
                </n-input-group>
              </div>
            </main>
          </div>
        </n-card>
      </n-tab-pane>

      <n-tab-pane name="l3" tab="L3 知识库">
        <n-card title="Markdown 编辑器">
          <div class="l3-layout">
            <aside class="file-side">
              <n-input
                v-model:value="fileFilter"
                placeholder="搜索文件名"
                clearable
              />
              <button
                v-for="path in filteredFiles"
                :key="path"
                type="button"
                class="file-item"
                :data-active="path === activeFile"
                @click="activeFile = path"
              >
                {{ path }}
              </button>
            </aside>
            <main class="file-main">
              <div class="actions">
                <span>{{ activeFile || "未选择文件" }}</span>
                <n-button type="primary" @click="saveFileContent">保存</n-button>
              </div>
              <MonacoEditor
                v-model="fileContent"
                language="markdown"
                height="460px"
              />
            </main>
          </div>
        </n-card>
      </n-tab-pane>
    </n-tabs>
  </section>
</template>

<style scoped>
.memory-view {
  height: 100%;
  min-height: 0;
  overflow: auto;
}

.l1-layout,
.l2-layout,
.l3-layout {
  display: grid;
  gap: 10px;
  grid-template-columns: 220px 1fr;
}

.session-list,
.table-list,
.file-side {
  display: grid;
  gap: 8px;
  max-height: 520px;
  overflow: auto;
}

.session-item,
.table-item,
.file-item {
  background: transparent;
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  color: var(--text);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  text-align: left;
}

.session-item[data-active="true"],
.table-item[data-active="true"],
.file-item[data-active="true"] {
  border-color: color-mix(in srgb, var(--brand) 60%, var(--panel-edge));
}

.session-main,
.table-main,
.file-main {
  min-width: 0;
}

.actions {
  align-items: center;
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.preview {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 10px;
  max-height: 360px;
  overflow: auto;
  padding: 10px;
}

.edit-box {
  margin-top: 10px;
}

@media (max-width: 1023px) {
  .l1-layout,
  .l2-layout,
  .l3-layout {
    grid-template-columns: 1fr;
  }
}
</style>

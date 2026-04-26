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

interface TypedMemoryItem {
  memory_id: string;
  memory_class: string;
  key: string;
  value: string;
  language?: string;
  source: string;
  confidence?: number | null;
  status: string;
  metadata_json?: string;
  updated_at?: string;
  injection_eligible?: boolean;
}

interface TypedMemoryResponse {
  items?: TypedMemoryItem[];
  injectable_classes?: string[];
}

interface TypedMemoryForm {
  memory_id: string | null;
  memory_class: string;
  key: string;
  value: string;
  source: string;
  confidence: number | null;
  status: string;
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
const activeTab = ref<"typed" | "l1" | "sqlite" | "l3">("typed");

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

// Typed semantic memory
const memoryClassOptions = [
  { label: "user_profile", value: "user_profile" },
  { label: "interaction_policy", value: "interaction_policy" },
  { label: "knowledge_note", value: "knowledge_note" },
  { label: "sop", value: "sop" },
  { label: "operational_state", value: "operational_state" },
  { label: "credentials_state", value: "credentials_state" },
] as const;
const memoryStatusOptions = [
  { label: "active", value: "active" },
  { label: "archived", value: "archived" },
] as const;
const injectableClasses = ref<string[]>([]);
const typedMemoryItems = ref<TypedMemoryItem[]>([]);
const typedClassFilter = ref("");
const typedStatusFilter = ref("active");
const typedForm = ref<TypedMemoryForm>({
  memory_id: null,
  memory_class: "interaction_policy",
  key: "",
  value: "",
  source: "webui",
  confidence: 0.8,
  status: "active",
});

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

const filteredTypedMemoryItems = computed(() =>
  typedMemoryItems.value.filter((item) => {
    const classMatches = !typedClassFilter.value || item.memory_class === typedClassFilter.value;
    const statusMatches = !typedStatusFilter.value || item.status === typedStatusFilter.value;
    return classMatches && statusMatches;
  }),
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

const isInjectionEligible = (item: TypedMemoryItem): boolean =>
  item.status === "active" &&
  (item.injection_eligible === true || injectableClasses.value.includes(item.memory_class));

const resetTypedForm = (): void => {
  typedForm.value = {
    memory_id: null,
    memory_class: "interaction_policy",
    key: "",
    value: "",
    source: "webui",
    confidence: 0.8,
    status: "active",
  };
};

const selectTypedMemoryItem = (item: TypedMemoryItem): void => {
  typedForm.value = {
    memory_id: item.memory_id,
    memory_class: item.memory_class,
    key: item.key,
    value: item.value,
    source: item.source || "webui",
    confidence: item.confidence ?? null,
    status: item.status || "active",
  };
};

const loadTypedMemoryItems = async (): Promise<void> => {
  const params = new URLSearchParams();
  if (typedStatusFilter.value) {
    params.set("status", typedStatusFilter.value);
  }
  if (typedClassFilter.value) {
    params.set("memory_class", typedClassFilter.value);
  }
  const query = params.toString();
  const data = await apiGetJson<TypedMemoryResponse>(
    withToken(`memory/items${query ? `?${query}` : ""}`),
  );
  typedMemoryItems.value = Array.isArray(data.items) ? data.items : [];
  injectableClasses.value = Array.isArray(data.injectable_classes)
    ? data.injectable_classes
    : ["interaction_policy", "knowledge_note", "sop", "user_profile"];
};

const saveTypedMemoryItem = async (): Promise<void> => {
  if (!typedForm.value.key.trim() || !typedForm.value.value.trim()) {
    message.warning("key 和 value 不能为空");
    return;
  }
  const response = await fetch(withToken("memory/items"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      memory_id: typedForm.value.memory_id,
      memory_class: typedForm.value.memory_class,
      key: typedForm.value.key,
      value: typedForm.value.value,
      source: typedForm.value.source || "webui",
      confidence: typedForm.value.confidence,
      status: typedForm.value.status,
    }),
  });
  if (!response.ok) {
    message.error("保存 typed memory 失败");
    return;
  }
  message.success("typed memory 已保存");
  await loadTypedMemoryItems();
};

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
  tables.value = Array.isArray(data.tables) ? data.tables : [];
  if (!tables.value.some((item) => item.name === activeTable.value) && tables.value.length > 0) {
    activeTable.value = tables.value[0]?.name ?? "preferences";
  } else if (tables.value.length === 0) {
    activeTable.value = "";
  }
};

const loadTableData = async (): Promise<void> => {
  if (!activeTable.value) {
    tableData.value = null;
    return;
  }
  const data = await apiGetJson<MemoryTableData>(
    withToken(
      `memory/tables/${encodeURIComponent(activeTable.value)}?page=${tablePage.value}&size=${tableSize.value}`,
    ),
  );
  tableData.value = {
    table: data.table ?? activeTable.value,
    page: data.page ?? tablePage.value,
    size: data.size ?? tableSize.value,
    total: data.total ?? 0,
    writable: Boolean(data.writable),
    rows: Array.isArray(data.rows) ? data.rows : [],
  };
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
  await Promise.all([loadTypedMemoryItems(), loadSessions(), loadTables(), loadFiles()]);
  await Promise.all([loadSessionMessages(), loadTableData(), loadFileContent()]);
});

watch(activeSessionId, () => {
  void loadSessionMessages();
});

watch([activeTable, tablePage, tableSize], () => {
  void loadTableData();
});

watch([typedClassFilter, typedStatusFilter], () => {
  void loadTypedMemoryItems();
});

watch(activeFile, () => {
  void loadFileContent();
});
</script>

<template>
  <section class="memory-view">
    <n-tabs v-model:value="activeTab" type="line" animated>
      <n-tab-pane name="typed" tab="语义记忆">
        <n-card title="Typed Memory">
          <div class="typed-layout">
            <main class="typed-main">
              <div class="actions">
                <select v-model="typedClassFilter" class="native-control" aria-label="memory class filter">
                  <option value="">全部类型</option>
                  <option
                    v-for="option in memoryClassOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
                <select v-model="typedStatusFilter" class="native-control" aria-label="memory status filter">
                  <option
                    v-for="option in memoryStatusOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
                <n-button @click="resetTypedForm">新建</n-button>
              </div>

              <div class="typed-list">
                <button
                  v-for="item in filteredTypedMemoryItems"
                  :key="item.memory_id"
                  type="button"
                  class="typed-memory-item"
                  data-testid="typed-memory-item"
                  @click="selectTypedMemoryItem(item)"
                >
                  <span class="memory-class">{{ item.memory_class }}</span>
                  <strong>{{ item.key }}</strong>
                  <span>{{ item.value }}</span>
                  <small>
                    {{ item.source || "unknown" }} ·
                    confidence {{ item.confidence ?? "n/a" }} ·
                    {{ item.updated_at || "no timestamp" }} ·
                    {{ isInjectionEligible(item) ? "可注入" : "不注入" }}
                  </small>
                </button>
              </div>
            </main>

            <aside class="typed-editor">
              <label>
                <span>Class</span>
                <select v-model="typedForm.memory_class" class="native-control">
                  <option
                    v-for="option in memoryClassOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
              </label>
              <label>
                <span>Key</span>
                <input v-model="typedForm.key" class="native-control" type="text" />
              </label>
              <label>
                <span>Value</span>
                <textarea
                  v-model="typedForm.value"
                  class="native-control value-editor"
                  data-testid="typed-memory-value"
                />
              </label>
              <label>
                <span>Source</span>
                <input v-model="typedForm.source" class="native-control" type="text" />
              </label>
              <label>
                <span>Confidence</span>
                <input
                  v-model.number="typedForm.confidence"
                  class="native-control"
                  max="1"
                  min="0"
                  step="0.01"
                  type="number"
                />
              </label>
              <label>
                <span>Status</span>
                <select v-model="typedForm.status" class="native-control">
                  <option
                    v-for="option in memoryStatusOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
              </label>
              <n-button
                type="primary"
                data-testid="typed-memory-save"
                @click="saveTypedMemoryItem"
              >
                保存
              </n-button>
            </aside>
          </div>
        </n-card>
      </n-tab-pane>

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

      <n-tab-pane name="sqlite" tab="SQLite Debug">
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
  width: 100%;
}

.l1-layout,
.l2-layout,
.l3-layout,
.typed-layout {
  display: grid;
  gap: 10px;
  grid-template-columns: 220px 1fr;
}

.typed-layout {
  grid-template-columns: minmax(0, 1fr) minmax(260px, 340px);
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
.file-main,
.typed-main,
.typed-editor {
  min-width: 0;
}

.actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.typed-list {
  display: grid;
  gap: 8px;
  max-height: 560px;
  overflow: auto;
}

.typed-memory-item {
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  color: var(--text);
  cursor: pointer;
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 10px;
  text-align: left;
}

.typed-memory-item strong,
.typed-memory-item span,
.typed-memory-item small {
  overflow-wrap: anywhere;
}

.memory-class {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
}

.typed-memory-item small {
  color: var(--muted);
}

.typed-editor {
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  display: grid;
  gap: 10px;
  padding: 10px;
}

.typed-editor label {
  display: grid;
  gap: 4px;
}

.typed-editor label span {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 700;
}

.native-control {
  background: color-mix(in srgb, var(--surface) 90%, transparent);
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  color: var(--text);
  font: inherit;
  min-width: 0;
  padding: 7px 8px;
}

.value-editor {
  min-height: 128px;
  resize: vertical;
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
  .l3-layout,
  .typed-layout {
    grid-template-columns: 1fr;
  }
}
</style>

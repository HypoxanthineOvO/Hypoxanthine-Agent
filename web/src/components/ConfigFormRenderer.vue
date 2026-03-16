<script setup lang="ts">
import { computed, reactive } from "vue";
import {
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NInput,
  NInputNumber,
  NSwitch,
  NTag,
} from "naive-ui";

defineOptions({
  name: "ConfigFormRenderer",
});

const props = withDefaults(
  defineProps<{
    modelValue: unknown;
    maskedFields?: string[];
    path?: string;
    fileName?: string;
  }>(),
  {
    maskedFields: () => [],
    path: "",
    fileName: "",
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: unknown];
}>();

const arrayDrafts = reactive<Record<string, string>>({});

const TASK_SECTIONS: Record<
  string,
  {
    title: string;
    icon: string;
    description: string;
  }
> = {
  heartbeat: {
    title: "Heartbeat",
    icon: "💓",
    description: "按固定频率唤醒 Agent，由它自主巡检、查邮件、查提醒并决定是否静默。",
  },
  email_store: {
    title: "邮件缓存",
    icon: "🗃️",
    description: "控制本地邮件索引缓存的容量、保留时间和启动预热范围。",
  },
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isScalar = (value: unknown): boolean =>
  value === null ||
  typeof value === "string" ||
  typeof value === "number" ||
  typeof value === "boolean";

const createDefaultValue = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return [];
  }
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, createDefaultValue(item)]),
    );
  }
  if (typeof value === "boolean") {
    return false;
  }
  if (typeof value === "number") {
    return 0;
  }
  return "";
};

const pathFor = (key: string | number): string =>
  props.path
    ? typeof key === "number"
      ? `${props.path}[${key}]`
      : `${props.path}.${key}`
    : String(key);

const testIdFor = (path: string): string =>
  `config-field-${path.replace(/\./g, "-").replace(/\[(\d+)\]/g, "-$1-").replace(/--+/g, "-")}`;

const isMaskedField = (path: string): boolean => props.maskedFields.includes(path);

const shouldUseTextarea = (key: string, value: string): boolean =>
  key === "system_prompt_template" ||
  key === "user_preferences" ||
  value.includes("\n") ||
  value.length > 120;

const rootObject = computed<Record<string, unknown>>(() =>
  isRecord(props.modelValue) ? props.modelValue : {},
);

const isPersonaRoot = computed(() => props.fileName === "persona.yaml" && props.path === "");
const visibleEntries = computed(() => Object.entries(rootObject.value));

const scalarArrayPaths = computed(() =>
  new Set(
    visibleEntries.value
      .filter(([, value]) => Array.isArray(value) && value.every((item) => isScalar(item)))
      .map(([key]) => pathFor(key)),
  ),
);

const updateObjectField = (key: string, value: unknown): void => {
  emit("update:modelValue", {
    ...rootObject.value,
    [key]: value,
  });
};

const removeObjectField = (key: string): void => {
  const next = { ...rootObject.value };
  delete next[key];
  emit("update:modelValue", next);
};

const addArrayItem = (key: string): void => {
  const current = rootObject.value[key];
  const path = pathFor(key);
  if (!Array.isArray(current)) {
    updateObjectField(key, []);
    return;
  }

  const draft = (arrayDrafts[path] ?? "").trim();
  const nextItems = [...current];
  if (current.every((item) => isScalar(item))) {
    if (!draft) {
      return;
    }
    nextItems.push(draft);
    arrayDrafts[path] = "";
    updateObjectField(key, nextItems);
    return;
  }

  const seed = current[0] ?? {};
  nextItems.push(createDefaultValue(seed));
  updateObjectField(key, nextItems);
};

const removeArrayItem = (key: string, index: number): void => {
  const current = rootObject.value[key];
  if (!Array.isArray(current)) {
    return;
  }
  const nextItems = current.filter((_, itemIndex) => itemIndex !== index);
  updateObjectField(key, nextItems);
};

const updateArrayItem = (key: string, index: number, value: unknown): void => {
  const current = rootObject.value[key];
  if (!Array.isArray(current)) {
    return;
  }
  const nextItems = [...current];
  nextItems[index] = value;
  updateObjectField(key, nextItems);
};

const taskEntries = computed(() =>
  Object.entries(rootObject.value).filter(([key]) => key in TASK_SECTIONS),
);

const remainingTaskEntries = computed(() =>
  Object.entries(rootObject.value).filter(([key]) => !(key in TASK_SECTIONS)),
);

const normalizeStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value
        .map((item) => String(item ?? "").trim())
        .filter((item) => item.length > 0)
    : [];

const personaSpeakingStyle = computed<Record<string, unknown>>(() => {
  const raw = rootObject.value.speaking_style;
  return isRecord(raw) ? raw : {};
});

const personaAliases = computed(() => normalizeStringArray(rootObject.value.aliases));
const personaHabits = computed(() => normalizeStringArray(personaSpeakingStyle.value.habits));
const personaPersonalityText = computed(() =>
  normalizeStringArray(rootObject.value.personality).join("\n"),
);

const personaExtraRootEntries = computed(() =>
  Object.entries(rootObject.value).filter(
    ([key]) => !["name", "aliases", "personality", "speaking_style"].includes(key),
  ),
);

const personaExtraSpeakingEntries = computed(() =>
  Object.entries(personaSpeakingStyle.value).filter(([key]) => !["tone", "habits"].includes(key)),
);

const updatePersonaRoot = (patch: Record<string, unknown>): void => {
  emit("update:modelValue", {
    ...rootObject.value,
    ...patch,
  });
};

const updatePersonaSpeakingStyle = (patch: Record<string, unknown>): void => {
  updatePersonaRoot({
    speaking_style: {
      ...personaSpeakingStyle.value,
      ...patch,
    },
  });
};

const updatePersonaListFromText = (field: "personality", nextValue: string): void => {
  updatePersonaRoot({
    [field]: nextValue
      .split(/\n|,/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0),
  });
};

const addPersonaTag = (field: "aliases" | "habits"): void => {
  const draftKey = `persona.${field}`;
  const draft = (arrayDrafts[draftKey] ?? "").trim();
  if (!draft) {
    return;
  }

  if (field === "aliases") {
    updatePersonaRoot({
      aliases: [...personaAliases.value, draft],
    });
  } else {
    updatePersonaSpeakingStyle({
      habits: [...personaHabits.value, draft],
    });
  }
  arrayDrafts[draftKey] = "";
};

const removePersonaTag = (field: "aliases" | "habits", index: number): void => {
  if (field === "aliases") {
    updatePersonaRoot({
      aliases: personaAliases.value.filter((_, itemIndex) => itemIndex !== index),
    });
    return;
  }
  updatePersonaSpeakingStyle({
    habits: personaHabits.value.filter((_, itemIndex) => itemIndex !== index),
  });
};

const updatePersonaExtraRoot = (next: unknown): void => {
  if (!isRecord(next)) {
    return;
  }
  updatePersonaRoot(next);
};

const updatePersonaExtraSpeaking = (next: unknown): void => {
  if (!isRecord(next)) {
    return;
  }
  updatePersonaSpeakingStyle(next);
};
</script>

<template>
  <div v-if="fileName === 'tasks.yaml' && path === ''" class="tasks-layout">
    <n-card
      v-for="[key, value] in taskEntries"
      :key="key"
      :title="`${TASK_SECTIONS[key]?.icon} ${TASK_SECTIONS[key]?.title}`"
      :bordered="false"
      class="task-card"
      :data-testid="`task-card-${key}`"
    >
      <p class="task-description">{{ TASK_SECTIONS[key]?.description }}</p>
      <ConfigFormRenderer
        :model-value="value"
        :masked-fields="maskedFields"
        :path="pathFor(key)"
        :file-name="fileName"
        @update:model-value="(next) => updateObjectField(key, next)"
      />
    </n-card>

    <n-collapse
      v-if="remainingTaskEntries.length > 0"
      class="config-collapse"
      :expanded-names="remainingTaskEntries.map(([key]) => key)"
    >
      <n-collapse-item
        v-for="[key, value] in remainingTaskEntries"
        :key="key"
        :name="key"
        :title="key"
      >
        <ConfigFormRenderer
          :model-value="value"
          :masked-fields="maskedFields"
          :path="pathFor(key)"
          :file-name="fileName"
          @update:model-value="(next) => updateObjectField(key, next)"
        />
      </n-collapse-item>
    </n-collapse>
  </div>

  <div v-else-if="isPersonaRoot" class="persona-layout">
    <n-card :bordered="false" class="persona-card">
      <div class="persona-grid">
        <div class="field-row">
          <label class="field-label">名字</label>
          <n-input
            :value="String(rootObject.name ?? '')"
            data-testid="persona-name-input"
            @update:value="(next) => updatePersonaRoot({ name: next })"
          />
        </div>

        <div class="field-stack">
          <label class="field-label">别名</label>
          <div class="tag-list">
            <n-tag
              v-for="(item, index) in personaAliases"
              :key="`persona-alias-${index}`"
              closable
              @close="removePersonaTag('aliases', index)"
            >
              {{ item }}
            </n-tag>
          </div>
          <div class="array-input-row">
            <n-input
              :value="arrayDrafts['persona.aliases'] ?? ''"
              placeholder="新增别名"
              data-testid="persona-aliases-input"
              @update:value="(next) => (arrayDrafts['persona.aliases'] = next)"
            />
            <n-button tertiary type="primary" @click="addPersonaTag('aliases')">
              添加
            </n-button>
          </div>
        </div>

        <div class="field-stack">
          <label class="field-label">性格特征</label>
          <n-input
            :value="personaPersonalityText"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 10 }"
            placeholder="每行一个特征"
            data-testid="persona-personality-input"
            @update:value="(next) => updatePersonaListFromText('personality', next)"
          />
        </div>

        <div class="field-row">
          <label class="field-label">语气</label>
          <n-input
            :value="String(personaSpeakingStyle.tone ?? '')"
            data-testid="persona-tone-input"
            @update:value="(next) => updatePersonaSpeakingStyle({ tone: next })"
          />
        </div>

        <div class="field-stack">
          <label class="field-label">表达习惯</label>
          <div class="tag-list">
            <n-tag
              v-for="(item, index) in personaHabits"
              :key="`persona-habit-${index}`"
              closable
              @close="removePersonaTag('habits', index)"
            >
              {{ item }}
            </n-tag>
          </div>
          <div class="array-input-row">
            <n-input
              :value="arrayDrafts['persona.habits'] ?? ''"
              placeholder="新增习惯"
              data-testid="persona-habits-input"
              @update:value="(next) => (arrayDrafts['persona.habits'] = next)"
            />
            <n-button tertiary type="primary" @click="addPersonaTag('habits')">
              添加
            </n-button>
          </div>
        </div>
      </div>
    </n-card>

    <n-collapse
      v-if="personaExtraRootEntries.length > 0 || personaExtraSpeakingEntries.length > 0"
      class="config-collapse"
      :expanded-names="['persona-extra', 'persona-speaking-extra']"
    >
      <n-collapse-item
        v-if="personaExtraRootEntries.length > 0"
        name="persona-extra"
        title="其他字段"
      >
        <ConfigFormRenderer
          :model-value="Object.fromEntries(personaExtraRootEntries)"
          :masked-fields="maskedFields"
          path="_persona_extra"
          :file-name="fileName"
          @update:model-value="updatePersonaExtraRoot"
        />
      </n-collapse-item>

      <n-collapse-item
        v-if="personaExtraSpeakingEntries.length > 0"
        name="persona-speaking-extra"
        title="说话风格扩展字段"
      >
        <ConfigFormRenderer
          :model-value="Object.fromEntries(personaExtraSpeakingEntries)"
          :masked-fields="maskedFields"
          path="speaking_style"
          :file-name="fileName"
          @update:model-value="updatePersonaExtraSpeaking"
        />
      </n-collapse-item>
    </n-collapse>
  </div>

  <n-collapse
    v-else-if="isRecord(modelValue)"
    class="config-collapse"
    :expanded-names="visibleEntries.map(([key]) => key)"
  >
    <n-collapse-item
      v-for="[key, value] in visibleEntries"
      :key="pathFor(key)"
      :name="key"
      :title="key"
    >
      <div
        v-if="typeof value === 'boolean'"
        class="field-row"
      >
        <label class="field-label">{{ key }}</label>
        <n-switch
          :value="value"
          :data-testid="testIdFor(pathFor(key))"
          @update:value="(next) => updateObjectField(key, next)"
        />
      </div>

      <div
        v-else-if="typeof value === 'number'"
        class="field-row"
      >
        <label class="field-label">{{ key }}</label>
        <n-input-number
          :value="value"
          :data-testid="testIdFor(pathFor(key))"
          @update:value="(next) => updateObjectField(key, next ?? 0)"
        />
      </div>

      <div
        v-else-if="typeof value === 'string'"
        class="field-row"
      >
        <label class="field-label">{{ key }}</label>
        <n-input
          v-if="!shouldUseTextarea(key, value)"
          :value="value"
          :type="isMaskedField(pathFor(key)) ? 'password' : 'text'"
          :placeholder="isMaskedField(pathFor(key)) ? '••••••••' : ''"
          :data-testid="testIdFor(pathFor(key))"
          @update:value="(next) => updateObjectField(key, next)"
        />
        <n-input
          v-else
          :value="value"
          type="textarea"
          :autosize="{ minRows: 4, maxRows: 12 }"
          :data-testid="testIdFor(pathFor(key))"
          @update:value="(next) => updateObjectField(key, next)"
        />
      </div>

      <div
        v-else-if="Array.isArray(value) && scalarArrayPaths.has(pathFor(key))"
        class="field-stack"
      >
        <label class="field-label">{{ key }}</label>
        <div class="tag-list">
          <n-tag
            v-for="(item, index) in value"
            :key="`${pathFor(key)}-${index}`"
            closable
            @close="removeArrayItem(key, index)"
          >
            {{ item }}
          </n-tag>
        </div>
        <div class="array-input-row">
          <n-input
            :value="arrayDrafts[pathFor(key)] ?? ''"
            :placeholder="`新增 ${key}`"
            :data-testid="testIdFor(pathFor(key))"
            @update:value="(next) => (arrayDrafts[pathFor(key)] = next)"
          />
          <n-button tertiary type="primary" @click="addArrayItem(key)">
            添加
          </n-button>
        </div>
      </div>

      <div
        v-else-if="Array.isArray(value)"
        class="field-stack"
      >
        <div class="object-array-header">
          <label class="field-label">{{ key }}</label>
          <n-button tertiary type="primary" size="small" @click="addArrayItem(key)">
            添加项
          </n-button>
        </div>
        <n-collapse :expanded-names="value.map((_, index) => String(index))">
          <n-collapse-item
            v-for="(item, index) in value"
            :key="`${pathFor(key)}-${index}`"
            :name="String(index)"
            :title="`${key} #${index + 1}`"
          >
            <div class="object-array-item-actions">
              <n-button tertiary type="error" size="small" @click="removeArrayItem(key, index)">
                删除
              </n-button>
            </div>
            <ConfigFormRenderer
              :model-value="item"
              :masked-fields="maskedFields"
              :path="`${pathFor(key)}[${index}]`"
              :file-name="fileName"
              @update:model-value="(next) => updateArrayItem(key, index, next)"
            />
          </n-collapse-item>
        </n-collapse>
      </div>

      <div
        v-else-if="isRecord(value)"
        class="field-stack"
      >
        <ConfigFormRenderer
          :model-value="value"
          :masked-fields="maskedFields"
          :path="pathFor(key)"
          :file-name="fileName"
          @update:model-value="(next) => updateObjectField(key, next)"
        />
      </div>

      <div v-else class="field-row">
        <label class="field-label">{{ key }}</label>
        <n-input
          :value="value == null ? '' : String(value)"
          :data-testid="testIdFor(pathFor(key))"
          @update:value="(next) => updateObjectField(key, next)"
        />
      </div>

      <div v-if="path !== ''" class="field-footer">
        <n-button tertiary type="error" size="tiny" @click="removeObjectField(key)">
          删除字段
        </n-button>
      </div>
    </n-collapse-item>
  </n-collapse>

  <div v-else class="field-row">
    <n-input
      :value="modelValue == null ? '' : String(modelValue)"
      @update:value="(next) => emit('update:modelValue', next)"
    />
  </div>
</template>

<style scoped>
.tasks-layout {
  display: grid;
  gap: 1rem;
}

.persona-layout {
  display: grid;
  gap: 1rem;
}

.persona-card {
  background:
    linear-gradient(145deg, color-mix(in srgb, var(--brand) 9%, transparent), transparent 72%),
    color-mix(in srgb, var(--surface) 95%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 92%, transparent);
  border-radius: 1rem;
}

.persona-grid {
  display: grid;
  gap: 1rem;
}

.task-card {
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--brand) 8%, transparent), transparent 72%),
    color-mix(in srgb, var(--surface) 94%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-edge) 90%, transparent);
  border-radius: 1rem;
}

.task-description {
  color: var(--muted);
  line-height: 1.5;
  margin: 0 0 1rem;
}

.config-collapse {
  border-radius: 0.9rem;
}

.field-row {
  align-items: center;
  display: grid;
  gap: 0.85rem;
  grid-template-columns: minmax(180px, 260px) 1fr;
}

.field-stack {
  display: grid;
  gap: 0.85rem;
}

.field-label {
  color: var(--muted);
  font-size: 0.86rem;
  font-weight: 700;
}

.field-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 0.75rem;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.array-input-row,
.object-array-header,
.object-array-item-actions {
  align-items: center;
  display: flex;
  gap: 0.75rem;
  justify-content: space-between;
}

@media (max-width: 720px) {
  .field-row {
    grid-template-columns: 1fr;
  }
}
</style>

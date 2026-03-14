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
    description: "定期巡检系统状态，并决定是否值得主动打扰用户。",
  },
  email_scan: {
    title: "邮件扫描",
    icon: "📧",
    description: "按固定频率扫描邮箱，把新邮件交给分类和推送链路。",
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
  key === "prompt_template" ||
  key === "system_prompt_template" ||
  key === "user_preferences" ||
  value.includes("\n") ||
  value.length > 120;

const rootObject = computed<Record<string, unknown>>(() =>
  isRecord(props.modelValue) ? props.modelValue : {},
);

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

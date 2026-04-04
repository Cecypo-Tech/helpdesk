<template>
  <div class="flex flex-wrap gap-1.5 p-1.5 rounded border border-outline-gray-2 bg-surface-white focus-within:border-outline-gray-4 min-h-[2rem]">
    <!-- Existing tag chips -->
    <span
      v-for="tag in currentTags"
      :key="tag"
      class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
      :class="tagColor(tag)"
    >
      {{ tag }}
      <button
        type="button"
        class="hover:opacity-70 flex-shrink-0"
        @click="removeTag(tag)"
      >
        <LucideX class="h-2.5 w-2.5" />
      </button>
    </span>

    <!-- Input -->
    <div class="relative flex-1 min-w-[6rem]">
      <input
        ref="inputRef"
        v-model="inputText"
        type="text"
        class="w-full bg-transparent text-sm text-ink-gray-8 outline-none placeholder:text-ink-gray-4 py-0.5"
        :placeholder="currentTags.length === 0 ? __('Add tags...') : ''"
        @input="onInput"
        @keydown="onKeydown"
        @focus="showDropdown = true"
        @blur="onBlur"
      />

      <!-- Autocomplete dropdown -->
      <div
        v-if="showDropdown && filteredSuggestions.length"
        class="absolute left-0 top-full mt-1 z-50 w-48 rounded-md border border-outline-gray-2 bg-surface-white shadow-md overflow-hidden"
      >
        <button
          v-for="suggestion in filteredSuggestions"
          :key="suggestion"
          type="button"
          class="w-full text-left px-3 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-1 flex items-center gap-2"
          @mousedown.prevent="addTag(suggestion)"
        >
          <span class="inline-block h-2 w-2 rounded-full flex-shrink-0" :class="tagDot(suggestion)" />
          {{ suggestion }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import LucideX from "~icons/lucide/x";
import { computed, ref } from "vue";

const props = defineProps<{
  modelValue: string;   // comma-separated, e.g. "bug,frontend"
  allTags: string[];    // autocomplete source
}>();

const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const inputRef = ref<HTMLInputElement | null>(null);
const inputText = ref("");
const showDropdown = ref(false);

const currentTags = computed<string[]>(() => {
  if (!props.modelValue) return [];
  return props.modelValue.split(",").map((t) => t.trim()).filter(Boolean);
});

const filteredSuggestions = computed<string[]>(() => {
  const q = inputText.value.trim().toLowerCase();
  return props.allTags.filter(
    (t) => !currentTags.value.includes(t) && t.toLowerCase().includes(q)
  );
});

function addTag(tag: string) {
  const trimmed = tag.trim();
  if (!trimmed || currentTags.value.includes(trimmed)) {
    inputText.value = "";
    return;
  }
  const next = [...currentTags.value, trimmed].join(",");
  emit("update:modelValue", next);
  inputText.value = "";
}

function removeTag(tag: string) {
  const next = currentTags.value.filter((t) => t !== tag).join(",");
  emit("update:modelValue", next);
}

function onInput() {
  showDropdown.value = true;
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    const val = inputText.value.replace(/,$/, "").trim();
    if (val) addTag(val);
  } else if (e.key === "Backspace" && !inputText.value && currentTags.value.length) {
    removeTag(currentTags.value[currentTags.value.length - 1]);
  } else if (e.key === "Escape") {
    showDropdown.value = false;
  }
}

function onBlur() {
  // Small delay so mousedown on suggestions fires first
  setTimeout(() => {
    if (inputText.value.trim()) addTag(inputText.value);
    showDropdown.value = false;
  }, 150);
}

// ── Visual helpers ────────────────────────────────────────────
const TAG_CLASSES = [
  "bg-blue-100 text-blue-700",
  "bg-green-100 text-green-700",
  "bg-purple-100 text-purple-700",
  "bg-orange-100 text-orange-700",
  "bg-pink-100 text-pink-700",
  "bg-teal-100 text-teal-700",
];

const TAG_DOT_CLASSES = [
  "bg-blue-400",
  "bg-green-400",
  "bg-purple-400",
  "bg-orange-400",
  "bg-pink-400",
  "bg-teal-400",
];

function hashStr(s: string): number {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return h;
}

function tagColor(tag: string): string {
  return TAG_CLASSES[hashStr(tag) % TAG_CLASSES.length];
}

function tagDot(tag: string): string {
  return TAG_DOT_CLASSES[hashStr(tag) % TAG_DOT_CLASSES.length];
}
</script>

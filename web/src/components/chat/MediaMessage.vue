<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  src: string;
}>();

const lowered = computed(() => props.src.toLowerCase());
const isImage = computed(() => /\.(png|jpe?g|gif|svg|webp)$/.test(lowered.value));
const isVideo = computed(() => /\.(mp4|webm)$/.test(lowered.value));
</script>

<template>
  <section class="media-message">
    <img v-if="isImage" :src="src" class="media-image" alt="image attachment" />
    <video v-else-if="isVideo" class="media-video" controls :src="src" />
    <a v-else :href="src" target="_blank" rel="noopener">打开媒体文件</a>
  </section>
</template>

<style scoped>
.media-image,
.media-video {
  border-radius: 0.65rem;
  max-height: 20rem;
  max-width: 100%;
}
</style>

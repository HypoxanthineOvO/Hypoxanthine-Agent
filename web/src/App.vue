<script setup lang="ts">
import ChatView from "./views/ChatView.vue";

const wsUrl = import.meta.env.VITE_WS_URL ?? "ws://127.0.0.1:8000/ws";
const token = import.meta.env.VITE_WS_TOKEN ?? "dev-token-change-me";
const apiBase = import.meta.env.VITE_API_BASE ?? "";
</script>

<template>
  <div class="page">
    <ChatView :ws-url="wsUrl" :token="token" :api-base="apiBase" />
  </div>
</template>

<style scoped>
.page {
  margin: 0 auto;
  max-width: 1100px;
  padding: 2.2rem 1rem 1.2rem;
}
</style>

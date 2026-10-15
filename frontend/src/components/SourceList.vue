<script setup lang="ts">
import { TopRight } from '@element-plus/icons-vue'
import { safeSourceUrl } from '../features/chat/markdown'
defineProps<{ sources: string[] }>()
</script>

<template>
  <div v-if="sources.length" class="sources">
    <span class="sources-label">参考来源 <span>{{ sources.length }}</span></span>
    <ol>
      <li v-for="(source, i) in sources" :key="`${i}-${source}`">
        <a v-if="safeSourceUrl(source)" :href="safeSourceUrl(source)" target="_blank" rel="noopener noreferrer">{{ source }}<el-icon><TopRight /></el-icon></a>
        <span v-else>{{ source }}</span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.sources { margin-top: 20px; border-top: 1px solid var(--line); padding-top: 14px; font-size: 12px; }
.sources-label { color: var(--muted); font-weight: 600; }
.sources-label span { margin-left: 5px; }
ol { padding-left: 20px; margin: 9px 0 0; color: var(--muted); }
li { margin: 7px 0; overflow-wrap: anywhere; }
a { color: var(--sage-dark); text-decoration: none; }
a:hover { text-decoration: underline; }
.el-icon { vertical-align: middle; margin-left: 4px; }
</style>

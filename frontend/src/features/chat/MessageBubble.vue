<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { renderMarkdown } from './markdown'
import type { ChatMessage } from './useChat'
import SourceList from '../../components/SourceList.vue'
const props = defineProps<{ message: ChatMessage; selected: boolean }>()
defineEmits<{ inspect: [] }>()
const html = computed(() => renderMarkdown(props.message.content))
const time = computed(() => new Date(props.message.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
</script>

<template>
  <article class="message" :class="message.role === 'user' ? 'user' : 'assistant'">
    <div class="message-avatar"><span v-if="message.role === 'user'">我</span><img v-else src="/ricegod.svg" width="30" height="30" alt="" /></div>
    <div class="message-body">
      <div class="message-meta"><strong>{{ message.role === 'user' ? '你' : message.role === 'assistant' ? 'RiceGod' : message.role }}</strong><time>{{ time }}</time></div>
      <div v-if="message.role === 'user'" class="user-content">{{ message.content }}</div>
      <!-- Only markdown-it output with html:false is inserted here. -->
      <div v-else class="markdown-body" v-html="html"></div>
      <SourceList v-if="message.response?.sources?.length" :sources="message.response.sources" />
      <button v-if="message.response" class="execution-link" :class="{ selected }" @click="$emit('inspect')">{{ selected ? '正在查看执行信息' : '查看本轮执行信息' }}<el-icon><ArrowRight /></el-icon></button>
    </div>
  </article>
</template>

<style scoped>
.message { display: flex; gap: 13px; margin-bottom: 31px; }
.message-avatar { flex: 0 0 30px; height: 30px; display: grid; place-items: center; font-size: 11px; background: #e9ebe3; color: var(--sage-dark); border-radius: 10px; }
.message-body { min-width: 0; flex: 1; padding-top: 2px; }
.message-meta { display: flex; align-items: center; gap: 10px; margin: 2px 0 13px; }
.message-meta strong { font-size: 12px; font-weight: 600; }
time { color: var(--muted); font-size: 10px; }
.user-content { background: #f1f0e9; border: 1px solid #eae8df; border-radius: 0 14px 14px; padding: 13px 17px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.85; }
.execution-link { display: inline-flex; gap: 5px; align-items: center; margin-top: 15px; padding: 5px 0; color: var(--muted); font-size: 10px; background: none; border: 0; cursor: pointer; }
.execution-link:hover, .execution-link.selected { color: var(--sage-dark); }
</style>

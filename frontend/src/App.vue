<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ChatDotRound, ArrowRight, InfoFilled } from '@element-plus/icons-vue'
import SessionSidebar from './features/sessions/SessionSidebar.vue'
import ChatWorkspace from './features/chat/ChatWorkspace.vue'
import MemoryPanel from './features/memory/MemoryPanel.vue'
import AgentPanel from './features/agent/AgentPanel.vue'
import { useSessions } from './features/sessions/useSessions'
import { useChat } from './features/chat/useChat'
import { useMemory } from './features/memory/useMemory'
import { useHealth } from './composables/useHealth'

const sessions = useSessions()
const memory = useMemory(sessions.userId.value)
const health = useHealth()
const chat = useChat(sessions.userId, sessions.activeId, sessions.markSent, () => { void memory.refresh() })
const rightTab = ref('memory')
onMounted(() => { void health.check(); void memory.refresh() })
</script>

<template>
  <div class="app-shell">
    <SessionSidebar :sessions="sessions.sessions.value" :active-id="sessions.activeId.value" :user-id="sessions.userId.value" :storage-error="sessions.storageError.value" @create="sessions.create" @select="sessions.select" @rename="sessions.rename" />
    <main class="main-column">
      <header class="chat-header">
        <div class="breadcrumb"><el-icon><ChatDotRound /></el-icon><span>饮食灵感</span><el-icon class="breadcrumb-arrow"><ArrowRight /></el-icon><strong :title="sessions.activeSession.value.name">{{ sessions.activeSession.value.name }}</strong></div>
        <el-tooltip :content="health.status.value === 'online' ? 'GET /health 可访问；不代表数据库、检索或模型服务均可用。点击重新检查。' : health.error.value === 'pending' ? '正在检查 GET /health' : health.error.value">
          <button class="health-badge" :class="health.status.value" :disabled="health.status.value === 'checking'" @click="health.check()"><span></span>{{ health.status.value === 'online' ? 'API 可访问' : health.status.value === 'offline' ? '后端未连接 · 重试' : '正在连接后端' }}</button>
        </el-tooltip>
      </header>
      <ChatWorkspace :chat="chat" :session="sessions.activeSession.value" @inspect="rightTab = 'agent'" />
    </main>
    <aside class="context-column" aria-label="用户画像与执行信息">
      <div class="context-heading"><span class="context-symbol">✳</span><strong>更懂你的每一餐</strong><el-tooltip content="记忆与执行信息均来自真实后端；会话目录保存在此浏览器。"><el-icon><InfoFilled /></el-icon></el-tooltip></div>
      <el-tabs v-model="rightTab" class="context-tabs" stretch>
        <el-tab-pane label="我的饮食画像" name="memory"><MemoryPanel :memory="memory" /></el-tab-pane>
        <el-tab-pane label="Agent 执行信息" name="agent"><AgentPanel :response="chat.current.value.selectedResponse" /></el-tab-pane>
      </el-tabs>
      <div class="context-bottom"><span></span> 为真实的偏好，找到合适的答案</div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowUp, ArrowDown, MagicStick, Bowl, Food, Connection, Reading, Collection, ChatDotRound, RefreshRight } from '@element-plus/icons-vue'
import MessageBubble from './MessageBubble.vue'
import type { useChat } from './useChat'
import type { Session } from '../sessions/useSessions'

const props = defineProps<{ chat: ReturnType<typeof useChat>; session: Session }>()
const emit = defineEmits<{ inspect: [] }>()
const current = computed(() => props.chat.current.value)
const scrollArea = ref<HTMLElement>()
const nearBottom = ref(true)
const composing = ref(false)
const composer = ref<{ focus: () => void }>()
let resizeObserver: ResizeObserver | undefined
const content = ref<HTMLElement>()
const suggestions = [
  { icon: Bowl, title: '今天吃点什么？', description: '从口味和食材出发，找一餐灵感', prompt: '我喜欢清淡的菜，只有鸡蛋和番茄，推荐一道二十分钟内能做好的晚餐。', tone: 'orange' },
  { icon: Food, title: '把一道菜做好', description: '食材、步骤与烹饪小技巧', prompt: '番茄炒蛋怎么做？请告诉我食材用量和详细步骤。', tone: 'sage' },
  { icon: Connection, title: '发现相似的美味', description: '喜欢这道菜，也许还会喜欢这些', prompt: '我喜欢宫保鸡丁，能推荐几道口味或食材相似的菜吗？', tone: 'sage' },
  { icon: Reading, title: '餐桌上的小知识', description: '探索菜肴背后的饮食文化', prompt: '川菜有什么历史和文化特点？', tone: 'orange' },
  { icon: Collection, title: '记住我的口味', description: '让以后的推荐，更合你的心意', prompt: '请记住我的长期饮食偏好：我喜欢清淡的菜，不吃香菜。', tone: 'orange' },
  { icon: ChatDotRound, title: '接着聊，慢慢挑', description: '多轮追问，一起调整你的选择', prompt: '先推荐三道简单的家常菜，接下来我想继续了解其中一道的做法。', tone: 'sage' },
]
function choose(prompt: string) { current.value.draft = prompt; void nextTick(() => composer.value?.focus()) }
function send() { if (!composing.value) void props.chat.send(current.value.draft) }
function keydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing.value && event.keyCode !== 229) { event.preventDefault(); send() }
}
function scrollToBottom(force = false) {
  if (!current.value.messages.length && !current.value.pending && scrollArea.value) {
    scrollArea.value.scrollTop = 0
    return
  }
  if ((force || nearBottom.value) && scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight
}
function onScroll() {
  const el = scrollArea.value
  if (el) nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 90
}
watch(() => [props.session.id, current.value.messages.length, current.value.pending, current.value.loading, current.value.error], async ([id], old) => {
  const changed = !old || id !== old[0]
  if (changed || current.value.pending) nearBottom.value = true
  await nextTick()
  scrollToBottom(changed)
}, { immediate: true })
watch(content, el => {
  resizeObserver?.disconnect()
  if (el) { resizeObserver = new ResizeObserver(() => scrollToBottom()); resizeObserver.observe(el) }
})
onBeforeUnmount(() => resizeObserver?.disconnect())
const blocked = computed(() => current.value.pending || current.value.loading || !!current.value.failedRequest)
const canSend = computed(() => !blocked.value && !!current.value.draft.trim() && current.value.draft.length <= 20_000)
</script>

<template>
  <section class="chat-workspace" aria-label="智能对话">
    <div ref="scrollArea" class="chat-scroll" @scroll="onScroll">
      <div ref="content" class="chat-content" :class="{ 'is-welcome': !current.messages.length && !current.loading }">
        <div v-if="current.historyError" class="notice warning" role="alert">
          <div><strong>历史记录暂未同步</strong><p>{{ current.historyError }}</p><small>请先恢复历史服务，再继续已有会话。新会话可尝试聊天，消息持久化取决于后端服务。</small></div>
          <el-button text :icon="RefreshRight" :loading="current.loading" :disabled="current.pending" @click="chat.loadHistory()">重试</el-button>
        </div>
        <p v-if="current.historyLimit" class="history-limit">已加载最近 1000 条消息。后端当前未提供分页查询。</p>
        <div v-if="current.loading" class="history-loading" role="status"><span class="waiting-dot"></span>正在读取真实会话历史…</div>
        <div v-if="!current.messages.length && !current.loading" class="welcome">
          <div class="welcome-emblem"><img src="/ricegod.svg" alt="" width="52" height="52" /><span>✦</span></div>
          <div class="eyebrow">A LITTLE INSPIRATION FOR EVERY MEAL</div>
          <h1>好好吃饭，<br />从<span>这里</span>开始。</h1>
          <p class="welcome-description">今天想吃什么？聊聊你的口味、食材或一点小好奇。<br />让 RiceGod 陪你，把每一餐变得更简单。</p>
          <div class="suggestion-heading"><span>从一个小问题开始</span><span class="fine-line"></span><el-icon><MagicStick /></el-icon></div>
          <div class="suggestions">
            <button v-for="item in suggestions" :key="item.title" class="suggestion" :disabled="blocked" @click="choose(item.prompt)">
              <span class="suggestion-icon" :class="item.tone"><el-icon><component :is="item.icon" /></el-icon></span>
              <span class="suggestion-copy"><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span>
              <span class="suggestion-arrow">↗</span>
            </button>
          </div>
        </div>
        <div v-if="current.messages.length" class="messages">
          <div class="conversation-start"><span></span> 一段关于吃的对话 <span></span></div>
          <MessageBubble v-for="message in current.messages" :key="message.id" :message="message" :selected="!!message.response && current.selectedResponse === message.response" @inspect="current.selectedResponse = message.response; emit('inspect')" />
        </div>
        <div v-if="current.pending" class="thinking" role="status" aria-live="polite"><img src="/ricegod.svg" width="30" height="30" alt="" /><div><strong>RiceGod 正在处理</strong><p><span class="waiting-dot"></span>等待检索与回答，请稍候…</p></div></div>
        <div v-if="current.error" class="notice error" role="alert"><div><strong>这一轮暂未完成</strong><p>{{ current.error }}</p><small>重试会先核对历史服务，再使用原 request_id。已完成的持久化轮次会返回缓存；失败轮次可能重新执行。</small><el-button size="small" :icon="RefreshRight" :loading="current.pending" @click="chat.retry()">核对并重试</el-button></div></div>
      </div>
    </div>
    <button v-if="!nearBottom && current.messages.length" class="to-bottom icon-button" aria-label="滚动到最新消息" @click="scrollToBottom(true)"><el-icon><ArrowDown /></el-icon></button>
    <div class="composer-region">
      <div class="composer" :class="{ 'is-disabled': blocked }">
        <el-input ref="composer" v-model="current.draft" type="textarea" resize="none" :autosize="{ minRows: 2, maxRows: 5 }" :maxlength="20000" :disabled="blocked || (!!current.historyError && session.hasMessages)" aria-label="向 RiceGod 发送消息" placeholder="告诉我你想吃什么，或聊聊你的饮食偏好…" @keydown="keydown" @compositionstart="composing = true" @compositionend="composing = false" />
        <div class="composer-toolbar"><span><el-icon><MagicStick /></el-icon> 好好吃饭的灵感，都可以聊</span><div><small v-if="current.draft.length > 18000">{{ current.draft.length }} / 20000</small><span class="keyboard-hint">Enter 发送 <i>·</i> Shift + Enter 换行</span><el-button class="send-button" type="primary" :icon="ArrowUp" :disabled="!canSend || (!!current.historyError && session.hasMessages)" :loading="current.pending" circle aria-label="发送消息" @click="send" /></div></div>
      </div>
      <p class="composer-footnote">AI 回答仅供参考，请结合实际饮食需求判断。<span>由 RiceGod Agent 驱动</span></p>
    </div>
  </section>
</template>

<style scoped>
.chat-workspace { flex: 1; min-height: 0; display: flex; flex-direction: column; position: relative; }
.chat-scroll { min-height: 0; flex: 1; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable both-edges; }
.chat-content { max-width: 820px; padding: 18px 36px 26px; margin: 0 auto; }
.is-welcome { padding-top: clamp(25px, 5.5vh, 65px); }
.welcome { max-width: 690px; margin: 0 auto; }
.welcome-emblem { display: inline-flex; align-items: center; position: relative; margin-bottom: 22px; }
.welcome-emblem img { box-shadow: 0 6px 20px #d7824220; border-radius: 17px; }
.welcome-emblem > span { position: absolute; color: var(--sage); font-size: 24px; right: -22px; top: -10px; }
.eyebrow { color: #a09c8b; font-size: 9px; letter-spacing: 2px; font-weight: 500; margin-bottom: 16px; }
h1 { font-size: clamp(34px, 2.5vw, 48px); letter-spacing: 2px; line-height: 1.45; font-weight: 550; margin: 0 0 16px; }
h1 span { color: var(--orange); }
.welcome-description { color: var(--muted); font-size: 12px; line-height: 2; margin: 0 0 30px; }
.suggestion-heading { display: flex; gap: 13px; align-items: center; font-size: 10px; color: var(--muted); margin-bottom: 14px; }
.fine-line { height: 1px; background: var(--line); flex: 1; }
.suggestions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.suggestion { display: flex; gap: 11px; align-items: center; padding: 17px 14px; border: 1px solid var(--line); border-radius: 12px; background: #fffefb; cursor: pointer; text-align: left; transition: border-color .15s, box-shadow .15s; min-width: 0; }
.suggestion:hover { border-color: #d5b99e; box-shadow: 0 4px 12px #6b4b1610; }
.suggestion-icon { width: 33px; height: 33px; border-radius: 10px; display: grid; place-items: center; flex-shrink: 0; font-size: 17px; }
.orange { color: var(--orange); background: var(--orange-soft); }
.sage { color: var(--sage-dark); background: var(--sage-soft); }
.suggestion-copy { min-width: 0; }
.suggestion strong { font-size: 12px; font-weight: 550; color: var(--ink); display: block; }
.suggestion small { font-size: 10px; color: var(--muted); margin-top: 6px; display: block; line-height: 1.5; }
.suggestion-arrow { font-size: 14px; color: #aca596; margin-left: auto; align-self: flex-start; }
.composer-region { padding: 12px 36px 14px; max-width: 820px; width: 100%; align-self: center; }
.composer { background: #fffefa; border: 1px solid #dedcd2; border-radius: 16px; padding: 12px 14px 10px; box-shadow: 0 5px 20px #62543a06; }
.composer:focus-within { border-color: #c8a485; box-shadow: 0 0 0 3px #e2b48412; }
.composer :deep(.el-textarea__inner) { box-shadow: none; background: transparent; font-size: 13px; padding: 2px; color: var(--ink); line-height: 1.8; }
.composer :deep(.el-textarea.is-disabled .el-textarea__inner) { background: transparent; }
.composer-toolbar { display: flex; justify-content: space-between; align-items: center; margin-top: 7px; }
.composer-toolbar > span { display: flex; gap: 5px; align-items: center; color: var(--sage-dark); font-size: 9px; }
.composer-toolbar > div { display: flex; align-items: center; gap: 10px; }
.composer-toolbar small { font-size: 9px; color: var(--muted); }
.keyboard-hint { font-size: 9px; color: #a39f94; }
.keyboard-hint i { font-style: normal; margin: 0 4px; }
.send-button { width: 31px; height: 31px; font-size: 17px; }
.composer-footnote { margin: 11px 0 0; text-align: center; color: #a49e91; font-size: 9px; line-height: 1.7; }
.composer-footnote span { margin-left: 12px; }
.conversation-start { display: flex; align-items: center; gap: 14px; justify-content: center; color: #aaa394; font-size: 10px; margin: 14px 0 31px; }
.conversation-start > span { height: 1px; flex: 1; background: var(--line); }
.thinking { display: flex; gap: 13px; padding: 0 0 15px; }
.thinking strong { font-size: 12px; font-weight: 500; }
.thinking p { font-size: 11px; color: var(--muted); display: flex; gap: 7px; align-items: center; }
.history-loading { display: flex; align-items: center; gap: 8px; color: var(--muted); justify-content: center; padding: 40px 0; font-size: 12px; }
.history-limit { font-size: 11px; text-align: center; color: var(--muted); }
.notice { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; padding: 13px 15px; border-radius: 10px; margin-bottom: 18px; border: 1px solid #ecd9be; background: #fcf5e9; }
.notice strong { font-size: 11px; font-weight: 600; }
.notice p { font-size: 11px; line-height: 1.8; margin: 5px 0; overflow-wrap: anywhere; }
.notice small { display: block; font-size: 10px; color: var(--muted); line-height: 1.8; }
.notice .el-button { margin-top: 8px; }
.error { border-color: #edd6ce; background: #fbf1ec; color: #8d4d39; }
.to-bottom { position: absolute; bottom: 175px; left: calc(50% - 17px); width: 34px; height: 34px; border: 1px solid var(--line); background: #fffdfa; border-radius: 50%; box-shadow: 0 3px 9px #41352110; }
@media (max-height: 800px) { .is-welcome { padding-top: 23px; } .welcome-emblem { margin-bottom: 15px; } .welcome-emblem img { width: 44px; height: 44px; } .eyebrow { margin-bottom: 10px; } h1 { font-size: 34px; margin-bottom: 11px; } .welcome-description { margin-bottom: 20px; } .suggestion { padding: 12px; } .composer-region { padding-bottom: 10px; } }
@media (min-width: 1700px) and (min-height: 900px) { .welcome { padding-top: 20px; } .welcome-description { margin-bottom: 40px; } .suggestion { padding-top: 21px; padding-bottom: 21px; } }
</style>

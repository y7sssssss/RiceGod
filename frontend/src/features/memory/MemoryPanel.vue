<script setup lang="ts">
import { computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import { RefreshRight, Delete, Collection, Warning, InfoFilled } from '@element-plus/icons-vue'
import type { useMemory } from './useMemory'
import type { MemoryRecord } from '../../types/api'
const props = defineProps<{ memory: ReturnType<typeof useMemory> }>()
const labels: Record<string, string> = {
  dietary_preference: '饮食偏好', allergy: '过敏信息', disliked_ingredient: '忌口食材', liked_cuisine: '偏好菜系',
  cooking_constraint: '烹饪限制', equipment: '厨房设备', household: '家庭信息', goal: '饮食目标', recipe_feedback: '菜谱反馈', todo: '其他记录',
}
const groups = computed(() => {
  const result = new Map<string, MemoryRecord[]>()
  for (const item of props.memory.memories.value) {
    const items = result.get(item.type) || []
    items.push(item)
    result.set(item.type, items)
  }
  return [...result].map(([type, items]) => ({ type, label: labels[type] || type, items }))
})
async function confirmDelete(item: MemoryRecord) {
  try {
    await ElMessageBox.confirm(`确认删除“${item.content}”？删除后，后续对话将不再使用这条长期记忆。`, '忘记这条记忆', {
      confirmButtonText: '确认删除', cancelButtonText: '保留记忆', type: 'warning', closeOnClickModal: false,
    })
  } catch { return }
  await props.memory.remove(item.id)
}
</script>

<template>
  <div class="memory-panel">
    <div class="panel-intro"><h2>慢慢了解你的口味<span v-if="!memory.error.value && !memory.loading.value" class="count">{{ memory.memories.value.length }}</span></h2><p>记住你的偏好，让下一餐更合心意。</p></div>
    <div class="panel-section-label"><span>长期记忆</span><el-tooltip content="从后端刷新记忆"><el-button text circle size="small" :icon="RefreshRight" :loading="memory.loading.value" :disabled="memory.deleting.value !== null" aria-label="刷新长期记忆" @click="memory.refresh()" /></el-tooltip></div>
    <div v-if="memory.error.value" class="service-state error-state" role="alert"><el-icon><Warning /></el-icon><strong>记忆服务暂不可用</strong><p>{{ memory.error.value }}</p><el-button size="small" :disabled="memory.deleting.value !== null" @click="memory.refresh()">重新获取</el-button></div>
    <div v-else-if="memory.loading.value" class="service-state" role="status"><span class="waiting-dot"></span><p>正在读取长期记忆…</p></div>
    <div v-else-if="!memory.memories.value.length" class="service-state empty-memory"><div class="empty-illustration"><el-icon><Collection /></el-icon><span>✦</span></div><strong>从一次聊天开始了解</strong><p>还没有长期记忆。<br />你可以告诉我你的长期口味或忌口。</p><small>是否成功保存，以后端记忆回执为准。</small></div>
    <section v-for="group in !memory.error.value && !memory.loading.value ? groups : []" :key="group.type" class="memory-group">
      <h3><span class="group-dot" :class="{ allergy: group.type === 'allergy' }"></span>{{ group.label }}<small>{{ group.items.length }}</small></h3>
      <article v-for="item in group.items" :key="item.id" class="memory-item">
        <p>{{ item.content }}</p>
        <div class="memory-meta"><span>#{{ item.id }} · {{ new Date(item.updated_at).toLocaleDateString('zh-CN') }}</span><el-tooltip content="删除这条记忆"><el-button text circle size="small" :icon="Delete" :loading="memory.deleting.value === item.id" :disabled="memory.deleting.value !== null" :aria-label="`删除记忆 ${item.id}`" @click="confirmDelete(item)" /></el-tooltip></div>
        <details v-if="item.evidence" class="evidence"><summary>查看记录依据</summary><p>{{ item.evidence }}</p><small v-if="item.source_message_id">来源消息 #{{ item.source_message_id }}</small></details>
      </article>
    </section>
    <div class="memory-tip"><el-icon><InfoFilled /></el-icon><p>偏好变了？直接在对话里告诉我。<span>例如：“把清淡偏好改成喜欢微辣。”<br />也可以明确说：“忘记清淡偏好。”</span></p></div>
    <div class="memory-footer"><span class="leaf-mark">⌁</span><p>关于你的了解，来自真实对话。<br />你始终可以查看和删除。</p></div>
  </div>
</template>

<style scoped>
.memory-panel { padding: 8px 23px 24px; }
.panel-intro { padding: 12px 0 20px; }
h2 { font-size: 14px; font-weight: 550; display: flex; align-items: center; gap: 8px; margin: 0; }
.count { font-size: 10px; background: var(--sage-soft); color: var(--sage-dark); border-radius: 5px; padding: 2px 5px; }
.panel-intro > p { font-size: 10px; line-height: 1.8; color: var(--muted); margin: 9px 0 0; }
.panel-section-label { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--line); padding-top: 13px; color: var(--muted); font-size: 10px; letter-spacing: .7px; }
.service-state { text-align: center; padding: 26px 8px; color: var(--muted); }
.service-state > .el-icon { color: #ac8967; background: #f2e8da; border-radius: 12px; width: 40px; height: 40px; font-size: 20px; margin-bottom: 15px; }
.service-state strong { display: block; font-size: 12px; font-weight: 500; color: var(--ink); }
.service-state p { font-size: 11px; line-height: 1.9; overflow-wrap: anywhere; margin-top: 9px; }
.service-state small { font-size: 9px; }
.error-state { background: #fbf6ed; border: 1px dashed #e8d8c2; border-radius: 13px; margin: 14px 0 20px; padding: 22px 12px; }
.error-state p { font-size: 10px; }
.empty-illustration { position: relative; width: 68px; height: 68px; margin: 0 auto 18px; background: var(--sage-soft); border-radius: 23px; color: var(--sage); display: grid; place-items: center; font-size: 30px; transform: rotate(-5deg); }
.empty-illustration span { position: absolute; top: -10px; right: -8px; font-size: 25px; color: var(--orange); }
.memory-group { margin-top: 18px; }
h3 { font-size: 11px; font-weight: 500; display: flex; gap: 6px; align-items: center; color: #726d61; margin-bottom: 10px; }
h3 small { margin-left: auto; font-size: 9px; color: var(--muted); }
.group-dot { width: 5px; height: 5px; background: var(--sage); border-radius: 50%; }
.group-dot.allergy { background: var(--orange); }
.memory-item { padding: 11px 12px 5px; border-radius: 10px; background: #f4f5ef; border: 1px solid #e7eadf; margin-bottom: 8px; }
.memory-item > p { font-size: 11px; line-height: 1.8; color: #59614f; margin: 0; overflow-wrap: anywhere; }
.memory-meta { display: flex; align-items: center; justify-content: space-between; margin-top: 5px; font-size: 9px; color: var(--muted); }
.memory-meta .el-button { font-size: 12px; color: var(--muted); }
.memory-meta .el-button:hover { color: var(--danger); }
.evidence { border-top: 1px solid #e1e5d9; padding: 7px 0; margin-top: 3px; font-size: 9px; color: var(--muted); }
.evidence summary { cursor: pointer; }
.evidence p { line-height: 1.8; overflow-wrap: anywhere; }
.memory-tip { display: flex; gap: 8px; background: #f5f3ed; border-radius: 10px; padding: 13px 11px; margin-top: 25px; color: var(--muted); }
.memory-tip .el-icon { margin-top: 2px; color: #a6a28f; flex-shrink: 0; font-size: 12px; }
.memory-tip p { margin: 0; font-size: 10px; line-height: 1.8; }
.memory-tip span { display: block; font-size: 9px; margin-top: 7px; }
.memory-footer { border-top: 1px solid var(--line); padding-top: 22px; margin-top: 27px; display: flex; gap: 10px; align-items: center; }
.leaf-mark { color: var(--sage); font-size: 32px; }
.memory-footer p { font-size: 9px; line-height: 1.9; color: #a29d8f; margin: 0; }
</style>

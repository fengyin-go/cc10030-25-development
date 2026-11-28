<template>
  <section class="recheck-panel" :data-module="moduleKey" data-testid="recheck-panel">
    <header class="recheck-head">
      <div>
        <h3>复检办理清单</h3>
        <p class="recheck-desc">
          按保养计划定位未来 {{ days }} 日内到期的复检项目，按复检期限升序排列；
          首条为最紧急的{{ firstIsCooling ? '冷却' : '' }}复检。
        </p>
      </div>
      <div class="recheck-actions">
        <label class="recheck-days">
          <span>筛选天数</span>
          <input v-model.number="days" type="number" min="0" max="365" data-testid="recheck-days" />
        </label>
        <button class="btn primary" type="button" data-testid="recheck-filter" @click="load">
          筛选未来 {{ days }} 日
        </button>
      </div>
    </header>

    <p class="recheck-window" data-testid="recheck-window">
      窗口：{{ list?.window_start || '—' }} 至 {{ list?.deadline || '—' }}
      · 共 {{ list?.total ?? 0 }} 项
      <span v-if="list?.snapshot"> · 清单快照 {{ list.snapshot }}</span>
    </p>

    <table class="data-table recheck-table">
      <thead>
        <tr>
          <th>排序</th>
          <th>船舶编号</th>
          <th>检验类型</th>
          <th>保养项目</th>
          <th>复检期限</th>
          <th>办理状态</th>
          <th>办理号 / 受理编号</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in sortedItems"
          :key="`${item.检验编号}-${item.保养编号}`"
          :data-testid="`recheck-item-${item.检验编号}`"
          :data-status="item.status"
          :class="['recheck-row', `tone-${statusTone(item.status)}`, { 'is-first': item.排序 === 1 }]"
        >
          <td>
            <span class="rank-badge">{{ item.排序 }}</span>
            <span v-if="item.排序 === 1" class="first-tag">首条</span>
          </td>
          <td>{{ item.船舶编号 }}</td>
          <td>
            {{ item.检验类型 }}
            <span v-if="isCooling(item)" class="cooling-tag">冷却</span>
          </td>
          <td>{{ item.保养项目 }}</td>
          <td>
            {{ item.复检期限 || '—' }}
            <small class="remaining">{{ remainingText(item) }}</small>
          </td>
          <td>
            <span class="status-chip" :data-testid="`recheck-status-${item.检验编号}`">{{ item.status }}</span>
            <small class="status-hint">{{ statusHint(item.status) }}</small>
          </td>
          <td>
            <div>{{ item.办理号 || '—' }}</div>
            <div>{{ item.受理编号 || '—' }}</div>
          </td>
          <td class="row-actions">
            <button
              v-if="item.status !== '已受理'"
              class="link"
              type="button"
              :data-testid="`recheck-arrange-${item.检验编号}`"
              @click="arrange(item)"
            >
              {{ item.status === '等待周期更正' ? '重试安排' : '安排复检' }}
            </button>
            <button
              v-if="item.status === '已发出安排请求' && item.handling_id"
              class="link"
              type="button"
              :data-testid="`recheck-accept-${item.检验编号}`"
              @click="accept(item)"
            >
              受理复检
            </button>
            <button
              v-if="item.status === '已受理' && item.handling_id"
              class="link"
              type="button"
              :data-testid="`recheck-verify-${item.检验编号}`"
              @click="verify(item)"
            >
              用这笔复检核验保养
            </button>
          </td>
        </tr>
        <tr v-if="!sortedItems.length">
          <td colspan="8" class="empty-state">未来 {{ days }} 日内没有待复检项目</td>
        </tr>
      </tbody>
    </table>

    <div v-if="movedOut.length" class="recheck-outside" data-testid="recheck-moved-out">
      <h4>等待周期更正（已移出窗口，未受理项不能沿旧清单核验）</h4>
      <ul>
        <li v-for="item in movedOut" :key="`out-${item.检验编号}`" :data-testid="`recheck-moved-${item.检验编号}`">
          {{ item.船舶编号 }} · {{ item.检验类型 }} · 当前期限 {{ item.复检期限 || '未知' }}
          · 办理号 {{ item.办理号 || '—' }} · {{ item.status }}
          <button
            class="link"
            type="button"
            :data-testid="`recheck-moved-retry-${item.检验编号}`"
            @click="arrange(item)"
          >
            重试安排
          </button>
        </li>
      </ul>
    </div>

    <div v-if="acceptedItems.length" class="recheck-accepted" data-testid="recheck-accepted">
      <h4>已受理安排（仍可查原检验与原保养周期）</h4>
      <ul>
        <li v-for="item in acceptedItems" :key="`acc-${item.检验编号}`" :data-testid="`recheck-accepted-${item.检验编号}`">
          {{ item.船舶编号 }} · {{ item.检验类型 }} · 受理编号 {{ item.受理编号 }}
          · 原检验 {{ item.原检验日期 }}（{{ item.原检验结论 }}）
          · 原周期 {{ item.原保养周期 }} · 原下次保养 {{ item.原下次保养 }}
        </li>
      </ul>
    </div>

    <div v-if="receipt" class="recheck-receipt" data-testid="recheck-receipt">
      <h4>安排回执</h4>
      <dl>
        <dt>回执号</dt><dd data-testid="receipt-no">{{ receipt.回执号 || '—' }}</dd>
        <dt>办理号</dt><dd data-testid="receipt-handling">{{ receipt.办理号 || '—' }}</dd>
        <dt>安排状态</dt><dd data-testid="receipt-status">{{ receipt.安排状态 || '—' }}</dd>
        <dt>复检期限</dt><dd>{{ receipt.复检期限 || '—' }}</dd>
        <dt>说明</dt><dd>{{ receipt.提示 || '—' }}</dd>
      </dl>
    </div>

    <p v-if="feedback" class="recheck-feedback" :class="{ 'error-text': !feedbackOk }" data-testid="recheck-feedback">
      {{ feedback }}
    </p>

    <div v-if="moduleKey === 'maintenance'" class="recheck-maintenance-tools">
      <h4>保养工单核验入口</h4>
      <p class="recheck-desc">
        核验必须关联复检办理清单，从有受理编号的冷却项目取实际证据；
        周期更正后先尝试核验，再重试安排。
      </p>
      <form class="recheck-form" @submit.prevent="verifyFromWorkOrder">
        <label>
          <span>保养工单编号</span>
          <input v-model.number="workOrderId" type="number" min="1" data-testid="workorder-id" />
        </label>
        <label>
          <span>复检办理号（可留空，自动恢复）</span>
          <input v-model="workOrderHandling" data-testid="workorder-handling" placeholder="FJ-…" />
        </label>
        <button class="btn" type="button" data-testid="recheck-recover" @click="recover">
          从原工单恢复办理号
        </button>
        <button class="btn primary" type="submit" data-testid="workorder-verify">
          用这笔复检核验保养
        </button>
      </form>

      <form class="recheck-form" @submit.prevent="correctCycle">
        <label>
          <span>保养工单编号</span>
          <input v-model.number="workOrderId" type="number" min="1" data-testid="cycle-workorder-id" />
        </label>
        <label>
          <span>更正下次保养日期</span>
          <input v-model="cycleNextDate" type="date" data-testid="cycle-next-date" />
        </label>
        <label>
          <span>更正保养周期</span>
          <input v-model="cycleValue" data-testid="cycle-value" placeholder="如 90天 / 6个月" />
        </label>
        <button class="btn" type="submit" data-testid="cycle-submit">更正保养周期</button>
        <button class="btn" type="button" data-testid="maintenance-retry-arrange" @click="retryFromMaintenance">
          从保养页重试安排
        </button>
        <button class="btn" type="button" data-testid="inspection-retry-arrange" @click="retryFromInspection">
          从检验页重试安排
        </button>
      </form>

      <dl v-if="recovered" class="recheck-recovered" data-testid="recheck-recovered">
        <dt>恢复办理号</dt><dd data-testid="recovered-handling">{{ recovered.办理号 || '—' }}</dd>
        <dt>受理编号</dt><dd data-testid="recovered-acceptance">{{ recovered.受理编号 || '—' }}</dd>
        <dt>原保养周期</dt><dd data-testid="recovered-cycle">{{ recovered.原保养周期 || '—' }}</dd>
        <dt>核验状态</dt><dd>{{ recovered.verification?.message || recovered.verification?.status || '—' }}</dd>
      </dl>

      <dl v-if="evidence" class="recheck-evidence" data-testid="recheck-evidence">
        <dt>来源检验编号</dt><dd data-testid="evidence-inspection">{{ evidence.来源检验编号 ?? '—' }}</dd>
        <dt>检验类型</dt><dd>{{ evidence.检验类型 || '—' }}</dd>
        <dt>检验日期</dt><dd>{{ evidence.检验日期 || '—' }}</dd>
        <dt>检验结论</dt><dd>{{ evidence.检验结论 || '—' }}</dd>
        <dt>缺陷项目</dt><dd>{{ evidence.缺陷项目 || '—' }}</dd>
        <dt>受理编号</dt><dd data-testid="evidence-acceptance">{{ evidence.受理编号 || '—' }}</dd>
      </dl>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  acceptRecheck,
  arrangeRecheck,
  correctMaintenanceCycle,
  loadRecheckList,
  recoverRecheck,
  runAction,
} from '@/api/backend-service'
import {
  RECHECK_STATUS,
  isCooling,
  remainingText,
  sortRecheckItems,
  statusHint,
  statusTone,
} from '@/domain/recheck'
import type { RecheckItem, RecheckList, RecheckReceipt } from '@/domain/recheck'

const props = withDefaults(
  defineProps<{ moduleKey: string; defaultDays?: number }>(),
  { defaultDays: 7 },
)
const emit = defineEmits<{ (event: 'changed'): void }>()

const days = ref(props.defaultDays)
const list = ref<RecheckList | null>(null)
const receipt = ref<RecheckReceipt | null>(null)
const feedback = ref('')
const feedbackOk = ref(true)
const workOrderId = ref<number | null>(1)
const workOrderHandling = ref('')
const cycleNextDate = ref('')
const cycleValue = ref('')
const recovered = ref<Record<string, any> | null>(null)
const evidence = ref<Record<string, any> | null>(null)
const activeHandling = ref<{ id: number; 检验编号: number; 办理号: string } | null>(null)

const sortedItems = computed(() => sortRecheckItems(list.value?.items ?? []))
const movedOut = computed(() => list.value?.moved_out ?? [])
const acceptedItems = computed(() => list.value?.accepted ?? [])
const firstIsCooling = computed(() => {
  const first = sortedItems.value[0]
  return first ? isCooling(first) : false
})

function note(message: string, ok: boolean) {
  feedback.value = message
  feedbackOk.value = ok
}

async function load() {
  note('', true)
  try {
    list.value = await loadRecheckList(days.value)
  } catch (error) {
    note(error instanceof Error ? error.message : '复检办理清单读取失败', false)
  }
}

async function arrange(item: RecheckItem) {
  note('', true)
  try {
    const result = await arrangeRecheck({
      inspectionId: item.检验编号,
      maintenanceId: item.保养编号,
      handlingId: item.handling_id,
      days: days.value,
      snapshot: list.value?.snapshot,
      expectedDue: item.复检期限,
    })
    receipt.value = result.receipt ?? null
    if (result.handling?.办理号) {
      workOrderHandling.value = result.handling.办理号
      activeHandling.value = {
        id: result.handling.id,
       检验编号: Number(result.handling.检验编号),
        办理号: result.handling.办理号,
      }
    }
    note(result.message, result.ok)
    await load()
    emit('changed')
  } catch (error) {
    note(error instanceof Error ? error.message : '安排复检失败', false)
  }
}

async function accept(item: RecheckItem) {
  if (!item.handling_id) return
  note('', true)
  try {
    const result = await acceptRecheck(item.handling_id, days.value)
    receipt.value = result.receipt ?? receipt.value
    note(result.message, result.ok)
    await load()
    emit('changed')
  } catch (error) {
    note(error instanceof Error ? error.message : '受理复检失败', false)
  }
}

async function verify(item: RecheckItem) {
  if (!item.maintenance_id) return
  evidence.value = null
  try {
    const result = await runAction('maintenance', item.maintenance_id, '核验保养', {
      handlingId: item.handling_id ?? activeHandling.value?.id ?? null,
      days: days.value,
    })
    evidence.value = (result.evidence as Record<string, any> | null) ?? null
    note(result.message, Boolean(result.ok))
    await load()
    emit('changed')
  } catch (error) {
    note(error instanceof Error ? error.message : '核验保养失败', false)
  }
}

async function verifyFromWorkOrder() {
  if (!workOrderId.value) {
    note('请先填写保养工单编号', false)
    return
  }
  evidence.value = null
  try {
    const handlingId = await ensureHandling()
    const result = await runAction('maintenance', Number(workOrderId.value), '核验保养', {
      handlingId: handlingId ?? activeHandling.value?.id ?? null,
      days: days.value,
    })
    evidence.value = (result.evidence as Record<string, any> | null) ?? null
    note(result.message, Boolean(result.ok))
    await load()
    emit('changed')
  } catch (error) {
    note(error instanceof Error ? error.message : '核验保养失败', false)
  }
}

function resolveHandlingId(): number | null {
  const text = workOrderHandling.value.trim()
  if (!text) return null
  const asNumber = Number(text)
  if (!Number.isNaN(asNumber) && asNumber > 0) return asNumber
  const match = (list.value?.items ?? [])
    .concat(list.value?.moved_out ?? [])
    .find((item) => item.办理号 === text)
  return match?.handling_id ?? null
}

async function recover() {
  if (!workOrderId.value) {
    note('请先填写保养工单编号', false)
    return
  }
  try {
    const result = await recoverRecheck(Number(workOrderId.value))
    recovered.value = result as Record<string, any>
    if (result.办理号) workOrderHandling.value = result.办理号
    note(result.message, result.ok)
  } catch (error) {
    note(error instanceof Error ? error.message : '恢复办理号失败', false)
  }
}

async function correctCycle() {
  if (!workOrderId.value) {
    note('请先填写保养工单编号', false)
    return
  }
  try {
    const result = await correctMaintenanceCycle(Number(workOrderId.value), {
      nextDate: cycleNextDate.value || null,
      cycle: cycleValue.value || null,
      days: days.value,
    })
    const affectedText = (result.affected ?? [])
      .map((row) => `${row.办理号 || '未登记'}：${row.status}`)
      .join('；')
    note(
      `${result.message}${affectedText ? `（${affectedText}）` : ''}；` +
        `更正后核验状态：${result.verification?.message || result.verification?.status || '—'}`,
      Boolean(result.ok),
    )
    await load()
    emit('changed')
  } catch (error) {
    note(error instanceof Error ? error.message : '更正保养周期失败', false)
  }
}

/** 重试安排前若还没有办理号，先从原保养工单恢复，避免重复开一条。 */
async function ensureHandling(): Promise<number | null> {
  const resolved = resolveHandlingId()
  if (resolved || !workOrderId.value) return resolved
  try {
    const restored = await recoverRecheck(Number(workOrderId.value))
    recovered.value = restored as Record<string, any>
    if (restored.办理号) {
      workOrderHandling.value = restored.办理号
      const entry = (restored as Record<string, any>).entry as Record<string, any> | null
      if (entry?.id) {
        return Number(entry.id)
      }
    }
  } catch {
    // 恢复失败时继续按检验/保养维度重试，后端仍会做唯一键去重。
  }
  return null
}

async function retryFromMaintenance() {
  if (!workOrderId.value) {
    note('请先填写保养工单编号', false)
    return
  }
  const handlingId = await ensureHandling()
  const result = await runAction('maintenance', Number(workOrderId.value), '重试安排', {
    handlingId,
    days: days.value,
  })
  note(result.message, Boolean(result.ok))
  await load()
  emit('changed')
}

/** 检验页重试：优先当前办理对应的检验，其次窗口外待更正项，最后窗口内首条。 */
function inspectionRetryTarget(): RecheckItem | null {
  const all = [...sortedItems.value, ...movedOut.value, ...acceptedItems.value]
  if (activeHandling.value) {
    const matched = all.find((item) => item.检验编号 === activeHandling.value?.检验编号)
    if (matched) return matched
  }
  return movedOut.value[0] ?? sortedItems.value[0] ?? acceptedItems.value[0] ?? null
}

async function retryFromInspection() {
  const target = inspectionRetryTarget()
  if (!target) {
    note('当前清单没有可重试的检验项目', false)
    return
  }
  const handlingId =
    target.handling_id ?? activeHandling.value?.id ?? (await ensureHandling())
  const result = await runAction('inspection', target.检验编号, '重试安排', {
    handlingId,
    days: days.value,
  })
  note(result.message, Boolean(result.ok))
  await load()
  emit('changed')
}

onMounted(load)
</script>

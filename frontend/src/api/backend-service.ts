import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'
import type {
  RecheckActionResult,
  RecheckHandling,
  RecheckList,
} from '@/domain/recheck'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export type ActionOptions = {
  handlingId?: number | null
  days?: number | null
  expectedDue?: string | null
  snapshot?: string | null
  nextDate?: string | null
  cycle?: string | null
  values?: Record<string, string>
}

export async function runAction(
  key: string,
  id: number,
  action: string,
  options: ActionOptions = {},
): Promise<ActionResult & Partial<RecheckActionResult>> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({
      action,
      handling_id: options.handlingId ?? undefined,
      days: options.days ?? undefined,
      expected_due: options.expectedDue ?? undefined,
      snapshot: options.snapshot ?? undefined,
      next_date: options.nextDate ?? undefined,
      cycle: options.cycle ?? undefined,
      values: options.values ?? {},
    }),
  })
  return await response.json()
}

/** 未来 N 日复检办理清单：后端已按复检期限排序，first 即首条。 */
export async function loadRecheckList(days = 7): Promise<RecheckList> {
  const response = await request(`/api/recheck?days=${days}`)
  if (!response.ok) throw new Error('复检办理清单读取失败')
  return await response.json()
}

export async function arrangeRecheck(payload: {
  inspectionId?: number | null
  maintenanceId?: number | null
  handlingId?: number | null
  days?: number
  snapshot?: string | null
  expectedDue?: string | null
}): Promise<RecheckActionResult> {
  const response = await request('/api/recheck/arrange', {
    method: 'POST',
    body: JSON.stringify({
      inspection_id: payload.inspectionId ?? undefined,
      maintenance_id: payload.maintenanceId ?? undefined,
      handling_id: payload.handlingId ?? undefined,
      days: payload.days,
      snapshot: payload.snapshot ?? undefined,
      expected_due: payload.expectedDue ?? undefined,
    }),
  })
  return await response.json()
}

export async function acceptRecheck(
  handlingId: number,
  days = 7,
): Promise<RecheckActionResult> {
  const response = await request(`/api/recheck/${handlingId}/accept?days=${days}`, {
    method: 'POST',
  })
  return await response.json()
}

export async function loadHandlings(status?: string): Promise<{
  items: RecheckHandling[]
  total: number
}> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const response = await request(`/api/recheck/handlings${query}`)
  if (!response.ok) throw new Error('复检办理记录读取失败')
  return await response.json()
}

export async function recoverRecheck(maintenanceId: number): Promise<{
  ok: boolean
  message: string
  办理号?: string
  受理编号?: string
  原检验?: Record<string, string | number>
  原保养周期?: string
  verification?: { status: string; message: string; can_verify: boolean }
}> {
  const response = await request(`/api/recheck/maintenance/${maintenanceId}`)
  if (!response.ok) throw new Error('复检办理号恢复失败')
  return await response.json()
}

export async function correctMaintenanceCycle(
  maintenanceId: number,
  payload: { nextDate?: string | null; cycle?: string | null; days?: number },
): Promise<RecheckActionResult & {
  affected?: { 办理号: string; status: string }[]
  verification?: { status: string; can_verify: boolean; message: string; handling_id: number | null }
}> {
  const response = await request(`/api/recheck/maintenance/${maintenanceId}/cycle`, {
    method: 'POST',
    body: JSON.stringify({
      next_date: payload.nextDate ?? undefined,
      cycle: payload.cycle ?? undefined,
      days: payload.days,
    }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

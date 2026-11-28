/** 复检办理的纯前端领域层：状态口径、排序和展示格式与后端保持一致。 */

export type RecheckStatus =
  | '已发出安排请求'
  | '已受理'
  | '等待周期更正'
  | '未安排'

export type RecheckItem = {
  id: number | null
  handling_id: number | null
  inspection_id: number | null
  maintenance_id: number | null
  检验编号: number
  保养编号: number
  船舶编号: string
  检验类型: string
  保养项目: string
  复检期限: string
  剩余天数: number | null
  排序: number
  status: string
  办理状态: string
  办理号: string
  受理编号: string
  原检验日期: string
  原有效期至: string
  原检验结论: string
  原缺陷项目: string
  原保养周期: string
  原下次保养: string
  安排时间: string
  受理时间: string
  窗口外: boolean
  是否冷却: boolean
}

export type RecheckList = {
  items: RecheckItem[]
  moved_out: RecheckItem[]
  accepted: RecheckItem[]
  first: RecheckItem | null
  total: number
  outside_total: number
  days: number
  window_days: number
  window_start: string
  deadline: string
  snapshot: string
  window_version: number
  generated_at: string
}

export type RecheckReceipt = {
  回执号?: string
  办理号?: string
  检验编号?: number
  保养编号?: number
  船舶编号?: string
  检验类型?: string
  复检期限?: string
  原保养周期?: string
  安排状态?: string
  提示?: string
}

export type RecheckHandling = {
  id: number
  status: string
  办理号: string
  受理编号: string
  检验编号: number
  保养编号: number
  复检期限: string
  原检验日期: string
  原有效期至: string
  原保养周期: string
  原下次保养: string
  证据?: Record<string, unknown>
  回执?: RecheckReceipt
  [field: string]: unknown
}

export type RecheckActionResult = {
  ok: boolean
  message: string
  status?: string
  duplicate?: boolean
  receipt?: RecheckReceipt | null
  handling?: RecheckHandling | null
  evidence?: Record<string, unknown> | null
  entry?: Record<string, unknown> | null
  [field: string]: unknown
}

export const RECHECK_STATUS = {
  requested: '已发出安排请求',
  accepted: '已受理',
  waiting: '等待周期更正',
  unscheduled: '未安排',
} as const

/** 三种结果对应的展示色调，页面靠它区分“只发了请求 / 已受理 / 等周期更正”。 */
export function statusTone(status: string): 'pending' | 'accepted' | 'waiting' | 'idle' {
  if (status === RECHECK_STATUS.accepted) return 'accepted'
  if (status === RECHECK_STATUS.waiting) return 'waiting'
  if (status === RECHECK_STATUS.requested) return 'pending'
  return 'idle'
}

export function statusHint(status: string): string {
  if (status === RECHECK_STATUS.accepted) return '已受理，可凭受理编号核验保养'
  if (status === RECHECK_STATUS.waiting) return '保养日期已超出窗口，等待周期更正'
  if (status === RECHECK_STATUS.requested) return '只发出了安排请求，尚未受理'
  return '尚未安排复检'
}

/** 复检期限升序、同日按检验编号，和后端排序规则一致。 */
export function sortRecheckItems(items: RecheckItem[]): RecheckItem[] {
  return [...items].sort(
    (left, right) =>
      (left.复检期限 || '9999-12-31').localeCompare(right.复检期限 || '9999-12-31') ||
      Number(left.检验编号) - Number(right.检验编号),
  )
}

export function isCooling(item: RecheckItem): boolean {
  return item.是否冷却 || item.检验类型.includes('冷却') || item.保养项目.includes('冷却')
}

export function remainingText(item: RecheckItem): string {
  if (item.剩余天数 === null) return '未知'
  if (item.剩余天数 < 0) return `已逾期 ${Math.abs(item.剩余天数)} 天`
  if (item.剩余天数 === 0) return '今天到期'
  return `剩 ${item.剩余天数} 天`
}

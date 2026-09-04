/**
 * SCNet Token Plan usage monitor — desktop plugin.
 *
 * Live Credits metering for scnet.cn Token Plan subscriptions. SCNet
 * exposes no usage API (console is session-cookie auth), so this reads
 * Hermes' own local usage records through the plugin's Python backend
 * (plugin_api.py → state.db session_model_usage) and converts tokens to
 * Credits with the official multiplier formula.
 *
 * UI: a right-side pane, a statusbar chip, and ⌘K commands.
 */

import {
  atom, cn, haptic, host, icons, queryClient,
  Badge, Button, Codicon, ErrorState, Skeleton, StatusDot, Tip,
  usePluginI18n, useQuery,
  PALETTE_AREA,
} from '@hermes/plugin-sdk'
import { useReducer } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'scnet-usage'

// ---------------------------------------------------------------------------
// Locale bundles
// ---------------------------------------------------------------------------

const STRINGS = {
  en: {
    paneTitle: 'SCNet Credits',
    refresh: 'Refresh',
    openSettings: 'Settings',
    today: 'Today',
    thisCycle: 'This cycle',
    creditsUsed: 'Used',
    creditsLeft: 'left',
    ofPlan: plan => `/ ${plan.toLocaleString()} cr`,
    percentUsed: p => `${p}% used`,
    noPlan: 'Set your plan quota in settings to see remaining Credits',
    byModel: 'By model',
    trend: 'Daily credits',
    multiplier: m => `×${m}`,
    multiplierUnknown: '×?',
    tokens: (i, o, c) => `in ${fmt(i)} · out ${fmt(o)} · cache ${fmt(c)}`,
    calls: n => `${n.toLocaleString()} calls`,
    estimated: 'Estimated from Hermes-local records (Hermes calls only)',
    error: 'Could not load usage',
    settingsTitle: 'SCNet usage settings',
    planLabel: 'Plan tier',
    monthlyCredits: 'Monthly Credits',
    cycleDay: 'Cycle start day (purchase day)',
    save: 'Save',
    cancel: 'Cancel',
    unset: 'Not set',
    lifetime: (i, o, c, n) => `All time: in ${fmt(i)} / out ${fmt(o)} / cache ${fmt(c)} · ${n.toLocaleString()} calls`,
    chipTip: 'SCNet Token Plan — click to open details',
    cmdOpen: 'SCNet: open usage pane',
    cmdRefresh: 'SCNet: refresh usage data',
  },
  zh: {
    paneTitle: 'SCNet 用量',
    refresh: '刷新',
    openSettings: '设置',
    today: '今日',
    thisCycle: '本周期',
    creditsUsed: '已用',
    creditsLeft: '剩余',
    ofPlan: plan => `/ ${plan.toLocaleString()} Credits`,
    percentUsed: p => `已用 ${p}%`,
    noPlan: '在设置中填写套餐额度后可查看剩余 Credits',
    byModel: '按模型',
    trend: '每日消耗',
    multiplier: m => `×${m}`,
    multiplierUnknown: '×?',
    tokens: (i, o, c) => `输入 ${fmt(i)} · 输出 ${fmt(o)} · 缓存 ${fmt(c)}`,
    calls: n => `${n.toLocaleString()} 次调用`,
    estimated: '基于 Hermes 本地记录估算（仅含 Hermes 发起的调用）',
    error: '无法加载用量数据',
    settingsTitle: 'SCNet 用量设置',
    planLabel: '套餐档位',
    monthlyCredits: '月度 Credits 额度',
    cycleDay: '周期起始日（购买日）',
    save: '保存',
    cancel: '取消',
    unset: '未设置',
    lifetime: (i, o, c, n) => `累计：输入 ${fmt(i)} / 输出 ${fmt(o)} / 缓存 ${fmt(c)} · ${n.toLocaleString()} 次调用`,
    chipTip: 'SCNet Token Plan — 点击查看详情',
    cmdOpen: 'SCNet：打开用量面板',
    cmdRefresh: 'SCNet：刷新用量数据',
  },
}

function fmt(n) {
  if (n == null) return '0'
  if (n >= 1e8) return (n / 1e8).toFixed(1) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return n.toLocaleString()
}

function fmtCredits(n) {
  if (n == null) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

// ---------------------------------------------------------------------------
// Backend access (ctx.rest captured at register time)
// ---------------------------------------------------------------------------

let restFn = null
function api(path, opts) {
  if (!restFn) return Promise.reject(new Error('plugin backend not ready'))
  return restFn(path, opts)
}

const USAGE_KEY = ['scnet-usage', 'usage']

function useUsage() {
  return useQuery({
    queryKey: USAGE_KEY,
    queryFn: () => api('/usage?days=30'),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
}

// component-scoped state helper
function useLocal(initial) {
  const [state, setState] = useReducer((_, v) => v, initial)
  return [state, setState]
}

// ---------------------------------------------------------------------------
// Statusbar chip
// ---------------------------------------------------------------------------

// Open (or re-front) the usage view as a workspace tile. Falls back to a
// toast on older desktops without openWorkspace.
function openUsageView() {
  if (typeof host.openWorkspace === 'function') {
    host.openWorkspace('scnet-usage', {
      title: 'SCNet 用量',
      minWidth: '24rem',
      render: () => jsx(UsagePane, {}),
    })
  } else {
    host.notify({ kind: 'info', message: 'SCNet 用量面板需要较新版本的桌面应用' })
  }
}

function Chip() {
  const t = usePluginI18n(ID)
  const { data } = useUsage()
  const credits = data?.totals?.credits
  const remaining = data?.plan?.remaining
  const monthly = data?.plan?.monthly_credits

  let label = 'SCNet …'
  if (credits != null) {
    label = remaining != null ? `${fmtCredits(remaining)} cr 剩余` : `${fmtCredits(credits)} cr`
  }

  const danger = remaining != null && monthly && remaining < monthly * 0.1
  const warn = remaining != null && monthly && !danger && remaining < monthly * 0.25

  return jsx(Tip, {
    label: t('chipTip'),
    children: jsx('button', {
      type: 'button',
      className: cn(
        'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] font-medium tabular-nums transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      onClick: () => {
        haptic('tap')
        openUsageView()
      },
      children: jsxs('span', { className: 'inline-flex items-center gap-1.5', children: [
        jsx(StatusDot, { tone: danger ? 'bad' : warn ? 'warn' : 'good' }),
        label,
      ] }),
    }),
  })
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

const TIERS = { 基础版: 60000, 标准版: 240000, 高级版: 600000, 旗舰版: 1800000 }

function SettingsPanel({ config, onClose }) {
  const t = usePluginI18n(ID)
  const [tier, setTier] = useLocal(config?.plan_label || '')
  const [credits, setCredits] = useLocal(config?.monthly_credits != null ? String(config.monthly_credits) : '')
  const [cycleDay, setCycleDay] = useLocal(String(config?.cycle_start_day || 1))
  const [saving, setSaving] = useLocal(false)

  const save = async () => {
    setSaving(true)
    try {
      const monthly = Number(credits)
      await api('/config', {
        method: 'POST',
        body: {
          plan_label: tier || null,
          monthly_credits: Number.isFinite(monthly) && monthly > 0 ? monthly : null,
          cycle_start_day: Number(cycleDay) || 1,
        },
      })
      await queryClient.invalidateQueries({ queryKey: USAGE_KEY })
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return jsxs('div', {
    className: 'flex flex-col gap-3 rounded-lg border border-(--ui-stroke-secondary) p-3',
    children: [
      jsx('div', { className: 'text-sm font-medium', children: t('settingsTitle') }),
      jsxs('label', { className: 'flex flex-col gap-1 text-xs', children: [
        jsx('span', { className: 'text-(--ui-text-secondary)', children: t('planLabel') }),
        jsxs('select', {
          className: 'rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1.5 text-sm',
          value: tier,
          onChange: e => {
            setTier(e.target.value)
            if (TIERS[e.target.value]) setCredits(String(TIERS[e.target.value]))
          },
          children: [
            jsx('option', { value: '', children: t('unset') }),
            ...Object.entries(TIERS).map(([k, v]) =>
              jsx('option', { value: k, children: `${k}（${v.toLocaleString()}）` }, k)),
          ],
        }),
      ] }),
      jsxs('label', { className: 'flex flex-col gap-1 text-xs', children: [
        jsx('span', { className: 'text-(--ui-text-secondary)', children: t('monthlyCredits') }),
        jsx('input', {
          className: 'rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1.5 text-sm tabular-nums',
          value: credits,
          inputMode: 'decimal',
          placeholder: t('unset'),
          onChange: e => setCredits(e.target.value.replace(/[^\d.]/g, '')),
        }),
      ] }),
      jsxs('label', { className: 'flex flex-col gap-1 text-xs', children: [
        jsx('span', { className: 'text-(--ui-text-secondary)', children: t('cycleDay') }),
        jsx('input', {
          className: 'rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1.5 text-sm tabular-nums',
          value: cycleDay,
          inputMode: 'numeric',
          onChange: e => setCycleDay(e.target.value.replace(/[^\d]/g, '').slice(0, 2)),
        }),
      ] }),
      jsxs('div', { className: 'flex justify-end gap-2', children: [
        jsx(Button, { variant: 'ghost', size: 'xs', onClick: onClose, children: t('cancel') }),
        jsx(Button, { size: 'xs', onClick: save, disabled: saving, children: saving ? '…' : t('save') }),
      ] }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Pane
// ---------------------------------------------------------------------------

function ProgressBar({ percent, danger, warn }) {
  return jsx('div', {
    className: 'h-1.5 w-full overflow-hidden rounded-full bg-(--ui-stroke-secondary)',
    children: jsx('div', {
      className: cn('h-full rounded-full transition-all duration-500',
        danger ? 'bg-red-500' : warn ? 'bg-amber-500' : 'bg-(--ui-accent)'),
      style: { width: `${Math.min(100, Math.max(0, percent))}%` },
    }),
  })
}

function UsagePane() {
  const t = usePluginI18n(ID)
  const { data, error, isLoading, refetch, isFetching } = useUsage()
  const [settingsOpen, setSettingsOpen] = useLocal(false)
  const [config, setConfig] = useLocal(null)

  if (isLoading) {
    return jsxs('div', { className: 'flex h-full flex-col gap-3 p-4', children: [
      jsx(Skeleton, { className: 'h-16 w-full' }),
      jsx(Skeleton, { className: 'h-24 w-full' }),
      jsx(Skeleton, { className: 'h-32 w-full' }),
    ] })
  }
  if (error) {
    return jsx('div', { className: 'flex h-full items-center justify-center p-4', children:
      jsx(ErrorState, { title: t('error'), message: String(error), onRetry: () => refetch() })
    })
  }
  if (!data) return null

  const { totals, plan, models, daily, cycle, lifetime, today_credits } = data
  const percent = plan?.percent_used
  const danger = percent != null && percent > 90
  const warn = percent != null && !danger && percent > 75
  const maxDaily = Math.max(1, ...daily.map(d => d.credits))

  const openSettings = async () => {
    try { setConfig(await api('/config')) } catch { setConfig({}) }
    setSettingsOpen(true)
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 overflow-y-auto p-4 text-sm',
    children: [
      // header
      jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
        jsxs('div', { className: 'flex min-w-0 items-center gap-2', children: [
          jsx(Codicon, { name: 'pulse', className: 'text-(--ui-accent)' }),
          jsx('span', { className: 'font-medium', children: t('paneTitle') }),
          jsx(Badge, { variant: 'muted', size: 'xs', children: `${cycle.start} → ${cycle.end}` }),
        ] }),
        jsxs('div', { className: 'flex shrink-0 items-center gap-1', children: [
          jsx(Tip, { label: t('openSettings'), children:
            jsx(Button, { variant: 'ghost', size: 'icon-xs', onClick: openSettings, children:
              jsx(Codicon, { name: 'settings-gear' }) }) }),
          jsx(Tip, { label: t('refresh'), children:
            jsx(Button, { variant: 'ghost', size: 'icon-xs', onClick: () => refetch(), children:
              jsx(Codicon, { name: 'refresh', spinning: isFetching }) }) }),
        ] }),
      ] }),

      settingsOpen ? jsx(SettingsPanel, { config, onClose: () => setSettingsOpen(false) }) : null,

      // headline numbers
      jsxs('div', {
        className: 'flex flex-col gap-3 rounded-lg border border-(--ui-stroke-secondary) p-3',
        children: [
          jsxs('div', { className: 'flex items-end justify-between gap-2', children: [
            jsxs('div', { className: 'flex flex-col gap-0.5', children: [
              jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: t('thisCycle') }),
              jsxs('span', { className: 'text-2xl font-semibold tabular-nums leading-tight', children: [
                fmtCredits(totals.credits),
                jsx('span', { className: 'ml-1 text-xs font-normal text-(--ui-text-tertiary)', children: 'Credits' }),
              ] }),
            ] }),
            jsxs('div', { className: 'flex flex-col items-end gap-0.5', children: [
              jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: t('today') }),
              jsx('span', { className: 'text-lg font-semibold tabular-nums', children: fmtCredits(today_credits) }),
            ] }),
          ] }),
          plan?.monthly_credits
            ? jsxs('div', { className: 'flex flex-col gap-1.5', children: [
                jsx(ProgressBar, { percent: percent || 0, danger, warn }),
                jsxs('div', { className: 'flex justify-between text-[0.6875rem] text-(--ui-text-quaternary) tabular-nums', children: [
                  jsx('span', { children: t('percentUsed', percent) }),
                  jsxs('span', { children: [
                    t('creditsLeft'), ' ', fmtCredits(plan.remaining), ' ', t('ofPlan', plan.monthly_credits),
                  ] }),
                ] }),
              ] })
            : jsx('div', { className: 'text-xs text-(--ui-text-quaternary) italic', children: t('noPlan') }),
          jsxs('div', { className: 'flex flex-wrap gap-x-4 gap-y-1 pt-1 text-[0.6875rem] text-(--ui-text-quaternary)', children: [
            jsx('span', { children: t('tokens', totals.input_tokens, totals.output_tokens, totals.cached_tokens) }),
            jsx('span', { children: t('calls', totals.api_calls) }),
          ] }),
        ],
      }),

      // daily trend
      daily.length > 0 ? jsxs('div', {
        className: 'flex flex-col gap-2 rounded-lg border border-(--ui-stroke-secondary) p-3',
        children: [
          jsx('span', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: t('trend') }),
          jsx('div', { className: 'flex h-16 items-end gap-[3px]', children:
            daily.map(d => jsx(Tip, {
              label: `${d.day} · ${d.credits.toLocaleString()} cr`,
              children: jsx('div', {
                className: 'min-w-[3px] flex-1 cursor-default rounded-sm bg-(--ui-accent) opacity-70 transition-opacity hover:opacity-100',
                style: { height: `${Math.max(4, (d.credits / maxDaily) * 100)}%` },
              }),
            }, d.day))
          }),
        ],
      }) : null,

      // by model
      models.length > 0 ? jsxs('div', {
        className: 'flex flex-col gap-2 rounded-lg border border-(--ui-stroke-secondary) p-3',
        children: [
          jsx('span', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: t('byModel') }),
          ...models.map(m => jsxs('div', {
            className: 'flex flex-col gap-1 border-b border-(--ui-stroke-secondary)/50 pb-2 last:border-0 last:pb-0',
            children: [
              jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
                jsxs('span', { className: 'flex min-w-0 items-center gap-1.5', children: [
                  jsx(StatusDot, { tone: 'good' }),
                  jsx('span', { className: 'truncate font-medium', children: m.model }),
                ] }),
                jsxs('span', { className: 'flex shrink-0 items-center gap-1.5', children: [
                  m.multiplier != null
                    ? jsx(Badge, { variant: 'muted', size: 'xs', children: t('multiplier', m.multiplier) })
                    : jsx(Badge, { variant: 'warn', size: 'xs', children: t('multiplierUnknown') }),
                  jsx('span', { className: 'font-semibold tabular-nums', children: fmtCredits(m.credits) }),
                ] }),
              ] }),
              jsxs('div', { className: 'flex justify-between gap-2 pl-3 text-[0.625rem] text-(--ui-text-quaternary) tabular-nums', children: [
                jsx('span', { className: 'truncate', children: t('tokens', m.input, m.output, m.cached) }),
                jsx('span', { className: 'shrink-0', children: t('calls', m.calls) }),
              ] }),
            ],
          }, m.model)),
        ],
      }) : null,

      // lifetime + footnote
      jsxs('div', { className: 'flex flex-col gap-1 text-[0.625rem] text-(--ui-text-quaternary)', children: [
        jsx('span', { children: t('lifetime', lifetime.input_tokens, lifetime.output_tokens, lifetime.cached_tokens, lifetime.api_calls) }),
        jsx('span', { className: 'italic', children: `※ ${t('estimated')} · 倍率日期 ${data.multipliers_date}` }),
      ] }),
    ],
  })
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

export default {
  id: ID,
  name: 'SCNet Usage Monitor',
  description: 'Live scnet.cn Token Plan Credits monitoring from local usage records',
  register(ctx) {
    ctx.i18n.register(STRINGS)

    restFn = (path, opts) => ctx.rest(path, opts)

    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'SCNet 用量',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(UsagePane, {}),
    })

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 128,
      render: () => jsx(Chip, {}),
    })

    ctx.register({
      id: 'cmd-open',
      area: PALETTE_AREA,
      data: { label: 'SCNet：打开用量面板', icon: icons.Activity, run: openUsageView },
    })
    ctx.register({
      id: 'cmd-refresh',
      area: PALETTE_AREA,
      data: {
        label: 'SCNet：刷新用量数据',
        icon: icons.RefreshCw,
        run: () => { void queryClient.invalidateQueries({ queryKey: USAGE_KEY }) },
      },
    })
  },
}

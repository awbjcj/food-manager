import type { AccountData } from './types'

const demoMode = import.meta.env.VITE_DEMO_MODE === 'true'

export const demoAccount: AccountData = {
  user: {
    telegramId: 1,
    name: 'Alex Chen',
    role: 'owner',
    language: 'en',
    timeZone: 'America/New_York',
    digestHour: 8,
    provider: 'gemini',
  },
  household: { name: 'The Chen kitchen', members: 2, seatCap: 2 },
  plan: {
    tier: 'free',
    status: 'active',
    periodEnd: '2026-09-01T08:00:00',
    renews: false,
    canManage: true,
  },
  quota: { receiptsUsed: 3, receiptsLimit: 5, actionsUsed: 18, actionsLimit: 30 },
  plans: [
    { code: 'free', title: 'Free', stars: 0, kind: 'tier', receipts: 5, actions: 30, seats: 2 },
    { code: 'family_monthly', title: 'Family plan', stars: 500, kind: 'subscription', receipts: 100, actions: 300, seats: 10 },
    { code: 'topup_receipts_50', title: '+50 receipts', stars: 250, kind: 'topup', receipts: 50, actions: 0, seats: null },
    { code: 'topup_actions_150', title: '+150 AI actions', stars: 250, kind: 'topup', receipts: 0, actions: 150, seats: null },
  ],
  availableProviders: ['anthropic', 'deepseek', 'gemini', 'openai'],
  botUsername: 'food_manager_bot',
  billingEnabled: true,
  hostedFeaturesEnabled: true,
}

function headers(): HeadersInit {
  const initData = window.Telegram?.WebApp.initData ?? ''
  return {
    Authorization: `tma ${initData}`,
    'Content-Type': 'application/json',
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { ...headers(), ...init?.headers } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.error || 'Something went wrong')
  return body as T
}

export async function loadAccount(): Promise<AccountData> {
  return demoMode ? demoAccount : request<AccountData>('/api/account')
}

export async function saveAccount(payload: object): Promise<void> {
  if (demoMode) return
  await request('/api/account', { method: 'PATCH', body: JSON.stringify(payload) })
}

export async function createCheckout(sku: string): Promise<string> {
  if (demoMode) return 'https://t.me/$demo-invoice'
  const result = await request<{ invoiceUrl: string }>('/api/checkout', {
    method: 'POST',
    body: JSON.stringify({ sku }),
  })
  return result.invoiceUrl
}

export async function cancelRenewal(): Promise<void> {
  if (demoMode) return
  await request('/api/subscription/cancel', { method: 'POST', body: '{}' })
}

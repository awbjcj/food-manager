export type Tab = 'home' | 'plans' | 'account'

export interface PlanOption {
  code: string
  title: string
  stars: number
  description?: string
  kind: 'tier' | 'subscription' | 'topup'
  receipts: number
  actions: number
  seats: number | null
}

export interface AccountData {
  user: {
    telegramId: number
    name: string
    role: string
    language: string
    timeZone: string
    digestHour: number
    provider: string
  }
  household: { name: string; members: number; seatCap: number }
  plan: {
    tier: string
    status: string
    periodEnd: string
    renews: boolean
    canManage: boolean
  }
  quota: {
    receiptsUsed: number
    receiptsLimit: number
    actionsUsed: number
    actionsLimit: number
  }
  plans: PlanOption[]
  availableProviders: string[]
  botUsername: string | null
  billingEnabled: boolean
  hostedFeaturesEnabled: boolean
}

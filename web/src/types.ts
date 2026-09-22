export type Tab = 'home' | 'kitchen' | 'plans' | 'account'

export interface WorkspaceCard {
  id: number
  text: string
  buttons: { text: string; url: string | null; action: string | null }[][]
  reply: boolean
  document: { name: string; data: string } | null
}

export interface WorkspaceState {
  id: string
  busy: boolean
  cards: WorkspaceCard[]
  notices: string[]
  error: string | null
  registered?: boolean
  hostedFeaturesEnabled?: boolean
}

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
    receiptsLimit: number | null
    actionsUsed: number
    actionsLimit: number | null
  }
  plans: PlanOption[]
  availableProviders: string[]
  botUsername: string | null
  billingEnabled: boolean
  hostedFeaturesEnabled: boolean
}

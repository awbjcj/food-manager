import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { cancelRenewal, createCheckout, loadAccount, saveAccount } from './api'
import type { AccountData, PlanOption, Tab } from './types'

type IconName =
  | 'account' | 'arrow' | 'brain' | 'calendar' | 'check' | 'close'
  | 'home' | 'household' | 'leaf' | 'pantry' | 'plan' | 'receipt'
  | 'recipes' | 'refresh' | 'shopping' | 'sparkle'

function Icon({ name, size = 24 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    account: <><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></>,
    arrow: <path d="m9 18 6-6-6-6"/>,
    brain: <><path d="M9.5 4a3 3 0 0 0-5 2.2A3 3 0 0 0 3 11a3 3 0 0 0 1.5 4.8A3 3 0 0 0 9.5 18Z"/><path d="M14.5 4a3 3 0 0 1 5 2.2A3 3 0 0 1 21 11a3 3 0 0 1-1.5 4.8 3 3 0 0 1-5 2.2ZM9.5 8h5M9.5 13h5M12 4v16"/></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v11h14V10M9 21v-7h6v7"/></>,
    household: <><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20a6 6 0 0 1 12 0M14 15a5 5 0 0 1 7 4.5"/></>,
    leaf: <><path d="M20 4c-8 0-14 4-14 10 0 3 2 5 5 5 6 0 9-7 9-15Z"/><path d="M4 21c2-6 6-9 12-12"/></>,
    pantry: <><path d="M5 5h14l-1 16H6L5 5Z"/><path d="M4 5h16M8 2h8l1 3M9 10h6M9 14h6"/></>,
    plan: <><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h6M9 11h6M9 15h4"/></>,
    receipt: <path d="M6 3v18l3-2 3 2 3-2 3 2V3l-3 2-3-2-3 2-3-2Zm3 7h6m-6 4h5"/>,
    recipes: <><path d="M4 5a3 3 0 0 1 3-3h5v18H7a3 3 0 0 0-3 2V5Z"/><path d="M20 5a3 3 0 0 0-3-3h-5v18h5a3 3 0 0 1 3 2V5Z"/></>,
    refresh: <><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></>,
    shopping: <><path d="M3 4h2l2.4 10.2a2 2 0 0 0 2 1.6H18a2 2 0 0 0 2-1.6L21 8H7"/><circle cx="10" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></>,
    sparkle: <><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3Z"/><path d="m19 14 .7 2.3L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.7L19 14Z"/></>,
  }
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function Logo() {
  return <div className="brand-mark"><Icon name="leaf" size={22}/></div>
}

function UsageRow({ icon, label, used, limit }: { icon: IconName; label: string; used: number; limit: number }) {
  const percent = Math.min(100, Math.round((used / Math.max(limit, 1)) * 100))
  return <div className="usage-row">
    <span className="icon-disc"><Icon name={icon}/></span>
    <div className="usage-content">
      <div className="usage-copy"><strong>{label}</strong><span>{used} of {limit} used</span></div>
      <div className="progress" aria-label={`${label}: ${used} of ${limit} used`}><span style={{ width: `${percent}%` }}/></div>
    </div>
  </div>
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <h2 className="section-label">{children}</h2>
}

function OpenRow({ icon, title, detail, action, onClick }: { icon: IconName; title: string; detail?: string; action?: string; onClick?: () => void }) {
  return <button className="open-row" onClick={onClick} type="button">
    <span className="icon-disc"><Icon name={icon}/></span>
    <span className="row-copy"><strong>{title}</strong>{detail && <small>{detail}</small>}</span>
    {action && <span className="row-action">{action}</span>}
    <Icon name="arrow" size={20}/>
  </button>
}

function Header({ data }: { data: AccountData }) {
  const initials = data.user.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()
  return <header className="brand-header"><div className="brand"><Logo/><span>Food Manager</span></div><div className="avatar" aria-label={data.user.name}>{initials}</div></header>
}

type QuickAccess = 'pantry' | 'plan' | 'shopping' | 'favorites'

function HomeView({ data, selectTab, openChat }: { data: AccountData; selectTab: (tab: Tab) => void; openChat: (destination?: QuickAccess) => void }) {
  const greeting = new Date().getHours() >= 17 ? 'Good evening' : new Date().getHours() >= 12 ? 'Good afternoon' : 'Good morning'
  const firstName = data.user.name.split(' ')[0]
  const reset = new Date(data.plan.periodEnd).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return <main className="page home-page">
    <Header data={data}/>
    <section className="hero-copy"><h1>{greeting}, {firstName}</h1><p>Your household is running smoothly.</p></section>
    <button className="plan-band" onClick={() => selectTab('plans')}><span className="plan-dot"><Icon name="leaf"/></span><strong>{data.plan.tier === 'family' ? 'Family plan' : 'Free plan'}</strong><span>View plans</span><Icon name="arrow"/></button>
    <section className="usage-list">
      <UsageRow icon="receipt" label="Receipts" used={data.quota.receiptsUsed} limit={data.quota.receiptsLimit}/>
      <UsageRow icon="brain" label="AI actions" used={data.quota.actionsUsed} limit={data.quota.actionsLimit}/>
      <p className="reset-copy"><Icon name="refresh" size={18}/> Resets {reset}</p>
    </section>
    <section><SectionLabel>My household</SectionLabel><OpenRow icon="household" title={data.household.name} detail={`${data.household.members} members`} action="Manage" onClick={() => selectTab('account')}/></section>
    <section><SectionLabel>Quick access</SectionLabel><div className="open-list">
      <OpenRow icon="pantry" title="Pantry" onClick={() => openChat('pantry')}/>
      <OpenRow icon="calendar" title="Meal plan" onClick={() => openChat('plan')}/>
      <OpenRow icon="shopping" title="Shopping list" onClick={() => openChat('shopping')}/>
      <OpenRow icon="recipes" title="Saved recipes" onClick={() => openChat('favorites')}/>
    </div></section>
  </main>
}

function PlanFeatures({ plan }: { plan: PlanOption }) {
  return <ul className="plan-features">
    <li><Icon name="receipt" size={19}/>{plan.receipts} receipts</li>
    <li><Icon name="sparkle" size={19}/>{plan.actions} AI actions</li>
    {plan.seats != null && <li><Icon name="household" size={19}/>{plan.seats} household members</li>}
  </ul>
}

function PlansView({ data, checkout, manage }: { data: AccountData; checkout: (sku: string) => void; manage: () => void }) {
  const free = data.plans.find(plan => plan.code === 'free')!
  const family = data.plans.find(plan => plan.code === 'family_monthly')!
  const topups = data.plans.filter(plan => plan.kind === 'topup')
  const familyActive = data.plan.tier === 'family'
  return <main className="page plans-page">
    <section className="page-heading"><h1>Choose your plan</h1><p>Simple household plans, paid securely with Telegram Stars.</p><small>30-day billing · cancel anytime</small></section>
    <section className="plan-option">
      <div className="plan-option-head"><span className="icon-disc"><Icon name="leaf"/></span><div><h2>Free</h2><p>0 Stars</p></div><button className="button secondary compact" disabled={!familyActive}>{!familyActive ? 'Current plan' : 'Included'}</button></div>
      <PlanFeatures plan={free}/>
    </section>
    <section className="plan-option featured">
      <div className="plan-option-head"><span className="icon-disc"><Icon name="household"/></span><div><h2>Family</h2><p>500 Stars / 30 days</p></div><button className="button primary compact" disabled={!data.billingEnabled} onClick={familyActive ? manage : () => checkout(family.code)}>{familyActive ? 'Manage plan' : 'Upgrade to Family'}</button></div>
      <PlanFeatures plan={family}/>
    </section>
    <section className="topups"><h2>Need a little more?</h2>{topups.map(plan => <OpenRow key={plan.code} icon={plan.receipts ? 'receipt' : 'sparkle'} title={`${plan.title} · ${plan.stars} Stars`} onClick={() => checkout(plan.code)}/>)}</section>
    {!data.billingEnabled && <p className="notice">Payments are currently unavailable.</p>}
    <p className="payment-note">Payments are processed by Telegram Stars.</p>
  </main>
}

const languages: Record<string, string> = { en: 'English', zh: '中文', fr: 'Français', es: 'Español' }
const commonZones = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Asia/Shanghai']

function AccountView({ data, onSaved, selectTab, openChat }: { data: AccountData; onSaved: (data: AccountData) => void; selectTab: (tab: Tab) => void; openChat: (destination?: QuickAccess) => void }) {
  const [form, setForm] = useState({ householdName: data.household.name, digestHour: data.user.digestHour, timeZone: data.user.timeZone, language: data.user.language, provider: data.user.provider })
  const [status, setStatus] = useState('')
  const initials = data.user.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()
  const zones = useMemo(() => Array.from(new Set([data.user.timeZone, ...commonZones])), [data.user.timeZone])
  async function submit(event: FormEvent) {
    event.preventDefault(); setStatus('Saving…')
    try { await saveAccount(form); onSaved({ ...data, user: { ...data.user, language: form.language, timeZone: form.timeZone, digestHour: form.digestHour, provider: form.provider }, household: { ...data.household, name: form.householdName } }); setStatus('Changes saved') }
    catch (error) { setStatus(error instanceof Error ? error.message : 'Could not save changes') }
  }
  return <main className="page account-page">
    <section className="page-heading"><h1>Account</h1><p>Your preferences follow you across the bot.</p></section>
    <div className="profile-row"><div className="avatar large">{initials}</div><div><strong>{data.user.name}</strong><span>{data.user.role === 'owner' ? 'Household owner' : 'Household member'}</span></div></div>
    <form onSubmit={submit}>
      <fieldset><legend>Household</legend><label>Household name<input value={form.householdName} disabled={data.user.role !== 'owner'} maxLength={80} onChange={e => setForm({ ...form, householdName: e.target.value })}/></label><p className="field-note">{data.household.members} of {data.household.seatCap} seats used</p></fieldset>
      <fieldset><legend>Daily digest</legend><label>Delivery time<select value={form.digestHour} onChange={e => setForm({ ...form, digestHour: Number(e.target.value) })}>{Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{new Date(2026, 0, 1, hour).toLocaleTimeString([], { hour: 'numeric' })}</option>)}</select></label><label>Time zone<select value={form.timeZone} onChange={e => setForm({ ...form, timeZone: e.target.value })}>{zones.map(zone => <option key={zone}>{zone}</option>)}</select></label></fieldset>
      <fieldset><legend>Preferences</legend><label>Language<select value={form.language} onChange={e => setForm({ ...form, language: e.target.value })}>{Object.entries(languages).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label><label>AI provider<select value={form.provider} onChange={e => setForm({ ...form, provider: e.target.value })}>{data.availableProviders.map(provider => <option key={provider} value={provider}>{provider[0].toUpperCase() + provider.slice(1)}</option>)}</select></label></fieldset>
      <div className="form-actions"><button className="button primary" type="submit">Save changes</button><button className="button secondary" type="button" onClick={() => openChat()}>Open bot chat</button>{status && <p role="status" className="save-status">{status}</p>}</div>
    </form>
    <button className="subscription-row" onClick={() => selectTab('plans')}><strong>Subscription</strong><span>{data.plan.tier === 'family' ? 'Family plan' : 'Free plan'}</span><b>View plans</b><Icon name="arrow" size={20}/></button>
  </main>
}

function BottomNav({ tab, select }: { tab: Tab; select: (tab: Tab) => void }) {
  return <nav className="bottom-nav" aria-label="Primary"><button className={tab === 'home' ? 'active' : ''} onClick={() => select('home')}><Icon name="home"/><span>Home</span></button><button className={tab === 'plans' ? 'active' : ''} onClick={() => select('plans')}><Icon name="plan"/><span>Plans</span></button><button className={tab === 'account' ? 'active' : ''} onClick={() => select('account')}><Icon name="account"/><span>Account</span></button></nav>
}

function ManageSheet({ data, close, cancel }: { data: AccountData; close: () => void; cancel: () => void }) {
  const until = new Date(data.plan.periodEnd).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return <div className="sheet-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}><section className="sheet" role="dialog" aria-modal="true" aria-labelledby="manage-title"><button className="sheet-close" onClick={close} aria-label="Close"><Icon name="close"/></button><div className="sheet-handle"/><h2 id="manage-title">Manage Family plan</h2><div className="active-status"><span><Icon name="check" size={17}/></span><div><strong>Active until {until}</strong><p>{data.plan.renews ? 'Renews automatically in Telegram' : 'Renewal is cancelled'}</p></div></div><button className="button primary" onClick={close}>Keep plan</button>{data.plan.renews && data.plan.canManage && <button className="cancel-button" onClick={cancel}>Cancel renewal</button>}</section></div>
}

export function App() {
  const [data, setData] = useState<AccountData | null>(null)
  const [tab, setTab] = useState<Tab>('home')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [manage, setManage] = useState(false)
  useEffect(() => { const controller = new AbortController(); loadAccount().then(setData).catch(err => setError(err instanceof Error ? err.message : 'Could not load account')); return () => controller.abort() }, [])
  function openChat(destination?: QuickAccess) {
    if (!data?.botUsername) { setError('Bot link is unavailable right now.'); return }
    const suffix = destination ? `?start=qa_${destination}` : ''
    const url = `https://t.me/${data.botUsername}${suffix}`
    const tg = window.Telegram?.WebApp
    if (!tg) { window.location.assign(url); return }
    tg.openTelegramLink(url)
    window.setTimeout(() => tg.close(), 120)
  }
  async function checkout(sku: string) {
    if (!data || busy) return
    setBusy(true); setError('')
    try {
      const url = await createCheckout(sku)
      const tg = window.Telegram?.WebApp
      if (tg && !url.includes('$demo')) tg.openInvoice(url, status => { if (status === 'paid') setTimeout(() => loadAccount().then(setData), 900) })
      else if (sku === 'family_monthly') setData({ ...data, household: { ...data.household, seatCap: 10 }, plan: { ...data.plan, tier: 'family', renews: true }, quota: { ...data.quota, receiptsLimit: 100, actionsLimit: 300 } })
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not start checkout') }
    finally { setBusy(false) }
  }
  async function cancel() { if (!data) return; setBusy(true); try { await cancelRenewal(); setData({ ...data, plan: { ...data.plan, renews: false } }); setManage(false) } catch (err) { setError(err instanceof Error ? err.message : 'Could not cancel renewal') } finally { setBusy(false) } }
  if (error && !data) return <main className="fatal-state"><Logo/><h1>Open Food Manager in Telegram</h1><p>{error}</p><button className="button primary" onClick={() => window.location.reload()}>Try again</button></main>
  if (!data) return <main className="loading-state"><Logo/><div className="spinner"/><span>Preparing your household…</span></main>
  return <div className={busy ? 'app busy' : 'app'}>
    {error && <button className="error-toast" onClick={() => setError('')}>{error}<Icon name="close" size={18}/></button>}
    {tab === 'home' && <HomeView data={data} selectTab={setTab} openChat={openChat}/>}
    {tab === 'plans' && <PlansView data={data} checkout={checkout} manage={() => setManage(true)}/>}
    {tab === 'account' && <AccountView data={data} onSaved={setData} selectTab={setTab} openChat={openChat}/>}
    <BottomNav tab={tab} select={setTab}/>
    {manage && <ManageSheet data={data} close={() => setManage(false)} cancel={cancel}/>}
  </div>
}

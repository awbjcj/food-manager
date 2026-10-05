import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { cancelRenewal, createCheckout, loadAccount, saveAccount } from './api'
import {
  countLabel,
  detectLocale,
  formatDigestHour,
  formatNumber,
  formatShortDate,
  greetingKey,
  hourInTimeZone,
  languageNames,
  planTitle,
  resolveLocale,
  t,
  type Locale,
  type MessageKey,
} from './i18n'
import type { AccountData, PlanOption, Tab } from './types'
import { WorkspaceView } from './Workspace'
import { w } from './workspace-copy'
import { Icon, type IconName } from './Icon'

function Logo() {
  return <div className="brand-mark" aria-hidden="true"><Icon name="leaf" size={22} /></div>
}

function UsageRow({ icon, label, used, limit, locale }: { icon: IconName; label: string; used: number; limit: number | null; locale: Locale }) {
  const percent = limit == null ? 0 : Math.min(100, Math.round((used / Math.max(limit, 1)) * 100))
  const values = { used: formatNumber(locale, used), limit: limit == null ? t(locale, 'usage.unlimited') : formatNumber(locale, limit) }
  return <div className="usage-row">
    <span className="icon-disc"><Icon name={icon} /></span>
    <div className="usage-content">
      <div className="usage-copy"><strong>{label}</strong><span>{t(locale, 'usage.usedOf', values)}</span></div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={limit ?? undefined} aria-valuenow={limit == null ? undefined : Math.min(used, limit)} aria-valuetext={limit == null ? t(locale, 'usage.ariaUnlimited', { label, used: values.used }) : undefined} aria-label={t(locale, 'usage.ariaUsedOf', { label, ...values })}>
        <span style={{ width: String(percent) + '%' }} />
      </div>
    </div>
  </div>
}

function currentPlanName(data: AccountData, locale: Locale): string {
  if (data.plan.tier === 'unlimited') return t(locale, 'plan.unlimitedPlan')
  return data.plan.tier === 'family' ? t(locale, 'plan.familyPlan') : t(locale, 'plan.freePlan')
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <h2 className="section-label">{children}</h2>
}

function OpenRow({ icon, title, detail, action, onClick, disabled = false }: { icon: IconName; title: string; detail?: string; action?: string; onClick?: () => void; disabled?: boolean }) {
  return <button className="open-row" onClick={onClick} type="button" disabled={disabled}>
    <span className="icon-disc"><Icon name={icon} /></span>
    <span className="row-copy"><strong>{title}</strong>{detail && <small>{detail}</small>}</span>
    {action && <span className="row-action">{action}</span>}
    <Icon name="arrow" size={20} />
  </button>
}

function Header({ data, locale, openAccount }: { data: AccountData; locale: Locale; openAccount: () => void }) {
  const initials = data.user.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()
  return <header className="brand-header"><div className="brand"><Logo /><span>{t(locale, 'brand.name')}</span></div><button type="button" className="avatar" onClick={openAccount} aria-label={`${t(locale, 'account.title')} · ${data.user.name}`}>{initials}</button></header>
}

type QuickAccess = 'pantry' | 'photo' | 'cook' | 'plan' | 'shopping' | 'favorites' | 'prefs' | 'stats' | 'household'

function HomeView({ data, locale, selectTab, openFeature }: { data: AccountData; locale: Locale; selectTab: (tab: Tab) => void; openFeature: (destination?: QuickAccess) => void }) {
  const firstName = data.user.name.split(' ')[0]
  const greeting = t(locale, greetingKey(hourInTimeZone(data.user.timeZone)), { name: firstName })
  const reset = formatShortDate(locale, new Date(data.plan.periodEnd), data.user.timeZone)
  const shortcuts = [
    { command: 'pantry', icon: 'pantry', label: t(locale, 'shortcut.pantry'), detail: w(locale, 'pantryShortcut') },
    { command: 'photo', icon: 'receipt', label: w(locale, 'photo'), detail: w(locale, 'photoShortcut') },
    { command: 'cook', icon: 'recipes', label: t(locale, 'shortcut.cook'), detail: w(locale, 'cookShortcut') },
    { command: 'shopping', icon: 'shopping', label: t(locale, 'shortcut.shopping'), detail: w(locale, 'shoppingShortcut') },
  ] as const
  return <main className="page home-page">
    <Header data={data} locale={locale} openAccount={() => selectTab('account')} />
    <section className="hero-copy"><h1>{greeting}</h1><p>{w(locale, 'subtitle')}</p></section>
    <div className="home-layout"><section className="home-tools" aria-labelledby="quick-access-title">
    <h2 id="quick-access-title" className="section-label">{t(locale, 'home.quickAccess')}</h2>
    <div className="quick-grid">{shortcuts.map(shortcut => <button key={shortcut.command} type="button" className="quick-card" onClick={() => openFeature(shortcut.command)}>
      <span className="icon-disc"><Icon name={shortcut.icon} /></span><strong>{shortcut.label}</strong><small>{shortcut.detail}</small><Icon name="arrow" size={18} />
    </button>)}</div>
    <div className="open-list home-secondary">
      <OpenRow icon="calendar" title={t(locale, 'shortcut.plan')} onClick={() => openFeature('plan')} />
      <OpenRow icon="recipes" title={t(locale, 'shortcut.favorites')} onClick={() => openFeature('favorites')} />
      <OpenRow icon="brain" title={t(locale, 'shortcut.preferences')} onClick={() => openFeature('prefs')} />
      <OpenRow icon="plan" title={t(locale, 'shortcut.stats')} onClick={() => openFeature('stats')} />
    </div></section>
    {data.hostedFeaturesEnabled && <aside className="home-overview" aria-label={w(locale, 'overview')}>
    <SectionLabel>{w(locale, 'overview')}</SectionLabel>
    <button type="button" className="plan-band" onClick={() => selectTab('plans')}><span className="plan-dot"><Icon name="leaf" /></span><strong>{currentPlanName(data, locale)}</strong><span>{t(locale, 'home.viewPlans')}</span><Icon name="arrow" /></button>
    <section className="usage-list">
      <UsageRow icon="receipt" label={t(locale, 'usage.receipts')} used={data.quota.receiptsUsed} limit={data.quota.receiptsLimit} locale={locale} />
      <UsageRow icon="brain" label={t(locale, 'usage.actions')} used={data.quota.actionsUsed} limit={data.quota.actionsLimit} locale={locale} />
      <p className="reset-copy"><Icon name="refresh" size={18} /> {t(locale, 'usage.resets', { date: reset })}</p>
    </section>
    <section className="content-section"><SectionLabel>{t(locale, 'home.myHousehold')}</SectionLabel><OpenRow icon="household" title={data.household.name} detail={countLabel(locale, data.household.members, 'count.member.one', 'count.member.many')} action={t(locale, 'home.manage')} onClick={() => openFeature('household')} /></section>
    </aside>}</div>
  </main>
}

function PlanFeatures({ plan, locale }: { plan: PlanOption; locale: Locale }) {
  return <ul className="plan-features">
    <li><Icon name="receipt" size={19} />{countLabel(locale, plan.receipts, 'count.receipt.one', 'count.receipt.many')}</li>
    <li><Icon name="sparkle" size={19} />{countLabel(locale, plan.actions, 'count.action.one', 'count.action.many')}</li>
    {plan.seats != null && <li><Icon name="household" size={19} />{countLabel(locale, plan.seats, 'count.member.one', 'count.member.many')}</li>}
  </ul>
}

function PlansView({ data, locale, checkout, manage, busy }: { data: AccountData; locale: Locale; checkout: (sku: string) => void; manage: () => void; busy: boolean }) {
  const free = data.plans.find(plan => plan.code === 'free')!
  const family = data.plans.find(plan => plan.code === 'family_monthly')!
  const topups = data.plans.filter(plan => plan.kind === 'topup')
  const familyActive = data.plan.tier === 'family'
  const unlimitedActive = data.plan.tier === 'unlimited'
  return <main className="page plans-page">
    <section className="page-heading"><h1>{t(locale, 'plan.choose')}</h1><p>{t(locale, 'plan.subtitle')}</p><small>{t(locale, 'plan.billingCycle')}</small></section>
    <section className="plan-option">
      <div className="plan-option-head"><span className="icon-disc"><Icon name="leaf" /></span><div><h2>{planTitle(locale, free.code, free.title)}</h2><p>{t(locale, 'plan.freePrice')}</p></div><span className="plan-badge">{familyActive || unlimitedActive ? t(locale, 'plan.included') : t(locale, 'plan.current')}</span></div>
      <PlanFeatures plan={free} locale={locale} />
    </section>
    <section className="plan-option featured">
      <div className="plan-option-head"><span className="icon-disc"><Icon name="household" /></span><div><h2>{planTitle(locale, family.code, family.title)}</h2><p>{t(locale, 'plan.familyPrice', { stars: formatNumber(locale, family.stars) })}</p></div><button className="button primary compact" disabled={busy || unlimitedActive || (!familyActive && !data.billingEnabled)} onClick={familyActive ? manage : () => checkout(family.code)}>{unlimitedActive ? t(locale, 'plan.included') : familyActive ? t(locale, 'plan.manage') : t(locale, 'plan.upgrade')}</button></div>
      <PlanFeatures plan={family} locale={locale} />
    </section>
    {!unlimitedActive && <section className="topups"><h2>{t(locale, 'plan.needMore')}</h2>{topups.map(plan => <OpenRow key={plan.code} icon={plan.receipts ? 'receipt' : 'sparkle'} title={planTitle(locale, plan.code, plan.title)} detail={t(locale, 'plan.stars', { count: formatNumber(locale, plan.stars) })} disabled={busy || !data.billingEnabled} onClick={() => checkout(plan.code)} />)}</section>}
    {unlimitedActive && <p className="notice">{t(locale, 'plan.unlimitedManaged')}</p>}
    {!data.billingEnabled && <p className="notice">{t(locale, 'plan.paymentsUnavailable')}</p>}
    <p className="payment-note">{t(locale, 'plan.paymentNote')}</p>
  </main>
}

const commonZones = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Asia/Shanghai']

function AccountView({ data, locale, onSaved, selectTab }: { data: AccountData; locale: Locale; onSaved: (data: AccountData) => void; selectTab: (tab: Tab) => void }) {
  const [form, setForm] = useState({ householdName: data.household.name, digestHour: data.user.digestHour, timeZone: data.user.timeZone, language: data.user.language, provider: data.user.provider })
  const [savedForm, setSavedForm] = useState(form)
  const [status, setStatus] = useState<MessageKey | null>(null)
  const saving = status === 'account.saving'
  const dirty = Object.keys(form).some(key => form[key as keyof typeof form] !== savedForm[key as keyof typeof form])
  const initials = data.user.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()
  const zones = useMemo(() => Array.from(new Set([data.user.timeZone, ...commonZones])), [data.user.timeZone])
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (saving || !dirty) return
    const submitted = { ...form, householdName: form.householdName.trim() }
    setStatus('account.saving')
    try {
      await saveAccount(submitted)
      onSaved({ ...data, user: { ...data.user, language: submitted.language, timeZone: submitted.timeZone, digestHour: submitted.digestHour, provider: submitted.provider }, household: { ...data.household, name: submitted.householdName } })
      setForm(submitted)
      setSavedForm(submitted)
      setStatus('account.saved')
    } catch {
      setStatus('error.save')
    }
  }
  return <main className="page account-page">
    <section className="page-heading"><h1>{t(locale, 'account.title')}</h1><p>{t(locale, 'account.subtitle')}</p></section>
    <div className="profile-row"><div className="avatar large" aria-hidden="true">{initials}</div><div><strong>{data.user.name}</strong><span>{data.user.role === 'owner' ? t(locale, 'account.owner') : t(locale, 'account.member')}</span></div></div>
    <form onSubmit={submit} onChange={() => setStatus(null)} aria-busy={saving}>
      <fieldset disabled={saving}><legend>{t(locale, 'account.household')}</legend><label>{t(locale, 'account.householdName')}<input required value={form.householdName} disabled={data.user.role !== 'owner'} maxLength={80} onChange={event => setForm({ ...form, householdName: event.target.value })} /></label>{data.hostedFeaturesEnabled && <p className="field-note">{t(locale, 'account.seatsUsed', { used: formatNumber(locale, data.household.members), limit: formatNumber(locale, data.household.seatCap) })}</p>}</fieldset>
      <fieldset disabled={saving}><legend>{t(locale, 'account.dailyDigest')}</legend><div className="form-fields"><label>{t(locale, 'account.deliveryTime')}<select value={form.digestHour} onChange={event => setForm({ ...form, digestHour: Number(event.target.value) })}>{Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{formatDigestHour(locale, hour)}</option>)}</select></label><label>{t(locale, 'account.timeZone')}<select value={form.timeZone} onChange={event => setForm({ ...form, timeZone: event.target.value })}>{zones.map(zone => <option key={zone}>{zone}</option>)}</select></label></div></fieldset>
      <fieldset disabled={saving}><legend>{t(locale, 'account.preferences')}</legend><div className="form-fields"><label>{t(locale, 'account.language')}<select value={form.language} onChange={event => setForm({ ...form, language: event.target.value })}>{Object.entries(languageNames).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label><label>{t(locale, 'account.provider')}<select value={form.provider} onChange={event => setForm({ ...form, provider: event.target.value })}>{data.availableProviders.map(provider => <option key={provider} value={provider}>{provider === 'openai' ? 'OpenAI' : provider === 'deepseek' ? 'DeepSeek' : provider[0].toUpperCase() + provider.slice(1)}</option>)}</select></label></div></fieldset>
      <div className="form-actions"><p role={status === 'error.save' ? 'alert' : 'status'} className={`save-status${status === 'error.save' ? ' save-error' : ''}`}>{status ? t(locale, status) : w(locale, dirty ? 'unsavedChanges' : 'allSaved')}</p><button className="button primary" type="submit" disabled={saving || !dirty}>{t(locale, saving ? 'account.saving' : 'account.save')}</button><button className="button secondary" type="button" disabled={saving || !dirty} onClick={() => { setForm(savedForm); setStatus(null) }}>{w(locale, 'discardChanges')}</button></div>
    </form>
    {data.hostedFeaturesEnabled && <button className="subscription-row" onClick={() => selectTab('plans')}><strong>{t(locale, 'account.subscription')}</strong><span>{currentPlanName(data, locale)}</span><b>{t(locale, 'home.viewPlans')}</b><Icon name="arrow" size={20} /></button>}
  </main>
}

function BottomNav({ tab, locale, select, hostedFeaturesEnabled }: { tab: Tab; locale: Locale; select: (tab: Tab) => void; hostedFeaturesEnabled: boolean }) {
  return <nav className="bottom-nav" aria-label={t(locale, 'nav.primary')}><button type="button" className={tab === 'home' ? 'active' : ''} aria-current={tab === 'home' ? 'page' : undefined} onClick={() => select('home')}><Icon name="home" /><span>{t(locale, 'nav.home')}</span></button><button type="button" className={tab === 'kitchen' ? 'active' : ''} aria-current={tab === 'kitchen' ? 'page' : undefined} onClick={() => select('kitchen')}><Icon name="pantry" /><span>{w(locale, 'kitchen')}</span></button>{hostedFeaturesEnabled && <button type="button" className={tab === 'plans' ? 'active' : ''} aria-current={tab === 'plans' ? 'page' : undefined} onClick={() => select('plans')}><Icon name="plan" /><span>{t(locale, 'nav.plans')}</span></button>}<button type="button" className={tab === 'account' ? 'active' : ''} aria-current={tab === 'account' ? 'page' : undefined} onClick={() => select('account')}><Icon name="account" /><span>{t(locale, 'nav.account')}</span></button></nav>
}

function ManageSheet({ data, locale, close, cancel }: { data: AccountData; locale: Locale; close: () => void; cancel: () => void }) {
  const until = formatShortDate(locale, new Date(data.plan.periodEnd), data.user.timeZone)
  const sheetRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        close()
        return
      }
      if (event.key !== 'Tab' || !sheetRef.current) return
      const focusable = Array.from(sheetRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'))
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [close])
  return <div className="sheet-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}><section ref={sheetRef} className="sheet" role="dialog" aria-modal="true" aria-labelledby="manage-title"><button ref={closeRef} className="sheet-close" type="button" onClick={close} aria-label={t(locale, 'common.close')}><Icon name="close" /></button><div className="sheet-handle" aria-hidden="true" /><h2 id="manage-title">{t(locale, 'manage.title')}</h2><div className="active-status"><span><Icon name="check" size={17} /></span><div><strong>{t(locale, 'manage.activeUntil', { date: until })}</strong><p>{data.plan.renews ? t(locale, 'manage.renews') : t(locale, 'manage.cancelled')}</p></div></div><button className="button primary" type="button" onClick={close}>{t(locale, 'manage.keep')}</button>{data.plan.renews && data.plan.canManage && <button className="cancel-button" type="button" onClick={cancel}>{t(locale, 'manage.cancel')}</button>}</section></div>
}

export function App() {
  const [initialLocale] = useState<Locale>(() => detectLocale())
  const [data, setData] = useState<AccountData | null>(null)
  const [tab, setTab] = useState<Tab>('home')
  const [error, setError] = useState<MessageKey | null>(null)
  const [busy, setBusy] = useState(false)
  const [manage, setManage] = useState(false)
  const [workspaceEntry, setWorkspaceEntry] = useState({ command: 'pantry', nonce: 0 })
  const locale = data ? resolveLocale(data.user.language) : initialLocale
  useEffect(() => { window.scrollTo(0, 0) }, [tab])
  useEffect(() => {
    document.documentElement.lang = locale
    document.title = t(locale, 'brand.name')
  }, [locale])
  useEffect(() => {
    const controller = new AbortController()
    loadAccount().then(setData).catch(() => setError('error.loadAccount'))
    return () => controller.abort()
  }, [])
  function openFeature(destination?: QuickAccess) {
    setWorkspaceEntry(previous => ({ command: destination === 'plan' ? 'plan_current' : destination ?? 'pantry', nonce: previous.nonce + 1 }))
    setTab('kitchen')
  }
  function refreshAccount() {
    loadAccount().then(account => { setData(account); setError(null) }).catch(() => { /* Leaving a household still permits joining through the workspace. */ })
  }
  async function checkout(sku: string) {
    if (!data || busy) return
    setBusy(true)
    setError(null)
    try {
      const url = await createCheckout(sku)
      const telegram = window.Telegram?.WebApp
      if (telegram && !url.includes('$demo')) {
        telegram.openInvoice(url, status => {
          if (status === 'paid') window.setTimeout(() => loadAccount().then(setData), 900)
        })
      } else if (sku === 'family_monthly') {
        setData({ ...data, household: { ...data.household, seatCap: 10 }, plan: { ...data.plan, tier: 'family', renews: true }, quota: { ...data.quota, receiptsLimit: 100, actionsLimit: 300 } })
      }
    } catch {
      setError('error.checkout')
    } finally {
      setBusy(false)
    }
  }
  async function cancel() {
    if (!data) return
    setBusy(true)
    try {
      await cancelRenewal()
      setData({ ...data, plan: { ...data.plan, renews: false } })
      setManage(false)
    } catch {
      setError('error.cancel')
    } finally {
      setBusy(false)
    }
  }
  if (error && !data) return <div className="app"><WorkspaceView data={null} locale={locale} entry={{ command: 'start', nonce: 0 }} onAccountChanged={refreshAccount} /></div>
  if (!data) return <main className="loading-state"><Logo /><div className="spinner" /><span>{t(locale, 'loading.preparing')}</span></main>
  return <div className={busy ? 'app busy' : 'app'} aria-busy={busy}>
    {error && <div className="error-toast" role="alert"><span>{t(locale, error)}</span><button type="button" onClick={() => setError(null)} aria-label={t(locale, 'common.close')}><Icon name="close" size={18} /></button></div>}
    {tab === 'home' && <HomeView data={data} locale={locale} selectTab={setTab} openFeature={openFeature} />}
    {tab === 'plans' && data.hostedFeaturesEnabled && <PlansView data={data} locale={locale} checkout={checkout} manage={() => setManage(true)} busy={busy} />}
    {tab === 'account' && <AccountView data={data} locale={locale} onSaved={setData} selectTab={setTab} />}
    {tab === 'kitchen' && <WorkspaceView data={data} locale={locale} entry={workspaceEntry} onAccountChanged={refreshAccount} />}
    <BottomNav tab={tab} locale={locale} select={setTab} hostedFeaturesEnabled={data.hostedFeaturesEnabled} />
    {manage && <ManageSheet data={data} locale={locale} close={() => setManage(false)} cancel={cancel} />}
  </div>
}

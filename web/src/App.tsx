import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
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

type IconName =
  | 'account' | 'arrow' | 'brain' | 'calendar' | 'check' | 'close'
  | 'home' | 'household' | 'leaf' | 'pantry' | 'plan' | 'receipt'
  | 'recipes' | 'refresh' | 'shopping' | 'sparkle'

function Icon({ name, size = 24 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    account: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
    arrow: <path d="m9 18 6-6-6-6" />,
    brain: <><path d="M9.5 4a3 3 0 0 0-5 2.2A3 3 0 0 0 3 11a3 3 0 0 0 1.5 4.8A3 3 0 0 0 9.5 18Z" /><path d="M14.5 4a3 3 0 0 1 5 2.2A3 3 0 0 1 21 11a3 3 0 0 1-1.5 4.8A3 3 0 0 1 14.5 18ZM9.5 8h5M9.5 13h5M12 4v16" /></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    home: <><path d="m3 11 9-8 9 8" /><path d="M5 10v11h14V10M9 21v-7h6v7" /></>,
    household: <><circle cx="9" cy="8" r="3" /><circle cx="17" cy="9" r="2.5" /><path d="M3 20a6 6 0 0 1 12 0M14 15a5 5 0 0 1 7 4.5" /></>,
    leaf: <><path d="M20 4c-8 0-14 4-14 10 0 3 2 5 5 5 6 0 9-7 9-15Z" /><path d="M4 21c2-6 6-9 12-12" /></>,
    pantry: <><path d="M5 5h14l-1 16H6L5 5Z" /><path d="M4 5h16M8 2h8l1 3M9 10h6M9 14h6" /></>,
    plan: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 7h6M9 11h6M9 15h4" /></>,
    receipt: <path d="M6 3v18l3-2 3 2 3-2 3 2V3l-3 2-3-2-3 2-3-2Zm3 7h6m-6 4h5" />,
    recipes: <><path d="M4 5a3 3 0 0 1 3-3h5v18H7a3 3 0 0 0-3 2V5Z" /><path d="M20 5a3 3 0 0 0-3-3h-5v18h5a3 3 0 0 1 3 2V5Z" /></>,
    refresh: <><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></>,
    shopping: <><path d="M3 4h2l2.4 10.2a2 2 0 0 0 2 1.6H18a2 2 0 0 0 2-1.6L21 8H7" /><circle cx="10" cy="20" r="1" /><circle cx="18" cy="20" r="1" /></>,
    sparkle: <><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2L12 3Z" /><path d="m19 14 .7 2.3L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.7L19 14Z" /></>,
  }
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function Logo() {
  return <div className="brand-mark"><Icon name="leaf" size={22} /></div>
}

function UsageRow({ icon, label, used, limit, locale }: { icon: IconName; label: string; used: number; limit: number; locale: Locale }) {
  const percent = Math.min(100, Math.round((used / Math.max(limit, 1)) * 100))
  const values = { used: formatNumber(locale, used), limit: formatNumber(locale, limit) }
  return <div className="usage-row">
    <span className="icon-disc"><Icon name={icon} /></span>
    <div className="usage-content">
      <div className="usage-copy"><strong>{label}</strong><span>{t(locale, 'usage.usedOf', values)}</span></div>
      <div className="progress" aria-label={t(locale, 'usage.ariaUsedOf', { label, ...values })}>
        <span style={{ width: String(percent) + '%' }} />
      </div>
    </div>
  </div>
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <h2 className="section-label">{children}</h2>
}

function OpenRow({ icon, title, detail, action, onClick }: { icon: IconName; title: string; detail?: string; action?: string; onClick?: () => void }) {
  return <button className="open-row" onClick={onClick} type="button">
    <span className="icon-disc"><Icon name={icon} /></span>
    <span className="row-copy"><strong>{title}</strong>{detail && <small>{detail}</small>}</span>
    {action && <span className="row-action">{action}</span>}
    <Icon name="arrow" size={20} />
  </button>
}

function Header({ data, locale }: { data: AccountData; locale: Locale }) {
  const initials = data.user.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()
  return <header className="brand-header"><div className="brand"><Logo /><span>{t(locale, 'brand.name')}</span></div><div className="avatar" aria-label={data.user.name}>{initials}</div></header>
}

type QuickAccess = 'pantry' | 'cook' | 'plan' | 'shopping' | 'favorites' | 'prefs' | 'stats'

function HomeView({ data, locale, selectTab, openChat }: { data: AccountData; locale: Locale; selectTab: (tab: Tab) => void; openChat: (destination?: QuickAccess) => void }) {
  const firstName = data.user.name.split(' ')[0]
  const greeting = t(locale, greetingKey(hourInTimeZone(data.user.timeZone)), { name: firstName })
  const reset = formatShortDate(locale, new Date(data.plan.periodEnd), data.user.timeZone)
  return <main className="page home-page">
    <Header data={data} locale={locale} />
    <section className="hero-copy"><h1>{greeting}</h1><p>{t(locale, 'home.running')}</p></section>
    {data.hostedFeaturesEnabled && <button className="plan-band" onClick={() => selectTab('plans')}><span className="plan-dot"><Icon name="leaf" /></span><strong>{data.plan.tier === 'family' ? t(locale, 'plan.familyPlan') : t(locale, 'plan.freePlan')}</strong><span>{t(locale, 'home.viewPlans')}</span><Icon name="arrow" /></button>}
    {data.hostedFeaturesEnabled && <section className="usage-list">
      <UsageRow icon="receipt" label={t(locale, 'usage.receipts')} used={data.quota.receiptsUsed} limit={data.quota.receiptsLimit} locale={locale} />
      <UsageRow icon="brain" label={t(locale, 'usage.actions')} used={data.quota.actionsUsed} limit={data.quota.actionsLimit} locale={locale} />
      <p className="reset-copy"><Icon name="refresh" size={18} /> {t(locale, 'usage.resets', { date: reset })}</p>
    </section>}
    {data.hostedFeaturesEnabled && <section><SectionLabel>{t(locale, 'home.myHousehold')}</SectionLabel><OpenRow icon="household" title={data.household.name} detail={countLabel(locale, data.household.members, 'count.member.one', 'count.member.many')} action={t(locale, 'home.manage')} onClick={() => selectTab('account')} /></section>}
    <section><SectionLabel>{t(locale, 'home.quickAccess')}</SectionLabel><div className="open-list">
      <OpenRow icon="pantry" title={t(locale, 'shortcut.pantry')} action="/pantry" onClick={() => openChat('pantry')} />
      <OpenRow icon="recipes" title={t(locale, 'shortcut.cook')} action="/cook" onClick={() => openChat('cook')} />
      <OpenRow icon="calendar" title={t(locale, 'shortcut.plan')} action="/plan" onClick={() => openChat('plan')} />
      <OpenRow icon="shopping" title={t(locale, 'shortcut.shopping')} action="/shopping" onClick={() => openChat('shopping')} />
      <OpenRow icon="recipes" title={t(locale, 'shortcut.favorites')} action="/favorites" onClick={() => openChat('favorites')} />
      <OpenRow icon="brain" title={t(locale, 'shortcut.preferences')} action="/prefs" onClick={() => openChat('prefs')} />
      <OpenRow icon="plan" title={t(locale, 'shortcut.stats')} action="/stats" onClick={() => openChat('stats')} />
    </div></section>
  </main>
}

function PlanFeatures({ plan, locale }: { plan: PlanOption; locale: Locale }) {
  return <ul className="plan-features">
    <li><Icon name="receipt" size={19} />{countLabel(locale, plan.receipts, 'count.receipt.one', 'count.receipt.many')}</li>
    <li><Icon name="sparkle" size={19} />{countLabel(locale, plan.actions, 'count.action.one', 'count.action.many')}</li>
    {plan.seats != null && <li><Icon name="household" size={19} />{countLabel(locale, plan.seats, 'count.member.one', 'count.member.many')}</li>}
  </ul>
}

function PlansView({ data, locale, checkout, manage }: { data: AccountData; locale: Locale; checkout: (sku: string) => void; manage: () => void }) {
  const free = data.plans.find(plan => plan.code === 'free')!
  const family = data.plans.find(plan => plan.code === 'family_monthly')!
  const topups = data.plans.filter(plan => plan.kind === 'topup')
  const familyActive = data.plan.tier === 'family'
  return <main className="page plans-page">
    <section className="page-heading"><h1>{t(locale, 'plan.choose')}</h1><p>{t(locale, 'plan.subtitle')}</p><small>{t(locale, 'plan.billingCycle')}</small></section>
    <section className="plan-option">
      <div className="plan-option-head"><span className="icon-disc"><Icon name="leaf" /></span><div><h2>{planTitle(locale, free.code, free.title)}</h2><p>{t(locale, 'plan.freePrice')}</p></div><button className="button secondary compact" disabled>{familyActive ? t(locale, 'plan.included') : t(locale, 'plan.current')}</button></div>
      <PlanFeatures plan={free} locale={locale} />
    </section>
    <section className="plan-option featured">
      <div className="plan-option-head"><span className="icon-disc"><Icon name="household" /></span><div><h2>{planTitle(locale, family.code, family.title)}</h2><p>{t(locale, 'plan.familyPrice', { stars: formatNumber(locale, family.stars) })}</p></div><button className="button primary compact" disabled={!data.billingEnabled} onClick={familyActive ? manage : () => checkout(family.code)}>{familyActive ? t(locale, 'plan.manage') : t(locale, 'plan.upgrade')}</button></div>
      <PlanFeatures plan={family} locale={locale} />
    </section>
    <section className="topups"><h2>{t(locale, 'plan.needMore')}</h2>{topups.map(plan => <OpenRow key={plan.code} icon={plan.receipts ? 'receipt' : 'sparkle'} title={[planTitle(locale, plan.code, plan.title), t(locale, 'plan.stars', { count: formatNumber(locale, plan.stars) })].join(' · ')} onClick={() => checkout(plan.code)} />)}</section>
    {!data.billingEnabled && <p className="notice">{t(locale, 'plan.paymentsUnavailable')}</p>}
    <p className="payment-note">{t(locale, 'plan.paymentNote')}</p>
  </main>
}

const commonZones = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Asia/Shanghai']

function AccountView({ data, locale, onSaved, selectTab, openChat }: { data: AccountData; locale: Locale; onSaved: (data: AccountData) => void; selectTab: (tab: Tab) => void; openChat: (destination?: QuickAccess) => void }) {
  const [form, setForm] = useState({ householdName: data.household.name, digestHour: data.user.digestHour, timeZone: data.user.timeZone, language: data.user.language, provider: data.user.provider })
  const [status, setStatus] = useState<MessageKey | null>(null)
  const initials = data.user.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()
  const zones = useMemo(() => Array.from(new Set([data.user.timeZone, ...commonZones])), [data.user.timeZone])
  async function submit(event: FormEvent) {
    event.preventDefault()
    setStatus('account.saving')
    try {
      await saveAccount(form)
      onSaved({ ...data, user: { ...data.user, language: form.language, timeZone: form.timeZone, digestHour: form.digestHour, provider: form.provider }, household: { ...data.household, name: form.householdName } })
      setStatus('account.saved')
    } catch {
      setStatus('error.save')
    }
  }
  return <main className="page account-page">
    <section className="page-heading"><h1>{t(locale, 'account.title')}</h1><p>{t(locale, 'account.subtitle')}</p></section>
    <div className="profile-row"><div className="avatar large">{initials}</div><div><strong>{data.user.name}</strong><span>{data.user.role === 'owner' ? t(locale, 'account.owner') : t(locale, 'account.member')}</span></div></div>
    <form onSubmit={submit}>
      <fieldset><legend>{t(locale, 'account.household')}</legend><label>{t(locale, 'account.householdName')}<input value={form.householdName} disabled={data.user.role !== 'owner'} maxLength={80} onChange={event => setForm({ ...form, householdName: event.target.value })} /></label>{data.hostedFeaturesEnabled && <p className="field-note">{t(locale, 'account.seatsUsed', { used: formatNumber(locale, data.household.members), limit: formatNumber(locale, data.household.seatCap) })}</p>}</fieldset>
      <fieldset><legend>{t(locale, 'account.dailyDigest')}</legend><label>{t(locale, 'account.deliveryTime')}<select value={form.digestHour} onChange={event => setForm({ ...form, digestHour: Number(event.target.value) })}>{Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{formatDigestHour(locale, hour)}</option>)}</select></label><label>{t(locale, 'account.timeZone')}<select value={form.timeZone} onChange={event => setForm({ ...form, timeZone: event.target.value })}>{zones.map(zone => <option key={zone}>{zone}</option>)}</select></label></fieldset>
      <fieldset><legend>{t(locale, 'account.preferences')}</legend><label>{t(locale, 'account.language')}<select value={form.language} onChange={event => setForm({ ...form, language: event.target.value })}>{Object.entries(languageNames).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label><label>{t(locale, 'account.provider')}<select value={form.provider} onChange={event => setForm({ ...form, provider: event.target.value })}>{data.availableProviders.map(provider => <option key={provider} value={provider}>{provider[0].toUpperCase() + provider.slice(1)}</option>)}</select></label></fieldset>
      <div className="form-actions"><button className="button primary" type="submit">{t(locale, 'account.save')}</button><button className="button secondary" type="button" onClick={() => openChat()}>{t(locale, 'account.openChat')}</button>{status && <p role="status" className="save-status">{t(locale, status)}</p>}</div>
    </form>
    {data.hostedFeaturesEnabled && <button className="subscription-row" onClick={() => selectTab('plans')}><strong>{t(locale, 'account.subscription')}</strong><span>{data.plan.tier === 'family' ? t(locale, 'plan.familyPlan') : t(locale, 'plan.freePlan')}</span><b>{t(locale, 'home.viewPlans')}</b><Icon name="arrow" size={20} /></button>}
  </main>
}

function BottomNav({ tab, locale, select, hostedFeaturesEnabled }: { tab: Tab; locale: Locale; select: (tab: Tab) => void; hostedFeaturesEnabled: boolean }) {
  return <nav className="bottom-nav" aria-label={t(locale, 'nav.primary')}><button className={tab === 'home' ? 'active' : ''} onClick={() => select('home')}><Icon name="home" /><span>{t(locale, 'nav.home')}</span></button>{hostedFeaturesEnabled && <button className={tab === 'plans' ? 'active' : ''} onClick={() => select('plans')}><Icon name="plan" /><span>{t(locale, 'nav.plans')}</span></button>}<button className={tab === 'account' ? 'active' : ''} onClick={() => select('account')}><Icon name="account" /><span>{t(locale, 'nav.account')}</span></button></nav>
}

function ManageSheet({ data, locale, close, cancel }: { data: AccountData; locale: Locale; close: () => void; cancel: () => void }) {
  const until = formatShortDate(locale, new Date(data.plan.periodEnd), data.user.timeZone)
  return <div className="sheet-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}><section className="sheet" role="dialog" aria-modal="true" aria-labelledby="manage-title"><button className="sheet-close" onClick={close} aria-label={t(locale, 'common.close')}><Icon name="close" /></button><div className="sheet-handle" /><h2 id="manage-title">{t(locale, 'manage.title')}</h2><div className="active-status"><span><Icon name="check" size={17} /></span><div><strong>{t(locale, 'manage.activeUntil', { date: until })}</strong><p>{data.plan.renews ? t(locale, 'manage.renews') : t(locale, 'manage.cancelled')}</p></div></div><button className="button primary" onClick={close}>{t(locale, 'manage.keep')}</button>{data.plan.renews && data.plan.canManage && <button className="cancel-button" onClick={cancel}>{t(locale, 'manage.cancel')}</button>}</section></div>
}

export function App() {
  const [initialLocale] = useState<Locale>(() => detectLocale())
  const [data, setData] = useState<AccountData | null>(null)
  const [tab, setTab] = useState<Tab>('home')
  const [error, setError] = useState<MessageKey | null>(null)
  const [busy, setBusy] = useState(false)
  const [manage, setManage] = useState(false)
  const locale = data ? resolveLocale(data.user.language) : initialLocale
  useEffect(() => {
    document.documentElement.lang = locale
    document.title = t(locale, 'brand.name')
  }, [locale])
  useEffect(() => {
    const controller = new AbortController()
    loadAccount().then(setData).catch(() => setError('error.loadAccount'))
    return () => controller.abort()
  }, [])
  function openChat(destination?: QuickAccess) {
    if (!data?.botUsername) {
      setError('error.botUnavailable')
      return
    }
    const suffix = destination ? '?start=qa_' + destination : ''
    const url = 'https://t.me/' + data.botUsername + suffix
    const telegram = window.Telegram?.WebApp
    if (!telegram) {
      window.location.assign(url)
      return
    }
    telegram.openTelegramLink(url)
    window.setTimeout(() => telegram.close(), 120)
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
  if (error && !data) return <main className="fatal-state"><Logo /><h1>{t(locale, 'error.openInTelegram.title')}</h1><p>{t(locale, error)}</p><button className="button primary" onClick={() => window.location.reload()}>{t(locale, 'error.tryAgain')}</button></main>
  if (!data) return <main className="loading-state"><Logo /><div className="spinner" /><span>{t(locale, 'loading.preparing')}</span></main>
  return <div className={busy ? 'app busy' : 'app'}>
    {error && <button className="error-toast" onClick={() => setError(null)} aria-label={t(locale, 'common.close')}>{t(locale, error)}<Icon name="close" size={18} /></button>}
    {tab === 'home' && <HomeView data={data} locale={locale} selectTab={setTab} openChat={openChat} />}
    {tab === 'plans' && data.hostedFeaturesEnabled && <PlansView data={data} locale={locale} checkout={checkout} manage={() => setManage(true)} />}
    {tab === 'account' && <AccountView data={data} locale={locale} onSaved={setData} selectTab={setTab} openChat={openChat} />}
    <BottomNav tab={tab} locale={locale} select={setTab} hostedFeaturesEnabled={data.hostedFeaturesEnabled} />
    {manage && <ManageSheet data={data} locale={locale} close={() => setManage(false)} cancel={cancel} />}
  </div>
}

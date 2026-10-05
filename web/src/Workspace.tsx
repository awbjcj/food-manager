import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError, loadWorkspace, uploadReceipt, workspaceAction } from './api'
import { languageNames, type Locale } from './i18n'
import type { AccountData, WorkspaceCard, WorkspaceState } from './types'
import { w, type WorkspaceKey } from './workspace-copy'
import { Icon, type IconName } from './Icon'

type Feature = { command: WorkspaceKey; group: WorkspaceKey; input?: 'text' | 'item' | 'correct' | 'snooze' | 'plan' | 'member' | 'code' | 'groupId' | 'filter' | 'invite' | 'hour' | 'language' | 'provider' | 'zone'; hosted?: boolean; owner?: boolean; confirm?: boolean }
export const features: Feature[] = [
  { command: 'pantry', group: 'pantryGroup' },
  { command: 'photo', group: 'pantryGroup' },
  { command: 'add', group: 'pantryGroup', input: 'text' },
  { command: 'list', group: 'pantryGroup', input: 'filter' },
  { command: 'correct', group: 'pantryGroup', input: 'correct' },
  { command: 'ate', group: 'pantryGroup', input: 'item', confirm: true },
  { command: 'toss', group: 'pantryGroup', input: 'item', confirm: true },
  { command: 'delete', group: 'pantryGroup', input: 'item', confirm: true },
  { command: 'snooze', group: 'pantryGroup', input: 'snooze' },
  { command: 'stats', group: 'pantryGroup' },
  { command: 'cook', group: 'mealsGroup' },
  { command: 'plan_current', group: 'mealsGroup' },
  { command: 'plan', group: 'mealsGroup', input: 'plan', confirm: true },
  { command: 'calendar', group: 'mealsGroup' },
  { command: 'shopping', group: 'mealsGroup' },
  { command: 'favorites', group: 'mealsGroup' },
  { command: 'history', group: 'mealsGroup' },
  { command: 'prefs', group: 'mealsGroup', input: 'text' },
  { command: 'household', group: 'householdGroup', hosted: true },
  { command: 'invite', group: 'householdGroup', input: 'invite', hosted: true },
  { command: 'join', group: 'householdGroup', input: 'code', hosted: true },
  { command: 'leave', group: 'householdGroup', hosted: true, confirm: true },
  { command: 'remove', group: 'householdGroup', input: 'member', hosted: true, owner: true, confirm: true },
  { command: 'bind', group: 'householdGroup', input: 'groupId', hosted: true, confirm: true },
  { command: 'tz', group: 'settingsGroup', input: 'zone' },
  { command: 'lang', group: 'settingsGroup', input: 'language' },
  { command: 'digest_at', group: 'settingsGroup', input: 'hour' },
  { command: 'llm', group: 'settingsGroup', input: 'provider' },
  { command: 'quota', group: 'settingsGroup', hosted: true },
  { command: 'buy', group: 'settingsGroup', hosted: true },
  { command: 'billing', group: 'settingsGroup', hosted: true },
  { command: 'help', group: 'settingsGroup' },
  { command: 'start', group: 'settingsGroup' },
  { command: 'text', group: 'settingsGroup', input: 'text' },
]

const groups = [
  { key: 'pantryGroup', hint: 'pantryHint', icon: 'pantry', primary: ['pantry', 'photo', 'add', 'list'] },
  { key: 'mealsGroup', hint: 'mealsHint', icon: 'recipes', primary: ['cook', 'plan_current', 'shopping', 'favorites'] },
  { key: 'householdGroup', hint: 'householdHint', icon: 'household', primary: ['household', 'invite', 'join', 'bind'] },
  { key: 'settingsGroup', hint: 'settingsHint', icon: 'account', primary: ['text', 'help', 'lang', 'llm', 'start'] },
] as const

const featureIcons: Partial<Record<WorkspaceKey, IconName>> = {
  pantry: 'pantry', photo: 'receipt', add: 'pantry', list: 'pantry', correct: 'plan',
  ate: 'check', toss: 'close', delete: 'close', snooze: 'refresh', stats: 'plan',
  cook: 'recipes', plan_current: 'calendar', plan: 'calendar', calendar: 'calendar',
  shopping: 'shopping', favorites: 'recipes', history: 'refresh', prefs: 'brain',
  household: 'household', invite: 'household', join: 'household', leave: 'arrow', remove: 'close', bind: 'household',
  tz: 'refresh', lang: 'account', digest_at: 'calendar', llm: 'brain', quota: 'plan',
  buy: 'sparkle', billing: 'receipt', help: 'recipes', start: 'leaf', text: 'sparkle',
}

function errorText(cause: unknown, locale: Locale): string {
  if (!(cause instanceof ApiError)) return w(locale, 'error')
  if (cause.status === 401 || cause.status === 403) return w(locale, 'access')
  if (cause.status === 413) return w(locale, 'receiptHint')
  if (cause.status >= 500) return w(locale, 'unavailable')
  return cause.message || w(locale, 'invalidRequest')
}

function safeUrl(raw: string): string | null {
  try {
    const url = new URL(raw)
    return url.protocol === 'https:' ? url.href : null
  } catch { return null }
}

function openLink(url: string) {
  const safe = safeUrl(url)
  if (!safe) return
  const parsed = new URL(safe)
  const telegram = window.Telegram?.WebApp
  if (telegram && parsed.hostname === 't.me' && (parsed.pathname.startsWith('/$') || parsed.pathname.startsWith('/invoice/'))) {
    telegram.openInvoice(safe)
  } else {
    window.open(safe, '_blank', 'noopener,noreferrer')
  }
}

function Card({ card, busy, locale, act, reply, setReply, latest = false }: { card: WorkspaceCard; busy: boolean; locale: Locale; act: (body: object) => void; reply: string; setReply: (value: string) => void; latest?: boolean }) {
  function download() {
    if (!card.document) return
    const bytes = Uint8Array.from(atob(card.document.data), char => char.charCodeAt(0))
    const url = URL.createObjectURL(new Blob([bytes], { type: 'text/calendar;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = card.document.name
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <article className="workspace-card">
    <div className="workspace-card-label"><Icon name={latest ? 'recipes' : 'refresh'} size={16} /><span>{w(locale, latest ? 'latestResult' : 'earlierResults')}</span></div>
    <div className="workspace-result-text">{card.text.split(/(https:\/\/[^\s<>]+)/g).map((part, index) => safeUrl(part) ? <a key={index} href={part} target="_blank" rel="noopener noreferrer">{part}</a> : part)}</div>
    {card.buttons.map((row, index) => <div className="workspace-button-row" key={index}>{row.map((button, i) => <button className="button secondary" key={i} type="button" disabled={busy || (!button.action && !safeUrl(button.url ?? ''))} onClick={() => button.action ? act({ kind: 'callback', cardId: card.id, action: button.action }) : openLink(button.url!)}>{button.text}</button>)}</div>)}
    {card.reply && <form onSubmit={event => { event.preventDefault(); act({ kind: 'reply', cardId: card.id, text: reply }) }}><label>{w(locale, 'reply')}<textarea required maxLength={4000} value={reply} onChange={event => setReply(event.target.value)} /></label><button className="button primary" disabled={busy || !reply.trim()}>{w(locale, 'run')}</button></form>}
    {card.document && <button type="button" className="button secondary" onClick={download}>{w(locale, 'download')}</button>}
  </article>
}

export function WorkspaceView({ data, locale, entry, onAccountChanged }: { data: AccountData | null; locale: Locale; entry: { command: string; nonce: number }; onAccountChanged: () => void }) {
  const [state, setState] = useState<WorkspaceState | null>(null)
  const [selected, setSelected] = useState(entry.command)
  const [value, setValue] = useState('')
  const [extra, setExtra] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({})
  const active = useRef(true)
  const inFlight = useRef(false)
  const wasBusy = useRef(false)
  const resultsRef = useRef<HTMLElement>(null)
  const awaitingResult = useRef(false)
  const accountChanged = useRef(onAccountChanged)
  accountChanged.current = onAccountChanged
  const busy = sending || !!state?.busy
  const hosted = data?.hostedFeaturesEnabled ?? state?.hostedFeaturesEnabled ?? false
  const registered = state?.registered ?? !!data
  const available = features.filter(item => registered ? (!item.hosted || hosted) && (!item.owner || data?.user.role === 'owner') : item.command === 'start' || (item.command === 'join' && hosted))
  const feature = available.find(item => item.command === selected) ?? available[0]
  const category = groups.find(group => group.key === feature.group)!
  const categoryFeatures = available.filter(item => item.group === feature.group)
  const primary = category.primary as readonly string[]
  const extraFeatures = categoryFeatures.filter(item => !primary.includes(item.command))
  const needsFoodId = ['item', 'correct', 'snooze'].includes(feature.input ?? '')
  const cards = state?.cards.slice().reverse() ?? []

  function choose(command: string, reset = false) {
    if (command === selected && !reset) return
    const next = features.find(item => item.command === command)
    const nextCategory = groups.find(group => group.key === next?.group)
    const advanced = !!nextCategory && !(nextCategory.primary as readonly string[]).includes(command)
    setShowMore(current => next?.group !== feature.group ? advanced : current || advanced)
    setSelected(command)
    setValue('')
    setExtra('')
    setConfirmation(false)
    setFile(null)
  }

  useEffect(() => { choose(entry.command, true) }, [entry.command, entry.nonce])
  useEffect(() => {
    if (state && selected !== feature.command) choose(feature.command)
  }, [state, selected, feature.command])
  useEffect(() => {
    active.current = true
    let stopped = false
    let authenticated = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await loadWorkspace()
        if (stopped) return
        setState(result)
        if (wasBusy.current && !result.busy) accountChanged.current()
        wasBusy.current = result.busy
        if (!result.registered) setSelected(current => current === 'join' ? current : 'start')
      } catch (cause) {
        authenticated = !(cause instanceof ApiError && cause.status === 401)
        if (!stopped) setError(w(locale, authenticated ? 'error' : 'access'))
      } finally {
        if (!stopped && authenticated) timer = setTimeout(poll, 1500)
      }
    }
    void poll()
    return () => { stopped = true; active.current = false; clearTimeout(timer) }
  }, [locale])

  useEffect(() => {
    if (!state || busy || !awaitingResult.current) return
    awaitingResult.current = false
    resultsRef.current?.focus({ preventScroll: true })
    if (window.matchMedia('(max-width: 759px)').matches) resultsRef.current?.scrollIntoView({ block: 'start' })
  }, [state, busy])

  async function act(body: object, receipt?: File) {
    if (!state || busy || inFlight.current) return
    inFlight.current = true
    setSending(true)
    setError(null)
    setConfirmation(false)
    try {
      const result = receipt ? await uploadReceipt(state.id, receipt) : await workspaceAction(state.id, body)
      if (!active.current) return
      setState(current => ({ ...current, ...result }))
      wasBusy.current = true
      accountChanged.current()
      awaitingResult.current = 'kind' in body && body.kind !== 'callback' && body.kind !== 'reply'
    } catch (cause) {
      if (active.current) setError(errorText(cause, locale))
    } finally {
      inFlight.current = false
      if (active.current) setSending(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (feature.confirm && !confirmation) { setConfirmation(true); return }
    const text = [value, extra].filter(Boolean).join(' ')
    if (feature.command === 'photo') {
      if (!file || file.size > 10 * 1024 * 1024 || !['image/jpeg', 'image/png'].includes(file.type)) { setError(w(locale, 'receiptHint')); return }
      void act({ kind: 'photo' }, file)
    } else {
      void act({ kind: feature.command === 'text' ? 'text' : 'command', command: feature.command, text })
    }
  }

  const input = feature.input
  function renderCard(card: WorkspaceCard, latest = false) {
    return <Card key={card.id} card={card} locale={locale} busy={busy} latest={latest} act={body => void act(body)} reply={replyDrafts[card.id] ?? ''} setReply={reply => setReplyDrafts(current => ({ ...current, [card.id]: reply }))} />
  }
  return <main className="page workspace-page">
    <section className="page-heading"><h1>{w(locale, 'kitchen')}</h1><p>{w(locale, registered ? 'subtitle' : 'onboarding')}</p></section>
    <div className="workspace-categories" role="group" aria-label={w(locale, 'categories')}>
      {groups.filter(group => available.some(item => item.group === group.key)).map(group =>
        <button key={group.key} type="button" disabled={busy || !state} aria-pressed={feature.group === group.key}
          onClick={() => { if (feature.group !== group.key) choose(available.find(item => item.group === group.key)!.command) }}>
          <Icon name={group.icon} size={20} /><span>{w(locale, group.key)}</span>
        </button>,
      )}
    </div>
    <div className="workspace-layout">
      <section className="workspace-controls" aria-label={w(locale, 'kitchen')}>
        <div className="workspace-control-heading"><h2>{w(locale, category.key)}</h2><p>{w(locale, category.hint)}</p></div>
        <div className="workspace-feature-picker" role="group" aria-label={w(locale, 'feature')}>
          <div className="workspace-feature-grid">{categoryFeatures.filter(item => primary.includes(item.command)).map(item => <button key={item.command} type="button" disabled={busy || !state} aria-pressed={feature.command === item.command} onClick={() => choose(item.command)}><Icon name={featureIcons[item.command] ?? category.icon} size={20} /><span>{w(locale, item.command)}</span></button>)}</div>
          {extraFeatures.length > 0 && <><button className="disclosure-button" type="button" disabled={busy || !state} aria-expanded={showMore} aria-controls="more-kitchen-actions" onClick={() => setShowMore(current => !current)}><span>{w(locale, showMore ? 'fewerActions' : 'moreActions')}</span><Icon name="arrow" size={18} /></button><div id="more-kitchen-actions" className="workspace-feature-grid secondary-features" hidden={!showMore}>{extraFeatures.map(item => <button key={item.command} type="button" disabled={busy || !state} aria-pressed={feature.command === item.command} onClick={() => choose(item.command)}><Icon name={featureIcons[item.command] ?? category.icon} size={18} /><span>{w(locale, item.command)}</span></button>)}</div></>}
        </div>
        <div className="workspace-selected"><small>{w(locale, 'selectedAction')}</small><h3>{w(locale, feature.command)}</h3></div>
        <form onSubmit={submit} onChange={() => setConfirmation(false)}>
          <fieldset className="workspace-action" disabled={busy || !state}>
            <legend>{w(locale, feature.command)}</legend>
            <div className="workspace-fields" key={feature.command}>
            {selected === 'photo' && <label className="receipt-picker"><span>{w(locale, 'choosePhoto')}</span><input type="file" accept="image/jpeg,image/png" required aria-describedby="receipt-hint" onChange={event => setFile(event.target.files?.[0] ?? null)} /><span className="receipt-name">{file?.name ?? w(locale, 'noPhoto')}</span><small id="receipt-hint">{w(locale, 'receiptHint')}</small></label>}
            {['item', 'correct', 'snooze', 'member', 'groupId', 'plan', 'hour'].includes(input ?? '') && <label>{w(locale, input === 'member' ? 'member' : input === 'groupId' ? 'groupId' : input === 'plan' ? 'days' : input === 'hour' ? 'digest_at' : 'item')}<input type="number" step="1" min={input === 'groupId' ? undefined : input === 'hour' ? 0 : input === 'plan' ? 3 : 1} max={input === 'hour' ? 23 : input === 'plan' ? 7 : undefined} required value={value} onChange={event => setValue(event.target.value)} /></label>}
            {(input === 'text' || input === 'correct') && <label>{w(locale, selected === 'add' ? 'addHint' : selected === 'prefs' ? 'prefsHint' : 'details')}<textarea maxLength={input === 'correct' ? 3900 : 4000} required={selected !== 'prefs'} value={input === 'correct' ? extra : value} onChange={event => input === 'correct' ? setExtra(event.target.value) : setValue(event.target.value)} /></label>}
            {input === 'snooze' && <label>{w(locale, 'days')}<input type="number" min={1} max={30} step="1" required value={extra} onChange={event => setExtra(event.target.value)} /></label>}
            {(input === 'code' || input === 'zone') && <label>{w(locale, input === 'code' ? 'code' : 'tz')}<input required maxLength={150} placeholder={input === 'zone' ? 'America/New_York' : undefined} value={value} onChange={event => setValue(event.target.value)} /></label>}
            {input === 'filter' && <label>{w(locale, 'filter')}<select value={value} onChange={event => setValue(event.target.value)}><option value="">{w(locale, 'all')}</option><option value="week">{w(locale, 'due')}</option><option value="expired">{w(locale, 'expired')}</option>{['produce', 'dairy', 'meat', 'seafood', 'bakery', 'pantry', 'frozen', 'beverage', 'other'].map(category => <option key={category} value={category}>{w(locale, category === 'pantry' ? 'pantryCategory' : category as WorkspaceKey)}</option>)}</select></label>}
            {input === 'invite' && <label>{w(locale, 'mode')}<select value={value} onChange={event => setValue(event.target.value)}><option value="">{w(locale, 'single')}</option><option value="family">{w(locale, 'family')}</option></select></label>}
            {input === 'language' && <label>{w(locale, 'lang')}<select required value={value} onChange={event => setValue(event.target.value)}><option value="">{w(locale, 'choose')}</option>{Object.entries(languageNames).map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select></label>}
            {input === 'provider' && <label>{w(locale, 'llm')}<select required value={value} onChange={event => setValue(event.target.value)}><option value="">{w(locale, 'choose')}</option>{data?.availableProviders.map(provider => <option key={provider} value={provider}>{provider === 'openai' ? 'OpenAI' : provider === 'deepseek' ? 'DeepSeek' : provider[0].toUpperCase() + provider.slice(1)}</option>)}</select></label>}
            </div>
            {needsFoodId && <div className="workspace-field-help"><p>{w(locale, 'foodIdHint')}</p><button type="button" className="disclosure-button" onClick={() => void act({ kind: 'command', command: 'pantry', text: '' })}>{w(locale, 'openPantry')}<Icon name="arrow" size={16} /></button></div>}
            {confirmation && <div className="workspace-confirm" role="alert"><strong>{w(locale, 'confirm')}</strong><p>{w(locale, 'confirmHint')}</p><p>{w(locale, feature.command)} {value} {extra}</p><button type="button" className="button secondary" onClick={() => setConfirmation(false)}>{w(locale, 'cancel')}</button></div>}
            <button className="button primary workspace-submit" type="submit"><Icon name={confirmation ? 'check' : featureIcons[feature.command] ?? category.icon} size={20} /><span>{w(locale, !state ? 'connecting' : busy ? 'working' : confirmation ? 'confirmAction' : feature.command)}</span></button>
          </fieldset>
        </form>
        {error && <p className="workspace-error" role="alert">{error}</p>}
        <div className="workspace-operation" role="status">{busy && <><span className="spinner" aria-hidden="true" /><span>{w(locale, 'loading')}</span></>}</div>
        {cards.length > 0 && <button type="button" className="disclosure-button activity-link" onClick={() => { resultsRef.current?.focus(); resultsRef.current?.scrollIntoView({ block: 'start' }) }}>{w(locale, 'viewActivity')}<Icon name="arrow" size={18} /></button>}
      </section>
      <section ref={resultsRef} tabIndex={-1} className="workspace-results" aria-label={w(locale, 'results')} aria-busy={busy}>
        <div className="workspace-results-heading"><h2>{w(locale, 'results')}</h2><button className="button secondary compact" type="button" onClick={() => loadWorkspace().then(result => { if (active.current) { setState(result); setError(null) } }).catch(() => { if (active.current) setError(w(locale, 'error')) })}><Icon name="refresh" size={16} />{w(locale, 'refresh')}</button></div>
        {state?.error && <p className="workspace-error" role="alert">{state.error}</p>}
        <div role="status">{busy && <p>{w(locale, 'loading')}</p>}{state?.notices.map((notice, index) => <p className="workspace-notice" key={index}>{notice}</p>)}</div>
        {!cards.length && !busy && <div className="workspace-empty"><span className="icon-disc"><Icon name="recipes" size={24} /></span><strong>{w(locale, state ? 'ready' : 'connecting')}</strong><p>{w(locale, 'empty')}</p></div>}
        {cards[0] && renderCard(cards[0], true)}
        {cards.length > 1 && <details className="workspace-history"><summary>{w(locale, 'earlierResults')} <span className="history-count">{cards.length - 1}</span><Icon name="arrow" size={18} /></summary>{cards.slice(1).map(card => renderCard(card))}</details>}
      </section>
    </div>
  </main>
}

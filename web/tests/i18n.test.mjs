import assert from 'node:assert/strict'
import test from 'node:test'
import { messages, supportedLocales, t, resolveLocale } from '../src/i18n.ts'
import { workspaceCopy, w } from '../src/workspace-copy.ts'

const placeholders = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()

test('every screen and Kitchen message has complete translations and matching placeholders', () => {
  for (const locale of supportedLocales) {
    assert.deepEqual(Object.keys(messages[locale]).sort(), Object.keys(messages.en).sort())
    for (const [key, english] of Object.entries(messages.en)) {
      assert.ok(t(locale, key).trim(), `${locale}/${key}`)
      assert.deepEqual(placeholders(t(locale, key)), placeholders(english), `${locale}/${key}`)
    }
  }
  for (const [key, variants] of Object.entries(workspaceCopy)) {
    assert.equal(variants.length, supportedLocales.length, key)
    for (const text of variants) {
      assert.ok(text.trim(), key)
      assert.deepEqual(placeholders(text), placeholders(variants[0]), key)
    }
  }
})

test('shared nouns stay consistent across Home, Kitchen, and Account', () => {
  const shared = {
    pantryGroup: 'shortcut.pantry', householdGroup: 'account.household',
    cook: 'shortcut.cook', shopping: 'shortcut.shopping', favorites: 'shortcut.favorites',
    prefs: 'shortcut.preferences', stats: 'shortcut.stats', tz: 'account.timeZone',
    lang: 'account.language', llm: 'account.provider',
  }
  for (const locale of supportedLocales) {
    for (const [key, other] of Object.entries(shared)) assert.equal(w(locale, key), t(locale, other))
  }
})

test('regional Telegram locales resolve before rendering and placeholders interpolate', () => {
  for (const [locale, expected] of [['zh-CN', 'zh'], ['fr-CA', 'fr'], ['es-MX', 'es'], ['de', 'en']]) {
    assert.equal(resolveLocale(locale), expected)
    assert.ok(w(resolveLocale(locale), 'working'))
  }
  assert.equal(t('fr', 'usage.usedOf', { used: 3, limit: 20 }), '3 sur 20 utilisés')
})

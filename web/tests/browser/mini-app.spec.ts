import { expect, test, type Page } from '@playwright/test'
import type { WorkspaceState } from '../../src/types'

async function mockKitchen(page: Page, options: { language?: string; hosted?: boolean; billing?: boolean; tier?: string; role?: string } = {}) {
  const account = {
    user: { telegramId: 1, name: 'Alex Chen', role: options.role ?? 'owner', language: options.language ?? 'en', timeZone: 'America/New_York', digestHour: 8, provider: 'gemini' },
    household: { name: 'The Chen kitchen', members: 2, seatCap: 2 },
    plan: { tier: options.tier ?? 'free', status: 'active', periodEnd: '2026-11-01T08:00:00', renews: true, canManage: true },
    quota: { receiptsUsed: 3, receiptsLimit: 5, actionsUsed: 18, actionsLimit: 30 },
    plans: [
      { code: 'free', title: 'Free', stars: 0, kind: 'tier', receipts: 5, actions: 30, seats: 2 },
      { code: 'family_monthly', title: 'Family plan', stars: 500, kind: 'subscription', receipts: 100, actions: 300, seats: 10 },
      { code: 'topup_receipts_50', title: '+50 receipts', stars: 250, kind: 'topup', receipts: 50, actions: 0, seats: null },
      { code: 'topup_actions_150', title: '+150 AI actions', stars: 250, kind: 'topup', receipts: 0, actions: 150, seats: null },
    ],
    availableProviders: ['gemini', 'openai'], billingEnabled: options.billing ?? true, hostedFeaturesEnabled: options.hosted ?? true,
  }
  const state: WorkspaceState = { id: 'preview', busy: false, cards: [], notices: [], error: null, registered: true, hostedFeaturesEnabled: account.hostedFeaturesEnabled }
  const actions: Record<string, unknown>[] = []
  await page.route('https://telegram.org/**', route => route.fulfill({ body: '' }))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/account') {
      if (route.request().method() === 'PATCH') {
        const payload = route.request().postDataJSON()
        Object.assign(account.user, { language: payload.language, timeZone: payload.timeZone, digestHour: payload.digestHour, provider: payload.provider })
        account.household.name = payload.householdName
      }
      await route.fulfill({ json: account })
    } else if (path === '/api/workspace/actions') {
      const body = route.request().postDataJSON()
      actions.push(body)
      state.cards.push({ id: state.cards.length + 1, text: `Result for ${body.command}: food #12`, buttons: [], reply: false, document: null })
      await route.fulfill({ json: state })
    } else if (path === '/api/workspace') {
      await route.fulfill({ json: state })
    } else {
      await route.fulfill({ status: 404, json: { error: 'Unknown test endpoint' } })
    }
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  return { account, state, actions }
}

async function openKitchen(page: Page) {
  await page.getByRole('navigation').getByRole('button', { name: 'Kitchen', exact: true }).click()
  await expect(page.locator('.workspace-submit')).toBeEnabled()
}

test('everyday Home actions are visible above the navigation and lead to the right form', async ({ page }) => {
  await mockKitchen(page)
  const nav = await page.getByRole('navigation').boundingBox()
  for (const card of await page.locator('.quick-card').all()) {
    const box = await card.boundingBox()
    expect(box!.y + box!.height).toBeLessThan(nav!.y)
  }
  await page.getByRole('button', { name: 'Scan a receipt Add your groceries' }).click()
  await expect(page.locator('input[type="file"]')).toBeVisible()
  await expect(page.locator('.workspace-submit')).toHaveText('Scan a receipt')
})

test('visible actions, additional actions, and category changes have correct selected states', async ({ page }) => {
  await mockKitchen(page)
  await openKitchen(page)
  const picker = page.getByRole('group', { name: 'What would you like to do?' })
  await expect(picker.getByRole('button', { name: 'Open pantry', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'More actions', exact: true }).click()
  await picker.getByRole('button', { name: 'Snooze reminder' }).click()
  await expect(page.getByLabel('Food ID', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Days', { exact: true })).toBeVisible()
  await expect(page.locator('.workspace-submit')).toHaveText('Snooze reminder')
  await page.getByRole('group', { name: 'Kitchen categories' }).getByRole('button', { name: 'Meals', exact: true }).click()
  await expect(picker.getByRole('button', { name: 'Cook from pantry', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'More actions', exact: true })).toHaveAttribute('aria-expanded', 'false')
})

test('editing a confirmed mutation requires fresh confirmation and submits the updated value once', async ({ page }) => {
  const { actions } = await mockKitchen(page)
  await openKitchen(page)
  await page.getByRole('button', { name: 'More actions', exact: true }).click()
  await page.getByRole('group', { name: 'What would you like to do?' }).getByRole('button', { name: 'Mark eaten' }).click()
  await page.getByLabel('Food ID', { exact: true }).fill('12')
  await page.locator('.workspace-submit').click()
  await expect(page.getByText('Confirm this change?')).toBeVisible()
  expect(actions).toHaveLength(0)
  await page.getByLabel('Food ID', { exact: true }).fill('13')
  await expect(page.getByText('Confirm this change?')).not.toBeVisible()
  await page.locator('.workspace-submit').click()
  await page.locator('.workspace-submit').click()
  await expect(page.getByText('Result for ate: food #12')).toBeVisible()
  expect(actions).toHaveLength(1)
  expect(actions[0]).toMatchObject({ kind: 'command', command: 'ate', text: '13' })
  await expect(page.getByRole('region', { name: 'Your activity' })).toBeFocused()
})

test('food ID lookup preserves the correction draft and earlier results remain accessible', async ({ page }) => {
  await mockKitchen(page)
  await openKitchen(page)
  await page.getByRole('button', { name: 'More actions', exact: true }).click()
  await page.getByRole('group', { name: 'What would you like to do?' }).getByRole('button', { name: 'Correct food details' }).click()
  await page.getByRole('textbox', { name: 'Details', exact: true }).fill('Expiry should be Friday')
  await page.getByRole('button', { name: 'Find a food ID' }).click()
  await expect(page.getByRole('textbox', { name: 'Details', exact: true })).toHaveValue('Expiry should be Friday')
  await page.getByLabel('Food ID', { exact: true }).fill('12')
  await page.locator('.workspace-submit').click()
  await expect(page.getByText('Result for correct: food #12')).toBeVisible()
  await expect(page.getByText('Result for pantry: food #12')).not.toBeVisible()
  await page.locator('.workspace-history summary').click()
  await expect(page.getByText('Result for pantry: food #12')).toBeVisible()
})

test('reselecting the current action or category preserves the form draft', async ({ page }) => {
  await mockKitchen(page)
  await openKitchen(page)
  const picker = page.getByRole('group', { name: 'What would you like to do?' })
  await picker.getByRole('button', { name: 'Add food', exact: true }).click()
  const draft = page.locator('.workspace-fields textarea')
  await draft.fill('Milk, 2 bottles')
  await picker.getByRole('button', { name: 'Add food', exact: true }).click()
  await page.getByRole('group', { name: 'Kitchen categories' }).getByRole('button', { name: 'Pantry', exact: true }).click()
  await expect(draft).toHaveValue('Milk, 2 bottles')
})

test('a correction reply draft survives a newer result moving its card into history', async ({ page }) => {
  const { state } = await mockKitchen(page)
  state.cards.push({ id: 1, text: 'Correct these details', buttons: [], reply: true, document: null })
  await openKitchen(page)
  await page.getByRole('textbox', { name: 'Describe the correction' }).fill('Keep this correction')
  await page.locator('.workspace-submit').click()
  await expect(page.getByText('Result for pantry: food #12')).toBeVisible()
  await page.locator('.workspace-history summary').click()
  await expect(page.getByRole('textbox', { name: 'Describe the correction' })).toHaveValue('Keep this correction')
})

test('busy jobs disable action controls and completion moves focus to activity', async ({ page }) => {
  const { state } = await mockKitchen(page)
  await openKitchen(page)
  await page.route('**/api/workspace/actions', async route => {
    state.busy = true
    await route.fulfill({ json: state })
  })
  await page.locator('.workspace-submit').click()
  await expect(page.locator('.workspace-submit')).toBeDisabled()
  await expect(page.getByRole('group', { name: 'What would you like to do?' }).getByRole('button', { name: 'Scan a receipt', exact: true })).toBeDisabled()
  state.cards.push({ id: 1, text: 'Finished pantry update', buttons: [], reply: false, document: null })
  state.busy = false
  await expect(page.getByText('Finished pantry update')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Your activity' })).toBeFocused()
  await expect(page.locator('.workspace-submit')).toBeEnabled()
})

test('invalid receipt files are rejected beside the form without sending an upload', async ({ page }) => {
  await mockKitchen(page)
  await openKitchen(page)
  await page.getByRole('group', { name: 'What would you like to do?' }).getByRole('button', { name: 'Scan a receipt', exact: true }).click()
  let uploads = 0
  page.on('request', request => { if (request.url().endsWith('/api/workspace/photo')) uploads++ })
  await page.locator('input[type="file"]').setInputFiles({ name: 'receipt.txt', mimeType: 'text/plain', buffer: Buffer.from('Not an image') })
  await page.locator('.workspace-submit').click()
  await expect(page.getByRole('alert')).toContainText('Choose a JPEG or PNG photo')
  expect(uploads).toBe(0)
})

test('account edits can be discarded and fields are locked until a save finishes', async ({ page }) => {
  await mockKitchen(page)
  await page.getByRole('button', { name: 'Account · Alex Chen' }).click()
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  await page.getByLabel('Household name').fill('New kitchen')
  await expect(page.getByText('You have unsaved changes.')).toBeVisible()
  await page.getByRole('button', { name: 'Discard changes' }).click()
  await expect(page.getByLabel('Household name')).toHaveValue('The Chen kitchen')
  await page.getByLabel('Household name').fill('New kitchen')
  let releaseSave!: () => void
  const saveGate = new Promise<void>(resolve => { releaseSave = resolve })
  await page.route('**/api/account', async route => {
    if (route.request().method() === 'PATCH') {
      await saveGate
      await route.fulfill({ json: {} })
    } else await route.fallback()
  })
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByLabel('Household name')).toBeDisabled()
  await expect(page.getByRole('combobox', { name: 'Language', exact: true })).toBeDisabled()
  releaseSave()
  await expect(page.getByText('Changes saved')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  await expect(page.getByLabel('Household name')).toHaveValue('New kitchen')
})

test('failed saves keep the draft and announce the error', async ({ page }) => {
  await mockKitchen(page)
  await page.getByRole('navigation').getByRole('button', { name: 'Account', exact: true }).click()
  await page.getByLabel('Household name').fill('Keep this draft')
  await page.route('**/api/account', route => route.request().method() === 'PATCH' ? route.fulfill({ status: 500, json: {} }) : route.fallback())
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByRole('alert')).toContainText('Could not save')
  await expect(page.getByLabel('Household name')).toHaveValue('Keep this draft')
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeEnabled()
})

test('payment availability disables purchases while an existing Family plan can still be managed', async ({ page }) => {
  await mockKitchen(page, { billing: false, tier: 'family' })
  await page.getByRole('navigation').getByRole('button', { name: 'Plans', exact: true }).click()
  await expect(page.getByRole('button', { name: '+50 receipts' })).toBeDisabled()
  await page.getByRole('button', { name: 'Manage plan' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
})

test('local mode hides hosted features and member mode hides owner actions', async ({ page }) => {
  await mockKitchen(page, { hosted: false, role: 'member' })
  await expect(page.getByRole('navigation').getByRole('button', { name: 'Plans', exact: true })).not.toBeVisible()
  await openKitchen(page)
  await expect(page.getByRole('group', { name: 'Kitchen categories' }).getByRole('button', { name: 'Household', exact: true })).not.toBeVisible()
  await page.getByRole('navigation').getByRole('button', { name: 'Account', exact: true }).click()
  await expect(page.getByLabel('Household name')).toBeDisabled()
})

test('text, primary buttons, and input boundaries maintain contrast in both themes', async ({ page }) => {
  await mockKitchen(page)
  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme })
    const contrasts = await page.evaluate(() => {
      const styles = getComputedStyle(document.documentElement)
      function luminance(token: string) {
        const hex = styles.getPropertyValue(token).trim().slice(1)
        const channels = [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
          .map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4)
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
      }
      return [
        ['--ink', '--bg', 4.5], ['--ink-soft', '--surface', 4.5],
        ['--muted', '--bg', 4.5], ['--muted', '--surface', 4.5],
        ['--green-dark', '--surface-soft', 4.5], ['--green-on', '--green', 4.5],
        ['--line-strong', '--surface-raised', 3],
      ].map(([foreground, background, minimum]) => {
        const a = luminance(foreground as string), b = luminance(background as string)
        return { foreground, background, minimum: minimum as number, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) }
      })
    })
    for (const contrast of contrasts) expect(contrast.ratio, `${colorScheme}: ${contrast.foreground}/${contrast.background}`).toBeGreaterThanOrEqual(contrast.minimum)
  }
})

for (const language of ['en', 'zh', 'fr', 'es']) {
  test(`responsive ${language} screens have no horizontal overflow in light and dark themes`, async ({ page }) => {
    await mockKitchen(page, { language })
    for (const colorScheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' })
      for (const viewport of [{ width: 375, height: 812 }, { width: 844, height: 390 }, { width: 1280, height: 900 }]) {
        await page.setViewportSize(viewport)
        for (const tab of ['home', 'kitchen', 'plans', 'account']) {
          await page.locator(`.bottom-nav button`).nth(['home', 'kitchen', 'plans', 'account'].indexOf(tab)).click()
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
          const buttons = page.locator('.bottom-nav button')
          for (const button of await buttons.all()) {
            const box = await button.boundingBox()
            expect(box!.height).toBeGreaterThanOrEqual(44)
            expect(box!.width).toBeGreaterThanOrEqual(44)
          }
        }
      }
    }
  })
}

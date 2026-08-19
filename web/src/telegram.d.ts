interface TelegramWebApp {
  initData: string
  initDataUnsafe?: {
    user?: { language_code?: string }
  }
  ready(): void
  expand(): void
  close(): void
  openTelegramLink(url: string): void
  openInvoice(url: string, callback?: (status: string) => void): void
}

interface Window {
  Telegram?: { WebApp: TelegramWebApp }
}

export const supportedLocales = ['en', 'zh', 'fr', 'es'] as const

export type Locale = (typeof supportedLocales)[number]
export type TranslationValues = Record<string, string | number>

const localeTags: Record<Locale, string> = {
  en: 'en-US',
  zh: 'zh-CN',
  fr: 'fr-FR',
  es: 'es-ES',
}

export const languageNames: Record<Locale, string> = {
  en: 'English',
  zh: '中文',
  fr: 'Français',
  es: 'Español',
}

const english = {
  'brand.name': 'Food Manager',
  'nav.primary': 'Primary navigation',
  'nav.home': 'Home',
  'nav.plans': 'Plans',
  'nav.account': 'Account',
  'error.openInTelegram.title': 'Open Food Manager in Telegram',
  'error.loadAccount': 'We could not load your account. Please try again.',
  'error.tryAgain': 'Try again',
  'error.botUnavailable': 'The bot link is unavailable right now.',
  'error.checkout': 'Could not start checkout. Please try again.',
  'error.cancel': 'Could not cancel renewal. Please try again.',
  'error.save': 'Could not save your changes. Please try again.',
  'loading.preparing': 'Preparing your household…',
  'common.close': 'Close',
  'home.greeting.morning': 'Good morning, {name}',
  'home.greeting.afternoon': 'Good afternoon, {name}',
  'home.greeting.evening': 'Good evening, {name}',
  'home.running': 'Your household is running smoothly.',
  'plan.free': 'Free',
  'plan.family': 'Family',
  'plan.freePlan': 'Free plan',
  'plan.familyPlan': 'Family plan',
  'home.viewPlans': 'View plans',
  'usage.receipts': 'Receipts',
  'usage.actions': 'AI actions',
  'usage.usedOf': '{used} of {limit} used',
  'usage.ariaUsedOf': '{label}: {used} of {limit} used',
  'usage.resets': 'Resets {date}',
  'home.myHousehold': 'My household',
  'home.manage': 'Manage',
  'home.quickAccess': 'Quick access',
  'shortcut.pantry': 'Pantry',
  'shortcut.cook': 'Cook from pantry',
  'shortcut.plan': 'Meal plan',
  'shortcut.shopping': 'Shopping list',
  'shortcut.favorites': 'Saved recipes',
  'shortcut.preferences': 'Food preferences',
  'shortcut.stats': 'Pantry stats',
  'count.receipt.one': '{count} receipt',
  'count.receipt.many': '{count} receipts',
  'count.action.one': '{count} AI action',
  'count.action.many': '{count} AI actions',
  'count.member.one': '{count} household member',
  'count.member.many': '{count} household members',
  'count.seat.one': '{count} seat',
  'count.seat.many': '{count} seats',
  'plan.choose': 'Choose your plan',
  'plan.subtitle': 'Simple household plans, paid securely with Telegram Stars.',
  'plan.billingCycle': '30-day billing · cancel anytime',
  'plan.current': 'Current plan',
  'plan.included': 'Included',
  'plan.manage': 'Manage plan',
  'plan.upgrade': 'Upgrade to Family',
  'plan.freePrice': '0 Stars',
  'plan.familyPrice': '{stars} Stars / 30 days',
  'plan.stars': '{count} Stars',
  'plan.needMore': 'Need a little more?',
  'plan.topupReceipts': '+{count} receipts',
  'plan.topupActions': '+{count} AI actions',
  'plan.paymentsUnavailable': 'Payments are currently unavailable.',
  'plan.paymentNote': 'Payments are processed by Telegram Stars.',
  'account.title': 'Account',
  'account.subtitle': 'Your preferences follow you across the bot.',
  'account.owner': 'Household owner',
  'account.member': 'Household member',
  'account.household': 'Household',
  'account.householdName': 'Household name',
  'account.seatsUsed': '{used} of {limit} seats used',
  'account.dailyDigest': 'Daily digest',
  'account.deliveryTime': 'Delivery time',
  'account.timeZone': 'Time zone',
  'account.preferences': 'Preferences',
  'account.language': 'Language',
  'account.provider': 'AI provider',
  'account.save': 'Save changes',
  'account.openChat': 'Open bot chat',
  'account.subscription': 'Subscription',
  'account.saving': 'Saving…',
  'account.saved': 'Changes saved',
  'manage.title': 'Manage Family plan',
  'manage.activeUntil': 'Active until {date}',
  'manage.renews': 'Renews automatically in Telegram',
  'manage.cancelled': 'Renewal is cancelled',
  'manage.keep': 'Keep plan',
  'manage.cancel': 'Cancel renewal',
} as const

export type MessageKey = keyof typeof english

const messages: Record<Locale, Record<MessageKey, string>> = {
  en: english,
  zh: {
    'brand.name': 'Food Manager',
    'nav.primary': '主导航',
    'nav.home': '首页',
    'nav.plans': '套餐',
    'nav.account': '账户',
    'error.openInTelegram.title': '请在 Telegram 中打开 Food Manager',
    'error.loadAccount': '无法加载您的账户，请重试。',
    'error.tryAgain': '重试',
    'error.botUnavailable': '机器人链接暂时不可用。',
    'error.checkout': '暂时无法发起结账，请重试。',
    'error.cancel': '暂时无法取消续订，请重试。',
    'error.save': '无法保存您的更改，请重试。',
    'loading.preparing': '正在准备您的家庭…',
    'common.close': '关闭',
    'home.greeting.morning': '早上好，{name}',
    'home.greeting.afternoon': '下午好，{name}',
    'home.greeting.evening': '晚上好，{name}',
    'home.running': '您的家庭运转顺利。',
    'plan.free': '免费版',
    'plan.family': '家庭版',
    'plan.freePlan': '免费套餐',
    'plan.familyPlan': '家庭套餐',
    'home.viewPlans': '查看套餐',
    'usage.receipts': '收据',
    'usage.actions': 'AI 操作',
    'usage.usedOf': '已使用 {used}/{limit}',
    'usage.ariaUsedOf': '{label}：已使用 {used}/{limit}',
    'usage.resets': '{date} 重置',
    'home.myHousehold': '我的家庭',
    'home.manage': '管理',
    'home.quickAccess': '快捷入口',
    'shortcut.pantry': '食品储藏',
    'shortcut.cook': '用储藏食材做菜',
    'shortcut.plan': '用餐计划',
    'shortcut.shopping': '购物清单',
    'shortcut.favorites': '已保存的菜谱',
    'shortcut.preferences': '饮食偏好',
    'shortcut.stats': '储藏统计',
    'count.receipt.one': '{count} 张收据',
    'count.receipt.many': '{count} 张收据',
    'count.action.one': '{count} 次 AI 操作',
    'count.action.many': '{count} 次 AI 操作',
    'count.member.one': '{count} 位家庭成员',
    'count.member.many': '{count} 位家庭成员',
    'count.seat.one': '{count} 个席位',
    'count.seat.many': '{count} 个席位',
    'plan.choose': '选择您的套餐',
    'plan.subtitle': '简单的家庭套餐，使用 Telegram Stars 安全付款。',
    'plan.billingCycle': '30 天计费 · 随时取消',
    'plan.current': '当前套餐',
    'plan.included': '已包含',
    'plan.manage': '管理套餐',
    'plan.upgrade': '升级到家庭套餐',
    'plan.freePrice': '0 Stars',
    'plan.familyPrice': '{stars} Stars / 30 天',
    'plan.stars': '{count} Stars',
    'plan.needMore': '还需要一些吗？',
    'plan.topupReceipts': '+{count} 张收据',
    'plan.topupActions': '+{count} 次 AI 操作',
    'plan.paymentsUnavailable': '付款暂时不可用。',
    'plan.paymentNote': '付款由 Telegram Stars 处理。',
    'account.title': '账户',
    'account.subtitle': '您的偏好会在机器人中保持同步。',
    'account.owner': '家庭所有者',
    'account.member': '家庭成员',
    'account.household': '家庭',
    'account.householdName': '家庭名称',
    'account.seatsUsed': '已使用 {used}/{limit} 个席位',
    'account.dailyDigest': '每日摘要',
    'account.deliveryTime': '发送时间',
    'account.timeZone': '时区',
    'account.preferences': '偏好设置',
    'account.language': '语言',
    'account.provider': 'AI 提供商',
    'account.save': '保存更改',
    'account.openChat': '打开机器人聊天',
    'account.subscription': '订阅',
    'account.saving': '正在保存…',
    'account.saved': '更改已保存',
    'manage.title': '管理家庭套餐',
    'manage.activeUntil': '有效期至 {date}',
    'manage.renews': '将在 Telegram 中自动续订',
    'manage.cancelled': '续订已取消',
    'manage.keep': '保留套餐',
    'manage.cancel': '取消续订',
  },
  fr: {
    'brand.name': 'Food Manager',
    'nav.primary': 'Navigation principale',
    'nav.home': 'Accueil',
    'nav.plans': 'Forfaits',
    'nav.account': 'Compte',
    'error.openInTelegram.title': 'Ouvrez Food Manager dans Telegram',
    'error.loadAccount': 'Impossible de charger votre compte. Réessayez.',
    'error.tryAgain': 'Réessayer',
    'error.botUnavailable': 'Le lien vers le bot est indisponible pour le moment.',
    'error.checkout': 'Impossible de lancer le paiement. Réessayez.',
    'error.cancel': 'Impossible d’annuler le renouvellement. Réessayez.',
    'error.save': 'Impossible d’enregistrer vos modifications. Réessayez.',
    'loading.preparing': 'Préparation de votre foyer…',
    'common.close': 'Fermer',
    'home.greeting.morning': 'Bonjour, {name}',
    'home.greeting.afternoon': 'Bon après-midi, {name}',
    'home.greeting.evening': 'Bonsoir, {name}',
    'home.running': 'Tout roule pour votre foyer.',
    'plan.free': 'Gratuit',
    'plan.family': 'Famille',
    'plan.freePlan': 'Forfait gratuit',
    'plan.familyPlan': 'Forfait Famille',
    'home.viewPlans': 'Voir les forfaits',
    'usage.receipts': 'Tickets',
    'usage.actions': 'Actions IA',
    'usage.usedOf': '{used} sur {limit} utilisés',
    'usage.ariaUsedOf': '{label} : {used} sur {limit} utilisés',
    'usage.resets': 'Réinitialisation le {date}',
    'home.myHousehold': 'Mon foyer',
    'home.manage': 'Gérer',
    'home.quickAccess': 'Accès rapide',
    'shortcut.pantry': 'Garde-manger',
    'shortcut.cook': 'Cuisiner avec le garde-manger',
    'shortcut.plan': 'Plan de repas',
    'shortcut.shopping': 'Liste de courses',
    'shortcut.favorites': 'Recettes sauvegardées',
    'shortcut.preferences': 'Préférences alimentaires',
    'shortcut.stats': 'Statistiques du garde-manger',
    'count.receipt.one': '{count} ticket',
    'count.receipt.many': '{count} tickets',
    'count.action.one': '{count} action IA',
    'count.action.many': '{count} actions IA',
    'count.member.one': '{count} membre du foyer',
    'count.member.many': '{count} membres du foyer',
    'count.seat.one': '{count} place',
    'count.seat.many': '{count} places',
    'plan.choose': 'Choisissez votre forfait',
    'plan.subtitle': 'Des forfaits simples pour le foyer, payés en toute sécurité avec Telegram Stars.',
    'plan.billingCycle': 'Facturation sur 30 jours · annulez à tout moment',
    'plan.current': 'Forfait actuel',
    'plan.included': 'Inclus',
    'plan.manage': 'Gérer le forfait',
    'plan.upgrade': 'Passer à Famille',
    'plan.freePrice': '0 Stars',
    'plan.familyPrice': '{stars} Stars / 30 jours',
    'plan.stars': '{count} Stars',
    'plan.needMore': 'Besoin d’un peu plus ?',
    'plan.topupReceipts': '+{count} tickets',
    'plan.topupActions': '+{count} actions IA',
    'plan.paymentsUnavailable': 'Les paiements sont indisponibles pour le moment.',
    'plan.paymentNote': 'Les paiements sont traités par Telegram Stars.',
    'account.title': 'Compte',
    'account.subtitle': 'Vos préférences vous suivent dans le bot.',
    'account.owner': 'Propriétaire du foyer',
    'account.member': 'Membre du foyer',
    'account.household': 'Foyer',
    'account.householdName': 'Nom du foyer',
    'account.seatsUsed': '{used} places utilisées sur {limit}',
    'account.dailyDigest': 'Résumé quotidien',
    'account.deliveryTime': 'Heure d’envoi',
    'account.timeZone': 'Fuseau horaire',
    'account.preferences': 'Préférences',
    'account.language': 'Langue',
    'account.provider': 'Fournisseur IA',
    'account.save': 'Enregistrer les modifications',
    'account.openChat': 'Ouvrir le chat du bot',
    'account.subscription': 'Abonnement',
    'account.saving': 'Enregistrement…',
    'account.saved': 'Modifications enregistrées',
    'manage.title': 'Gérer le forfait Famille',
    'manage.activeUntil': 'Actif jusqu’au {date}',
    'manage.renews': 'Renouvellement automatique dans Telegram',
    'manage.cancelled': 'Le renouvellement est annulé',
    'manage.keep': 'Conserver le forfait',
    'manage.cancel': 'Annuler le renouvellement',
  },
  es: {
    'brand.name': 'Food Manager',
    'nav.primary': 'Navegación principal',
    'nav.home': 'Inicio',
    'nav.plans': 'Planes',
    'nav.account': 'Cuenta',
    'error.openInTelegram.title': 'Abre Food Manager en Telegram',
    'error.loadAccount': 'No pudimos cargar tu cuenta. Inténtalo de nuevo.',
    'error.tryAgain': 'Intentar de nuevo',
    'error.botUnavailable': 'El enlace al bot no está disponible ahora.',
    'error.checkout': 'No se pudo iniciar el pago. Inténtalo de nuevo.',
    'error.cancel': 'No se pudo cancelar la renovación. Inténtalo de nuevo.',
    'error.save': 'No se pudieron guardar los cambios. Inténtalo de nuevo.',
    'loading.preparing': 'Preparando tu hogar…',
    'common.close': 'Cerrar',
    'home.greeting.morning': 'Buenos días, {name}',
    'home.greeting.afternoon': 'Buenas tardes, {name}',
    'home.greeting.evening': 'Buenas noches, {name}',
    'home.running': 'Todo va bien en tu hogar.',
    'plan.free': 'Gratis',
    'plan.family': 'Familiar',
    'plan.freePlan': 'Plan gratuito',
    'plan.familyPlan': 'Plan Familiar',
    'home.viewPlans': 'Ver planes',
    'usage.receipts': 'Recibos',
    'usage.actions': 'Acciones de IA',
    'usage.usedOf': '{used} de {limit} usados',
    'usage.ariaUsedOf': '{label}: {used} de {limit} usados',
    'usage.resets': 'Se reinicia el {date}',
    'home.myHousehold': 'Mi hogar',
    'home.manage': 'Administrar',
    'home.quickAccess': 'Acceso rápido',
    'shortcut.pantry': 'Despensa',
    'shortcut.cook': 'Cocinar con la despensa',
    'shortcut.plan': 'Plan de comidas',
    'shortcut.shopping': 'Lista de compras',
    'shortcut.favorites': 'Recetas guardadas',
    'shortcut.preferences': 'Preferencias de comida',
    'shortcut.stats': 'Estadísticas de la despensa',
    'count.receipt.one': '{count} recibo',
    'count.receipt.many': '{count} recibos',
    'count.action.one': '{count} acción de IA',
    'count.action.many': '{count} acciones de IA',
    'count.member.one': '{count} miembro del hogar',
    'count.member.many': '{count} miembros del hogar',
    'count.seat.one': '{count} plaza',
    'count.seat.many': '{count} plazas',
    'plan.choose': 'Elige tu plan',
    'plan.subtitle': 'Planes sencillos para el hogar, pagados de forma segura con Telegram Stars.',
    'plan.billingCycle': 'Facturación de 30 días · cancela cuando quieras',
    'plan.current': 'Plan actual',
    'plan.included': 'Incluido',
    'plan.manage': 'Administrar plan',
    'plan.upgrade': 'Mejorar a Familiar',
    'plan.freePrice': '0 Stars',
    'plan.familyPrice': '{stars} Stars / 30 días',
    'plan.stars': '{count} Stars',
    'plan.needMore': '¿Necesitas un poco más?',
    'plan.topupReceipts': '+{count} recibos',
    'plan.topupActions': '+{count} acciones de IA',
    'plan.paymentsUnavailable': 'Los pagos no están disponibles ahora.',
    'plan.paymentNote': 'Los pagos se procesan con Telegram Stars.',
    'account.title': 'Cuenta',
    'account.subtitle': 'Tus preferencias te acompañan en el bot.',
    'account.owner': 'Propietario del hogar',
    'account.member': 'Miembro del hogar',
    'account.household': 'Hogar',
    'account.householdName': 'Nombre del hogar',
    'account.seatsUsed': '{used} de {limit} plazas usadas',
    'account.dailyDigest': 'Resumen diario',
    'account.deliveryTime': 'Hora de envío',
    'account.timeZone': 'Zona horaria',
    'account.preferences': 'Preferencias',
    'account.language': 'Idioma',
    'account.provider': 'Proveedor de IA',
    'account.save': 'Guardar cambios',
    'account.openChat': 'Abrir chat del bot',
    'account.subscription': 'Suscripción',
    'account.saving': 'Guardando…',
    'account.saved': 'Cambios guardados',
    'manage.title': 'Administrar plan Familiar',
    'manage.activeUntil': 'Activo hasta el {date}',
    'manage.renews': 'Se renueva automáticamente en Telegram',
    'manage.cancelled': 'La renovación está cancelada',
    'manage.keep': 'Mantener plan',
    'manage.cancel': 'Cancelar renovación',
  },
}

export function resolveLocale(value: string | undefined): Locale {
  const language = value?.toLowerCase().split(/[-_]/)[0]
  return supportedLocales.includes(language as Locale) ? (language as Locale) : 'en'
}

export function detectLocale(): Locale {
  return resolveLocale(
    window.Telegram?.WebApp.initDataUnsafe?.user?.language_code ?? navigator.language,
  )
}

export function t(
  locale: Locale,
  key: MessageKey,
  values: TranslationValues = {},
): string {
  return messages[locale][key].replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = values[name]
    return value === undefined ? match : String(value)
  })
}

export function formatNumber(locale: Locale, value: number): string {
  return new Intl.NumberFormat(localeTags[locale]).format(value)
}

export function formatShortDate(
  locale: Locale,
  value: Date,
  timeZone?: string,
): string {
  const options: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
  }
  try {
    return new Intl.DateTimeFormat(localeTags[locale], {
      ...options,
      ...(timeZone ? { timeZone } : {}),
    }).format(value)
  } catch {
    return new Intl.DateTimeFormat(localeTags[locale], options).format(value)
  }
}

export function formatDigestHour(locale: Locale, hour: number): string {
  return new Intl.DateTimeFormat(localeTags[locale], {
    hour: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(2026, 0, 1, hour)))
}

export function hourInTimeZone(timeZone: string): number {
  try {
    const hour = new Intl.DateTimeFormat('en-US', {
      hour: 'numeric',
      hourCycle: 'h23',
      timeZone,
    })
      .formatToParts(new Date())
      .find(part => part.type === 'hour')?.value
    return hour ? Number(hour) : new Date().getHours()
  } catch {
    return new Date().getHours()
  }
}

export function greetingKey(hour: number): MessageKey {
  if (hour >= 17) return 'home.greeting.evening'
  if (hour >= 12) return 'home.greeting.afternoon'
  return 'home.greeting.morning'
}

export function countLabel(
  locale: Locale,
  count: number,
  singular: MessageKey,
  plural: MessageKey,
): string {
  return t(locale, count === 1 ? singular : plural, {
    count: formatNumber(locale, count),
  })
}

export function planTitle(
  locale: Locale,
  code: string,
  fallback: string,
): string {
  if (code === 'free') return t(locale, 'plan.free')
  if (code === 'family_monthly') return t(locale, 'plan.family')
  if (code === 'topup_receipts_50') {
    return t(locale, 'plan.topupReceipts', {
      count: formatNumber(locale, 50),
    })
  }
  if (code === 'topup_actions_150') {
    return t(locale, 'plan.topupActions', {
      count: formatNumber(locale, 150),
    })
  }
  return fallback
}

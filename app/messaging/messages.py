"""Static messaging onboarding strings, included in the shared i18n catalog."""

MESSAGES = {
    "messaging.choose_action": {
        "en": "Choose an action",
        "zh": "选择操作",
        "fr": "Choisissez une action",
        "es": "Elige una acción",
    },
    "messaging.actions": {
        "en": "Actions",
        "zh": "操作",
        "fr": "Actions",
        "es": "Acciones",
    },
    "messaging.help": {
        "en": "Send a receipt photo, or use pantry, add, cook, plan, shopping, favorites, prefs, lang, tz, digest_at, llm, quota.\nEnable daily reminders with reminders on.\nHousehold: invite, join CODE, household, leave, remove ID, bind.\nConnect another account: link, link CODE on the new connection, then confirm_link CODE here.",
        "zh": "发送购物小票照片，或使用 pantry、add、cook、plan、shopping、favorites、prefs、lang、tz、digest_at、llm、quota。\n发送 reminders on 开启每日提醒。\n家庭管理：invite、join CODE、household、leave、remove ID、bind。\n关联账号：先发送 link，在新连接中发送 link CODE，再回到这里发送 confirm_link CODE。",
        "fr": "Envoyez une photo de ticket, ou utilisez pantry, add, cook, plan, shopping, favorites, prefs, lang, tz, digest_at, llm, quota.\nActivez les rappels avec reminders on.\nFoyer : invite, join CODE, household, leave, remove ID, bind.\nPour lier un compte : link, puis link CODE sur la nouvelle connexion, et confirm_link CODE ici.",
        "es": "Envía una foto del recibo, o usa pantry, add, cook, plan, shopping, favorites, prefs, lang, tz, digest_at, llm, quota.\nActiva los recordatorios con reminders on.\nHogar: invite, join CODE, household, leave, remove ID, bind.\nPara vincular otra cuenta: link, luego link CODE en la nueva conexión y confirm_link CODE aquí.",
    },
    "messaging.link_pending": {
        "en": "Identity recorded. Return to your original connection and send confirm_link followed by the same code.",
        "zh": "已记录待关联身份。请回到原连接，发送 confirm_link 加上相同的验证码。",
        "fr": "Identité enregistrée. Revenez sur la connexion d'origine et envoyez confirm_link suivi du même code.",
        "es": "Identidad registrada. Vuelve a la conexión original y envía confirm_link seguido del mismo código.",
    },
    "messaging.joined": {
        "en": "Joined the household. Send pantry to see its food.",
        "zh": "已加入家庭。发送 pantry 查看食材。",
        "fr": "Vous avez rejoint le foyer. Envoyez pantry pour voir les aliments.",
        "es": "Te has unido al hogar. Envía pantry para ver los alimentos.",
    },
    "messaging.reminders_on": {
        "en": "Daily reminders enabled here.",
        "zh": "已在此连接开启每日提醒。",
        "fr": "Rappels quotidiens activés ici.",
        "es": "Recordatorios diarios activados aquí.",
    },
    "messaging.reminders_off": {
        "en": "Daily reminders disabled here.",
        "zh": "已关闭此连接的每日提醒。",
        "fr": "Rappels quotidiens désactivés ici.",
        "es": "Recordatorios diarios desactivados aquí.",
    },
    "messaging.link_code": {
        "en": "On the other connection send: link {token}\nThen confirm here: confirm_link {token}\nExpires in 10 minutes. Do not share this code.",
        "zh": "在另一连接发送：link {token}\n再回到这里确认：confirm_link {token}\n10 分钟后失效。请勿分享此验证码。",
        "fr": "Sur l'autre connexion, envoyez : link {token}\nPuis confirmez ici : confirm_link {token}\nExpire dans 10 minutes. Ne partagez pas ce code.",
        "es": "En la otra conexión envía: link {token}\nLuego confirma aquí: confirm_link {token}\nCaduca en 10 minutos. No compartas este código.",
    },
    "messaging.linked": {
        "en": "Accounts linked. Each connection now uses the same household and preferences.",
        "zh": "账号已关联。各连接现在使用同一家庭和偏好设置。",
        "fr": "Comptes liés. Les connexions utilisent maintenant le même foyer et les mêmes préférences.",
        "es": "Cuentas vinculadas. Las conexiones usan el mismo hogar y las mismas preferencias.",
    },
    "messaging.settings": {
        "en": "Language: {language}; timezone: {tz}; reminder hour: {hour}:00.",
        "zh": "语言：{language}；时区：{tz}；提醒时间：{hour}:00。",
        "fr": "Langue : {language} ; fuseau : {tz} ; rappel à {hour}:00.",
        "es": "Idioma: {language}; zona horaria: {tz}; recordatorio a las {hour}:00.",
    },
    "messaging.payments": {
        "en": "Payments are available through the deployment's Telegram connection. Link your account before purchasing there.",
        "zh": "付款可通过此部署的 Telegram 连接完成。购买前请先关联账号。",
        "fr": "Les paiements sont disponibles sur la connexion Telegram du déploiement. Liez votre compte avant d'y acheter.",
        "es": "Los pagos están disponibles en la conexión de Telegram del despliegue. Vincula tu cuenta antes de comprar allí.",
    },
    "messaging.quota": {
        "en": "Household plan: {tier}. Period ends: {end}.\nReceipts: {receipts}/{receipt_limit}; actions: {actions}/{action_limit}.",
        "zh": "家庭套餐：{tier}。周期结束日期：{end}。\n小票：{receipts}/{receipt_limit}；操作：{actions}/{action_limit}。",
        "fr": "Offre du foyer : {tier}. Fin de période : {end}.\nTickets : {receipts}/{receipt_limit} ; actions : {actions}/{action_limit}.",
        "es": "Plan del hogar: {tier}. Fin del período: {end}.\nRecibos: {receipts}/{receipt_limit}; acciones: {actions}/{action_limit}.",
    },
    "messaging.invite": {
        "en": "Invite code: {token}\nSend join {token} in a direct conversation. Expires in 24 hours.",
        "zh": "邀请码：{token}\n在私聊中发送 join {token}。24 小时后失效。",
        "fr": "Code d'invitation : {token}\nEnvoyez join {token} en message direct. Expire dans 24 heures.",
        "es": "Código de invitación: {token}\nEnvía join {token} en un mensaje directo. Caduca en 24 horas.",
    },
    "messaging.left": {
        "en": "You left the household. All linked connections have lost access.",
        "zh": "你已退出家庭。所有关联连接的访问权限均已撤销。",
        "fr": "Vous avez quitté le foyer. Toutes vos connexions liées ont perdu l'accès.",
        "es": "Has salido del hogar. Todas las conexiones vinculadas han perdido el acceso.",
    },
    "messaging.removed": {
        "en": "Member removed from all linked connections.",
        "zh": "已撤销该成员所有关联连接的家庭访问权限。",
        "fr": "Accès du membre révoqué sur toutes ses connexions liées.",
        "es": "Acceso del miembro revocado en todas sus conexiones vinculadas.",
    },
    "messaging.bound": {
        "en": "Conversation bound to your household.",
        "zh": "此会话已绑定到你的家庭。",
        "fr": "Conversation associée à votre foyer.",
        "es": "Conversación asociada a tu hogar.",
    },
}

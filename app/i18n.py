from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

LANGS: tuple[str, ...] = ("en", "zh", "fr", "es")
DEFAULT_LANG = "en"

# Catalog. English is mandatory for every key; other languages are optional and
# fall back to English. Keys are added incrementally by later tasks.
MESSAGES: dict[str, dict[str, str]] = {
    "hosted_only": {
        "en": "This feature is available on the hosted Food Manager bot: https://t.me/foodie_manager_bot",
        "zh": "此功能可在托管版 Food Manager 机器人中使用：https://t.me/foodie_manager_bot",
        "fr": "Cette fonctionnalité est disponible sur le bot Food Manager hébergé : https://t.me/foodie_manager_bot",
        "es": "Esta función está disponible en el bot alojado Food Manager: https://t.me/foodie_manager_bot",
    },
    "quota.title": {
        "en": "📊 {plan} quota - renews in {days}d",
        "zh": "📊 {plan}配额 - {days}天后更新",
        "fr": "📊 Quota {plan} - renouvellement dans {days} j",
        "es": "📊 Cuota {plan} - se renueva en {days} d",
    },
    "quota.plan.free": {"en": "Free", "zh": "免费", "fr": "Gratuit", "es": "Gratis"},
    "quota.plan.family": {
        "en": "Family",
        "zh": "家庭",
        "fr": "Famille",
        "es": "Familiar",
    },
    "quota.receipts": {
        "en": "🧾 Receipts",
        "zh": "🧾 收据",
        "fr": "🧾 Reçus",
        "es": "🧾 Recibos",
    },
    "quota.actions": {
        "en": "⚡ AI actions",
        "zh": "⚡ AI 操作",
        "fr": "⚡ Actions IA",
        "es": "⚡ Acciones de IA",
    },
    "quota.op.cook": {"en": "Cook", "zh": "烹饪", "fr": "Cuisine", "es": "Cocina"},
    "quota.op.plan": {"en": "Plans", "zh": "计划", "fr": "Menus", "es": "Planes"},
    "quota.op.edit": {"en": "Edits", "zh": "编辑", "fr": "Modifications", "es": "Ediciones"},
    "quota.op.chat": {"en": "Chat", "zh": "聊天", "fr": "Discussion", "es": "Chat"},
    "quota.op.search": {"en": "Search", "zh": "搜索", "fr": "Recherche", "es": "Búsqueda"},
    "quota.need_more": {
        "en": "Need more? -> /buy",
        "zh": "需要更多？-> /buy",
        "fr": "Besoin de plus ? -> /buy",
        "es": "¿Necesitas más? -> /buy",
    },
    "quota.multiplier_note": {
        "en": "Provider multipliers are included in the AI actions total.",
        "zh": "AI 操作总数已包含提供商倍率。",
        "fr": "Le total des actions IA inclut les multiplicateurs des fournisseurs.",
        "es": "El total de acciones de IA incluye los multiplicadores del proveedor.",
    },
    "quota.free_upsell": {
        "en": "Family: 100 receipts, 300 AI actions, 10 members.",
        "zh": "家庭版：100 张收据、300 次 AI 操作、10 位成员。",
        "fr": "Famille : 100 reçus, 300 actions IA, 10 membres.",
        "es": "Familiar: 100 recibos, 300 acciones de IA, 10 miembros.",
    },
    "quota.receipts_exhausted": {
        "en": "You've used all {limit} receipts this period. More: /buy",
        "zh": "本周期的 {limit} 张收据配额已用完。需要更多：/buy",
        "fr": "Vous avez utilisé les {limit} reçus de cette période. Plus : /buy",
        "es": "Has usado los {limit} recibos de este período. Más: /buy",
    },
    "quota.degraded.profile": {
        "en": "AI quota used up; your profile was not changed. More: /buy",
        "zh": "AI 配额已用完；您的资料未更改。需要更多：/buy",
        "fr": "Quota IA épuisé ; votre profil n'a pas été modifié. Plus : /buy",
        "es": "Cuota de IA agotada; tu perfil no cambió. Más: /buy",
    },
    "quota.degraded.plan": {
        "en": "AI quota used up - using the basic planner.",
        "zh": "AI 配额已用完——正在使用基础计划器。",
        "fr": "Quota IA épuisé - utilisation du planificateur de base.",
        "es": "Cuota de IA agotada: se usará el planificador básico.",
    },
    "quota.degraded.add": {
        "en": "Added {name} with a basic {days}-day estimate. AI quota used up; more: /buy",
        "zh": "已添加 {name}，使用基础的 {days} 天估算。AI 配额已用完；需要更多：/buy",
        "fr": "{name} ajouté avec une estimation simple de {days} jours. Quota IA épuisé ; plus : /buy",
        "es": "Se añadió {name} con una estimación básica de {days} días. Cuota de IA agotada; más: /buy",
    },
    "quota.degraded.correction": {
        "en": "AI quota used up; correction parsing is unavailable. More: /buy",
        "zh": "AI 配额已用完；无法解析更正。需要更多：/buy",
        "fr": "Quota IA épuisé ; l'analyse des corrections est indisponible. Plus : /buy",
        "es": "Cuota de IA agotada; no se pueden interpretar correcciones. Más: /buy",
    },
    "quota.degraded.cook": {
        "en": "AI quota used up. Your saved recipes still work: /favorites",
        "zh": "AI 配额已用完。已保存的食谱仍可使用：/favorites",
        "fr": "Quota IA épuisé. Vos recettes enregistrées restent disponibles : /favorites",
        "es": "Cuota de IA agotada. Tus recetas guardadas siguen disponibles: /favorites",
    },
    "quota.degraded.more": {
        "en": "AI quota used up; more: /buy",
        "zh": "AI 配额已用完；需要更多：/buy",
        "fr": "Quota IA épuisé ; plus : /buy",
        "es": "Cuota de IA agotada; más: /buy",
    },
    "billing.payments_unavailable": {
        "en": "Payments aren't available right now. Try again later.",
        "zh": "付款暂时不可用。请稍后再试。",
        "fr": "Les paiements sont indisponibles pour le moment. Réessayez plus tard.",
        "es": "Los pagos no están disponibles ahora. Inténtalo más tarde.",
    },
    "billing.buy_choose": {
        "en": "Choose a plan or top-up:",
        "zh": "选择套餐或加购：",
        "fr": "Choisissez un forfait ou une recharge :",
        "es": "Elige un plan o una recarga:",
    },
    "billing.sku.family_monthly": {"en": "Family plan", "zh": "家庭套餐", "fr": "Forfait Famille", "es": "Plan Familiar"},
    "billing.sku.topup_receipts_50": {"en": "+50 receipts", "zh": "+50 张收据", "fr": "+50 reçus", "es": "+50 recibos"},
    "billing.sku.topup_actions_150": {"en": "+150 AI actions", "zh": "+150 次 AI 操作", "fr": "+150 actions IA", "es": "+150 acciones de IA"},
    "billing.purchase_unavailable": {
        "en": "This purchase isn't available. Try /buy again.",
        "zh": "此购买不可用。请重试 /buy。",
        "fr": "Cet achat n'est pas disponible. Réessayez avec /buy.",
        "es": "Esta compra no está disponible. Vuelve a intentar /buy.",
    },
    "billing.subscription_active": {
        "en": "Thanks! Family plan is active. See /quota",
        "zh": "谢谢！家庭套餐已启用。查看 /quota",
        "fr": "Merci ! Le forfait Famille est actif. Voir /quota",
        "es": "¡Gracias! El plan Familiar está activo. Consulta /quota",
    },
    "billing.topup_active": {
        "en": "Thanks! Your top-up has been added. See /quota",
        "zh": "谢谢！加购已添加。查看 /quota",
        "fr": "Merci ! Votre recharge a été ajoutée. Voir /quota",
        "es": "¡Gracias! Se añadió tu recarga. Consulta /quota",
    },
    "billing.plan_free": {
        "en": "Plan: Free. Upgrade with /buy",
        "zh": "套餐：免费。使用 /buy 升级",
        "fr": "Forfait : Gratuit. Mise à niveau avec /buy",
        "es": "Plan: Gratis. Mejora con /buy",
    },
    "billing.plan_family": {
        "en": "Plan: Family. Renews in {days}d. Manage it in Telegram Settings > Stars.",
        "zh": "套餐：家庭。{days} 天后更新。在 Telegram 设置 > Stars 中管理。",
        "fr": "Forfait : Famille. Renouvellement dans {days} j. Gérez-le dans Réglages Telegram > Stars.",
        "es": "Plan: Familiar. Se renueva en {days} d. Adminístralo en Ajustes de Telegram > Stars.",
    },
    "billing.plan_family_cancelled": {
        "en": "Plan: Family. Renewal cancelled; access ends in {days}d.",
        "zh": "套餐：家庭。续订已取消；访问权限将在 {days} 天后结束。",
        "fr": "Forfait : Famille. Renouvellement annulé ; l'accès se termine dans {days} j.",
        "es": "Plan: Familiar. Renovación cancelada; el acceso termina en {days} d.",
    },
    "start.welcome_free": {
        "en": "You're on the free plan: 5 receipts and 30 AI actions a month, up to 2 people. Check usage with /quota, upgrade with /buy.",
        "zh": "你正在使用免费套餐：每月 5 张收据、30 次 AI 操作，最多 2 人。使用 /quota 查看用量，使用 /buy 升级。",
        "fr": "Vous êtes sur le forfait gratuit : 5 reçus et 30 actions IA par mois, jusqu'à 2 personnes. Utilisation avec /quota, mise à niveau avec /buy.",
        "es": "Estás en el plan gratuito: 5 recibos y 30 acciones IA al mes, hasta 2 personas. Consulta el uso con /quota y mejora con /buy.",
    },
    "invite.household_full": {
        "en": "This household is full ({cap} members).",
        "zh": "此家庭已满（{cap} 位成员）。",
        "fr": "Ce foyer est complet ({cap} membres).",
        "es": "Este hogar está lleno ({cap} miembros).",
    },
    "digest.tonight": {
        "en": "🍽 Tonight · {dish}",
        "zh": "🍽 今晚 · {dish}",
        "fr": "🍽 Ce soir · {dish}",
        "es": "🍽 Esta noche · {dish}",
    },
    "digest.section.expired": {"en": "Expired", "zh": "已过期", "fr": "Périmé", "es": "Caducado"},
    "digest.section.today": {"en": "Today", "zh": "今天", "fr": "Aujourd'hui", "es": "Hoy"},
    "digest.section.tomorrow": {"en": "Tomorrow", "zh": "明天", "fr": "Demain", "es": "Mañana"},
    "digest.section.this_week": {"en": "This week", "zh": "本周", "fr": "Cette semaine", "es": "Esta semana"},
    "digest.section.later": {"en": "Later", "zh": "稍后", "fr": "Plus tard", "es": "Más adelante"},
    "digest.attention": {"en": "{n} items need attention", "zh": "{n} 项需要关注", "fr": "{n} articles à surveiller", "es": "{n} artículos requieren atención"},
    "pantry.tracked": {"en": "{n} items tracked", "zh": "正在跟踪 {n} 项", "fr": "{n} articles suivis", "es": "{n} artículos registrados"},
    "digest.more": {
        "en": "+ {n} more items",
        "zh": "+ 还有 {n} 项",
        "fr": "+ {n} autres articles",
        "es": "+ {n} artículos más",
    },
    "item.tail.today": {"en": "today", "zh": "今天", "fr": "aujourd'hui", "es": "hoy"},
    "item.tail.tomorrow": {"en": "tomorrow", "zh": "明天", "fr": "demain", "es": "mañana"},
    "item.tail.expired": {"en": "{n}d overdue", "zh": "已过期 {n}天",
                          "fr": "en retard de {n}j", "es": "vencido hace {n}d"},
    "item.tail.days": {"en": "({n}d)", "zh": "({n}天)", "fr": "({n}j)", "es": "({n}d)"},
    "list.empty": {"en": "no items match this filter"},
    "digest.title": {"en": "🥬 Pantry · {weekday}, {date}",
                     "zh": "🥬 食品储藏 · {weekday}，{date}",
                     "fr": "🥬 Garde-manger · {weekday} {date}",
                     "es": "🥬 Despensa · {weekday}, {date}"},
    "item.detail.quantity": {"en": "Quantity · {value}", "zh": "数量 · {value}", "fr": "Quantité · {value}", "es": "Cantidad · {value}"},
    "item.detail.storage": {"en": "Stored · {value}", "zh": "储存 · {value}", "fr": "Stockage · {value}", "es": "Guardado · {value}"},
    "item.detail.shelf_life": {"en": "Shelf life · {days} days", "zh": "保质期 · {days} 天", "fr": "Conservation · {days} jours", "es": "Duración · {days} días"},
    "storage.default": {"en": "Pantry", "zh": "常温", "fr": "Garde-manger", "es": "Despensa"},
    "storage.fridge": {"en": "Fridge", "zh": "冷藏", "fr": "Réfrigérateur", "es": "Nevera"},
    "storage.frozen": {"en": "Freezer", "zh": "冷冻", "fr": "Congélateur", "es": "Congelador"},
    "category.produce": {"en": "Produce", "zh": "果蔬", "fr": "Fruits et légumes", "es": "Frutas y verduras"},
    "category.dairy": {"en": "Dairy", "zh": "乳制品", "fr": "Produits laitiers", "es": "Lácteos"},
    "category.meat": {"en": "Meat", "zh": "肉类", "fr": "Viande", "es": "Carne"},
    "category.seafood": {"en": "Seafood", "zh": "海鲜", "fr": "Fruits de mer", "es": "Mariscos"},
    "category.bakery": {"en": "Bakery", "zh": "烘焙", "fr": "Boulangerie", "es": "Panadería"},
    "category.frozen": {"en": "Frozen", "zh": "冷冻", "fr": "Surgelés", "es": "Congelados"},
    "category.beverage": {"en": "Beverage", "zh": "饮料", "fr": "Boissons", "es": "Bebidas"},
    "category.pantry": {"en": "Pantry", "zh": "杂货", "fr": "Garde-manger", "es": "Despensa"},
    "category.other": {"en": "Other", "zh": "其他", "fr": "Autre", "es": "Otro"},
    "lang.set": {
        "en": "Language set to {lang}.",
        "zh": "语言已设置为 {lang}。",
        "fr": "Langue définie sur {lang}.",
        "es": "Idioma configurado a {lang}.",
    },
    "lang.current": {
        "en": "Current language: {lang}. Change with /lang [{choices}]",
        "zh": "当前语言：{lang}。使用 /lang [{choices}] 更改",
        "fr": "Langue actuelle : {lang}. Changez avec /lang [{choices}]",
        "es": "Idioma actual: {lang}. Cambia con /lang [{choices}]",
    },
    "cost.value": {"en": "Cost: ${amount}", "zh": "费用：${amount}",
                   "fr": "Coût : {amount} $", "es": "Costo: ${amount}"},
    "cost.unavailable": {"en": "Cost: unavailable", "zh": "费用：不可用",
                         "fr": "Coût : indisponible", "es": "Costo: no disponible"},
    "ingest.none_found": {"en": "No food items found in this receipt.",
                          "zh": "此收据中未找到食品。",
                          "fr": "Aucun aliment trouvé sur ce reçu.",
                          "es": "No se encontraron alimentos en este recibo."},
    "ingest.none_clear": {"en": "No clear food items found (skipped {n} unclear items).",
                          "zh": "未找到清晰的食品（跳过 {n} 个不明项）。",
                          "fr": "Aucun aliment clair trouvé (ignoré {n} articles).",
                          "es": "No se hallaron alimentos claros (se omitieron {n})."},
    "ingest.logged": {"en": "Logged {n} items from this receipt:",
                      "zh": "已从此收据记录 {n} 项：",
                      "fr": "{n} articles enregistrés depuis ce reçu :",
                      "es": "Se registraron {n} artículos de este recibo:"},
    "ingest.refined_mark": {"en": " ✓refined", "zh": " ✓已优化", "fr": " ✓affiné", "es": " ✓refinado"},
    "ingest.purchase_date": {"en": "Purchase date: {date}", "zh": "购买日期：{date}",
                             "fr": "Date d'achat : {date}", "es": "Fecha de compra: {date}"},
    "ingest.purchase_date_assumed": {"en": "Purchase date assumed: {date}",
                                     "zh": "假定购买日期：{date}",
                                     "fr": "Date d'achat supposée : {date}",
                                     "es": "Fecha de compra asumida: {date}"},
    "ingest.low_confidence": {"en": "Low confidence: {ids}{more} - review with /correct or /delete",
                              "zh": "低置信度：{ids}{more} - 用 /correct 或 /delete 复核",
                              "fr": "Faible confiance : {ids}{more} - vérifiez avec /correct ou /delete",
                              "es": "Baja confianza: {ids}{more} - revisa con /correct o /delete"},
    "ingest.skipped_unclear": {"en": "(skipped {n} unclear items: {names}{more})",
                               "zh": "（跳过 {n} 个不明项：{names}{more}）",
                               "fr": "(ignoré {n} articles : {names}{more})",
                               "es": "(omitidos {n} artículos: {names}{more})"},
    "ingest.skipped_excluded": {"en": "Skipped (not tracked): {names}{more}",
                                "zh": "已跳过（未跟踪）：{names}{more}",
                                "fr": "Ignoré (non suivi) : {names}{more}",
                                "es": "Omitido (sin seguimiento): {names}{more}"},
    "ingest.want_tracked": {"en": "Want one tracked? /add <name>",
                            "zh": "想跟踪某项？/add <名称>",
                            "fr": "Suivre un article ? /add <nom>",
                            "es": "¿Seguir uno? /add <nombre>"},
    "ingest.item": {"en": "  - #{id} {name} - exp {date} ({days}d){mark}",
                    "zh": "  - #{id} {name} - 到期 {date} ({days}天){mark}",
                    "fr": "  - #{id} {name} - exp {date} ({days}j){mark}",
                    "es": "  - #{id} {name} - vence {date} ({days}d){mark}"},
    "cook.none": {
        "en": "Couldn't find a recipe that fits your pantry and restrictions.",
        "zh": "找不到符合您储藏和限制的食谱。",
        "fr": "Aucune recette ne correspond à votre garde-manger et vos restrictions.",
        "es": "No se encontró ninguna receta que se ajuste a tu despensa y restricciones.",
    },
    "cook.health": {
        "en": "Health {score}/100 · {effort} · {minutes} min",
        "zh": "健康 {score}/100 · {effort} · {minutes} 分钟",
        "fr": "Santé {score}/100 · {effort} · {minutes} min",
        "es": "Salud {score}/100 · {effort} · {minutes} min",
    },
    "cook.card.title": {"en": "{marker} {title}\n{cuisine}", "zh": "{marker} {title}\n{cuisine}", "fr": "{marker} {title}\n{cuisine}", "es": "{marker} {title}\n{cuisine}"},
    "cook.ingredients": {
        "en": "  Ingredients: {items}",
        "zh": "  食材：{items}",
        "fr": "  Ingrédients : {items}",
        "es": "  Ingredientes: {items}",
    },
    "cook.recipe_link": {
        "en": "  Recipe: {url}",
        "zh": "  食谱链接：{url}",
        "fr": "  Recette : {url}",
        "es": "  Receta: {url}",
    },
    "cook.need_buy": {
        "en": "  Need to buy: {items}",
        "zh": "  需要购买：{items}",
        "fr": "  À acheter : {items}",
        "es": "  Necesita comprar: {items}",
    },
    "cook.need_buy_none": {
        "en": "  Need to buy: nothing - you have it all!",
        "zh": "  无需购买——您已备齐所有食材！",
        "fr": "  Rien à acheter — vous avez tout ce qu'il faut !",
        "es": "  No necesita comprar nada - ¡tienes todo lo necesario!",
    },
    "shopping.empty": {
        "en": "Your shopping list is empty. Tap ➕ Shopping list on a /cook result.",
        "zh": "您的购物清单为空。在 /cook 结果中点击 ➕ Shopping list。",
        "fr": "Votre liste de courses est vide. Appuyez sur ➕ Shopping list dans un résultat /cook.",
        "es": "Tu lista de compras está vacía. Toca ➕ Shopping list en un resultado de /cook.",
    },
    "shopping.title": {
        "en": "🛒 Shopping list · {n} items",
        "zh": "🛒 购物清单 · {n} 项",
        "fr": "🛒 Liste de courses · {n} articles",
        "es": "🛒 Lista de compras · {n} artículos",
    },
    "favorites.empty": {
        "en": "No saved recipes yet. Tap ★ Save on a /cook result.",
        "zh": "还没有保存的食谱。在 /cook 结果中点击 ★ Save。",
        "fr": "Aucune recette sauvegardée. Appuyez sur ★ Save dans un résultat /cook.",
        "es": "Aún no hay recetas guardadas. Toca ★ Save en un resultado de /cook.",
    },
    "favorites.title": {
        "en": "★ Saved recipes · {n}",
        "zh": "★ 已保存的食谱 · {n}",
        "fr": "★ Recettes sauvegardées · {n}",
        "es": "★ Recetas guardadas · {n}",
    },
    # -----------------------------------------------------------------------
    # Button labels (Task 15)
    # -----------------------------------------------------------------------
    "btn.ate": {"en": "Ate", "zh": "吃掉", "fr": "Mangé", "es": "Comido"},
    "btn.tossed": {"en": "Tossed", "zh": "扔掉", "fr": "Jeté", "es": "Tirado"},
    "btn.snooze2": {"en": "Remind +2d", "zh": "提醒 +2天", "fr": "Rappel +2j", "es": "Recordar +2d"},
    "btn.freeze": {"en": "❄️ Freeze", "zh": "❄️ 冷冻", "fr": "❄️ Congeler", "es": "❄️ Congelar"},
    "btn.fridge": {"en": "🧊 Fridge", "zh": "🧊 冷藏", "fr": "🧊 Frigo", "es": "🧊 Nevera"},
    "btn.show_all": {"en": "☰ View full pantry", "zh": "☰ 查看全部储藏", "fr": "☰ Voir tout le garde-manger", "es": "☰ Ver toda la despensa"},
    "btn.undo": {"en": "Undo", "zh": "撤销", "fr": "Annuler", "es": "Deshacer"},
    "btn.apply": {"en": "Apply", "zh": "应用", "fr": "Appliquer", "es": "Aplicar"},
    "btn.cancel": {"en": "Cancel", "zh": "取消", "fr": "Annuler", "es": "Cancelar"},
    "progress.reading_receipt": {
        "en": "📸 Reading your receipt…",
        "zh": "📸 正在读取收据…",
        "fr": "📸 Lecture du reçu…",
        "es": "📸 Leyendo tu recibo…",
    },
    "progress.parsing_add": {
        "en": "📝 Adding your items…",
        "zh": "📝 正在添加食品…",
        "fr": "📝 Ajout de vos articles…",
        "es": "📝 Añadiendo tus artículos…",
    },
    "nl.thinking": {"en": "💬 On it…", "zh": "💬 处理中…", "fr": "💬 Je m'en occupe…", "es": "💬 Voy…"},
    "nl.hint": {
        "en": "I didn't catch that. Try \"bought milk and eggs\", \"ate the yogurt\", or /help.",
        "zh": "没听懂。试试“买了牛奶和鸡蛋”、“吃完了酸奶”，或 /help。",
        "fr": "Je n'ai pas compris. Essayez « acheté du lait et des œufs », « mangé le yaourt », ou /help.",
        "es": "No entendí. Prueba \"compré leche y huevos\", \"me comí el yogur\", o /help.",
    },
    "nl.not_found": {
        "en": "I couldn't find \"{name}\" in your pantry.",
        "zh": "在您的储藏中找不到“{name}”。",
        "fr": "Je ne trouve pas « {name} » dans votre garde-manger.",
        "es": "No encontré \"{name}\" en tu despensa.",
    },
    "nl.which_one": {"en": "Which one?", "zh": "是哪一个？", "fr": "Lequel ?", "es": "¿Cuál?"},
    "nl.done.ate": {"en": "✅ Ate {name}.", "zh": "✅ 已吃完 {name}。", "fr": "✅ {name} mangé.", "es": "✅ {name} comido."},
    "nl.done.tossed": {"en": "🗑 Tossed {name}.", "zh": "🗑 已丢弃 {name}。", "fr": "🗑 {name} jeté.", "es": "🗑 {name} desechado."},
    "nl.done.snooze": {"en": "⏰ Snoozed {name} 2 days.", "zh": "⏰ {name} 已延后 2 天。", "fr": "⏰ {name} reporté de 2 jours.", "es": "⏰ {name} pospuesto 2 días."},
    "nl.done.freeze": {"en": "❄️ Froze {name}.", "zh": "❄️ 已冷冻 {name}。", "fr": "❄️ {name} congelé.", "es": "❄️ {name} congelado."},
    "nl.already_done": {
        "en": "{name} was already updated.",
        "zh": "{name} 已经更新过了。",
        "fr": "{name} a déjà été mis à jour.",
        "es": "{name} ya estaba actualizado.",
    },
    "nl.shelf_life": {
        "en": "{name} keeps about {days} days.",
        "zh": "{name} 大约可保存 {days} 天。",
        "fr": "{name} se conserve environ {days} jours.",
        "es": "{name} dura unos {days} días.",
    },
    "nl.shelf_life_unknown": {
        "en": "I'm not sure how long {name} keeps.",
        "zh": "我不确定 {name} 能保存多久。",
        "fr": "Je ne sais pas combien de temps {name} se conserve.",
        "es": "No estoy seguro de cuánto dura {name}.",
    },
    "nl.no_plan_today": {
        "en": "No planned meal for today — try /plan to build a week.",
        "zh": "今天没有计划的餐点 — 试试 /plan 制定一周计划。",
        "fr": "Aucun repas prévu aujourd'hui — essayez /plan pour créer une semaine.",
        "es": "No hay comida planificada para hoy — prueba /plan para crear una semana.",
    },
    "plan.header": {
        "en": "🗓 Dinner plan — {n} days",
        "zh": "🗓 晚餐计划 — {n} 天",
        "fr": "🗓 Plan de dîners — {n} jours",
        "es": "🗓 Plan de cenas — {n} días",
    },
    "plan.day_line": {
        "en": "{weekday}: {title} ({cuisine}, {minutes}m){fire}",
        "zh": "{weekday}：{title}（{cuisine}，{minutes}分钟）{fire}",
        "fr": "{weekday} : {title} ({cuisine}, {minutes}min){fire}",
        "es": "{weekday}: {title} ({cuisine}, {minutes}m){fire}",
    },
    "plan.progress": {
        "en": "🗓 Planning your week…",
        "zh": "🗓 正在为你规划本周菜单…",
        "fr": "🗓 Planification de votre semaine…",
        "es": "🗓 Planificando tu semana…",
    },
    "plan.not_enough": {
        "en": "Not enough pantry items to plan — /add a few things first.",
        "zh": "食品储藏不足，无法规划 —— 请先 /add 一些食品。",
        "fr": "Pas assez d'articles pour planifier — /add quelques articles d'abord.",
        "es": "No hay suficientes artículos para planificar — /add algunas cosas primero.",
    },
    "plan.none_active": {
        "en": "No active meal plan yet — send /plan to build one.",
        "zh": "还没有有效的用餐计划 — 发送 /plan 来创建一个。",
        "fr": "Aucun plan de repas actif — envoyez /plan pour en créer un.",
        "es": "Aún no hay un plan de comidas activo — envía /plan para crear uno.",
    },
    "plan.no_swap": {
        "en": "No other recipe found for that day — try again later.",
        "zh": "找不到该天的其他菜谱 —— 请稍后再试。",
        "fr": "Aucune autre recette trouvée pour ce jour — réessayez plus tard.",
        "es": "No se encontró otra receta para ese día — inténtalo más tarde.",
    },
    "plan.shopping_added": {
        "en": "Added {n} items to your shopping list.",
        "zh": "已将 {n} 件物品加入购物清单。",
        "fr": "{n} articles ajoutés à votre liste de courses.",
        "es": "Se añadieron {n} artículos a tu lista de compras.",
    },
    "plan.shopping_none": {
        "en": "Everything's already covered.",
        "zh": "所有食材都已齐备。",
        "fr": "Tout est déjà couvert.",
        "es": "Ya tienes todo cubierto.",
    },
    "plan.cancelled": {
        "en": "Plan cancelled.",
        "zh": "计划已取消。",
        "fr": "Plan annulé.",
        "es": "Plan cancelado.",
    },
    "plan.superseded": {
        "en": "(Replaced your previous plan.)",
        "zh": "（已替换你之前的计划。）",
        "fr": "(A remplacé votre plan précédent.)",
        "es": "(Reemplazó tu plan anterior.)",
    },
    "plan.expired": {
        "en": "this plan expired - run /plan again",
        "zh": "该计划已过期 —— 请重新运行 /plan",
        "fr": "ce plan a expiré - relancez /plan",
        "es": "este plan expiró - ejecuta /plan de nuevo",
    },
    "btn.plan.swap": {"en": "🔄 {weekday}", "zh": "🔄 {weekday}", "fr": "🔄 {weekday}", "es": "🔄 {weekday}"},
    "btn.plan.cooked": {
        "en": "✅ Cooked {weekday}",
        "zh": "✅ 已做 {weekday}",
        "fr": "✅ Cuisiné {weekday}",
        "es": "✅ Cocinado {weekday}",
    },
    "btn.plan.shop": {"en": "➕ Shopping list", "zh": "➕ 购物清单", "fr": "➕ Liste de courses", "es": "➕ Lista de compras"},
    "btn.plan.cancel": {"en": "❌ Cancel plan", "zh": "❌ 取消计划", "fr": "❌ Annuler le plan", "es": "❌ Cancelar plan"},
    "btn.open_recipe": {"en": "🔗 Open recipe", "zh": "🔗 查看菜谱", "fr": "🔗 Ouvrir la recette", "es": "🔗 Abrir receta"},
    "meal.dinner": {"en": "Dinner", "zh": "晚餐", "fr": "Dîner", "es": "Cena"},
    "meal.lunch": {"en": "Lunch", "zh": "午餐", "fr": "Déjeuner", "es": "Almuerzo"},
    "meal.breakfast": {"en": "Breakfast", "zh": "早餐", "fr": "Petit-déjeuner", "es": "Desayuno"},
    "meal.dessert": {"en": "Dessert", "zh": "甜点", "fr": "Dessert", "es": "Postre"},
    "meal.snack": {"en": "Snack", "zh": "零食", "fr": "En-cas", "es": "Tentempié"},
    "meal.surprise": {"en": "Surprise me", "zh": "随便", "fr": "Surprends-moi", "es": "Sorpréndeme"},
    "cuisine.african": {"en": "African", "zh": "非洲菜", "fr": "Cuisine africaine", "es": "Cocina africana"},
    "cuisine.asian": {"en": "Asian", "zh": "亚洲菜", "fr": "Cuisine asiatique", "es": "Cocina asiática"},
    "cuisine.american": {"en": "American", "zh": "美式菜", "fr": "Cuisine américaine", "es": "Cocina estadounidense"},
    "cuisine.british": {"en": "British", "zh": "英国菜", "fr": "Cuisine britannique", "es": "Cocina británica"},
    "cuisine.cajun": {"en": "Cajun", "zh": "卡津菜", "fr": "Cuisine cajun", "es": "Cocina cajún"},
    "cuisine.caribbean": {"en": "Caribbean", "zh": "加勒比菜", "fr": "Cuisine caribéenne", "es": "Cocina caribeña"},
    "cuisine.chinese": {"en": "Chinese", "zh": "中餐", "fr": "Cuisine chinoise", "es": "Cocina china"},
    "cuisine.eastern_european": {"en": "Eastern European", "zh": "东欧菜", "fr": "Cuisine d’Europe de l’Est", "es": "Cocina de Europa del Este"},
    "cuisine.european": {"en": "European", "zh": "欧洲菜", "fr": "Cuisine européenne", "es": "Cocina europea"},
    "cuisine.french": {"en": "French", "zh": "法餐", "fr": "Cuisine française", "es": "Cocina francesa"},
    "cuisine.german": {"en": "German", "zh": "德国菜", "fr": "Cuisine allemande", "es": "Cocina alemana"},
    "cuisine.greek": {"en": "Greek", "zh": "希腊菜", "fr": "Cuisine grecque", "es": "Cocina griega"},
    "cuisine.indian": {"en": "Indian", "zh": "印度菜", "fr": "Cuisine indienne", "es": "Cocina india"},
    "cuisine.irish": {"en": "Irish", "zh": "爱尔兰菜", "fr": "Cuisine irlandaise", "es": "Cocina irlandesa"},
    "cuisine.italian": {"en": "Italian", "zh": "意大利菜", "fr": "Cuisine italienne", "es": "Cocina italiana"},
    "cuisine.japanese": {"en": "Japanese", "zh": "日本料理", "fr": "Cuisine japonaise", "es": "Cocina japonesa"},
    "cuisine.jewish": {"en": "Jewish", "zh": "犹太菜", "fr": "Cuisine juive", "es": "Cocina judía"},
    "cuisine.korean": {"en": "Korean", "zh": "韩国菜", "fr": "Cuisine coréenne", "es": "Cocina coreana"},
    "cuisine.latin_american": {"en": "Latin American", "zh": "拉丁美洲菜", "fr": "Cuisine latino-américaine", "es": "Cocina latinoamericana"},
    "cuisine.mediterranean": {"en": "Mediterranean", "zh": "地中海菜", "fr": "Cuisine méditerranéenne", "es": "Cocina mediterránea"},
    "cuisine.mexican": {"en": "Mexican", "zh": "墨西哥菜", "fr": "Cuisine mexicaine", "es": "Cocina mexicana"},
    "cuisine.middle_eastern": {"en": "Middle Eastern", "zh": "中东菜", "fr": "Cuisine du Moyen-Orient", "es": "Cocina de Oriente Medio"},
    "cuisine.nordic": {"en": "Nordic", "zh": "北欧菜", "fr": "Cuisine nordique", "es": "Cocina nórdica"},
    "cuisine.southern": {"en": "Southern", "zh": "南方菜", "fr": "Cuisine du Sud", "es": "Cocina sureña"},
    "cuisine.spanish": {"en": "Spanish", "zh": "西班牙菜", "fr": "Cuisine espagnole", "es": "Cocina española"},
    "cuisine.thai": {"en": "Thai", "zh": "泰国菜", "fr": "Cuisine thaïlandaise", "es": "Cocina tailandesa"},
    "cuisine.vietnamese": {"en": "Vietnamese", "zh": "越南菜", "fr": "Cuisine vietnamienne", "es": "Cocina vietnamita"},
    "cuisine.surprise": {"en": "Surprise me", "zh": "随便", "fr": "Surprends-moi", "es": "Sorpréndeme"},
    "cook.round.purpose": {"en": "What's the goal?", "zh": "目标是什么？", "fr": "Quel est le but ?", "es": "¿Cuál es el objetivo?"},
    "purpose.use_it_up": {"en": "Use it up", "zh": "用完食材", "fr": "Tout utiliser", "es": "Aprovechar"},
    "purpose.quick": {"en": "Quick (≤30m)", "zh": "快手 (≤30分)", "fr": "Rapide (≤30min)", "es": "Rápido (≤30m)"},
    "purpose.healthy": {"en": "Healthy", "zh": "健康", "fr": "Sain", "es": "Saludable"},
    "purpose.comfort": {"en": "Comfort", "zh": "解馋", "fr": "Réconfort", "es": "Reconfortante"},
    "purpose.surprise": {"en": "Surprise me", "zh": "随便", "fr": "Surprends-moi", "es": "Sorpréndeme"},
    "btn.more_recipes": {"en": "🔄 More", "zh": "🔄 更多", "fr": "🔄 Plus", "es": "🔄 Más"},
    "btn.adjust": {"en": "🎛 Adjust", "zh": "🎛 调整", "fr": "🎛 Ajuster", "es": "🎛 Ajustar"},
    "btn.more_cuisines": {"en": "More cuisines »", "zh": "更多菜系 »", "fr": "Plus de cuisines »", "es": "Más cocinas »"},
    "cook.no_more": {"en": "No more recipes for these filters — try 🎛 Adjust.", "zh": "这些筛选没有更多菜谱了 —— 试试 🎛 调整。", "fr": "Plus de recettes pour ces filtres — essayez 🎛 Ajuster.", "es": "No hay más recetas para estos filtros — prueba 🎛 Ajustar."},
    "btn.correct": {"en": "✏️ Correct", "zh": "✏️ 更正", "fr": "✏️ Corriger", "es": "✏️ Corregir"},
    "btn.remove": {"en": "❌ Remove", "zh": "❌ 移除", "fr": "❌ Retirer", "es": "❌ Quitar"},
    "btn.back_to_list": {"en": "⬅ Back to list", "zh": "⬅ 返回列表", "fr": "⬅ Retour à la liste", "es": "⬅ Volver a la lista"},
    "btn.back": {"en": "⬅ Back", "zh": "⬅ 返回", "fr": "⬅ Retour", "es": "⬅ Volver"},
    "btn.remove_yes": {"en": "✅ Yes, remove", "zh": "✅ 确认移除", "fr": "✅ Oui, retirer", "es": "✅ Sí, quitar"},
    "btn.nudge_plus_week": {"en": "+1w longer", "zh": "+1周", "fr": "+1 sem.", "es": "+1 sem."},
    "btn.nudge_plus_3d": {"en": "+3d longer", "zh": "+3天", "fr": "+3 j", "es": "+3 d"},
    "btn.nudge_minus_3d": {"en": "−3d shorter", "zh": "−3天", "fr": "−3 j", "es": "−3 d"},
    "btn.nudge_use_today": {"en": "use by today", "zh": "今天用完", "fr": "à finir aujourd'hui", "es": "usar hoy"},
    "btn.correct_other": {"en": "💬 Something else…", "zh": "💬 其他…", "fr": "💬 Autre chose…", "es": "💬 Otra cosa…"},
    "btn.liked": {"en": "👍 Liked", "zh": "👍 喜欢", "fr": "👍 Aimé", "es": "👍 Me gusta"},
    "btn.disliked": {"en": "👎 Not for me", "zh": "👎 不喜欢", "fr": "👎 Pas pour moi", "es": "👎 No es para mí"},
    "btn.save": {"en": "★ Save", "zh": "★ 保存", "fr": "★ Sauvegarder", "es": "★ Guardar"},
    "btn.shopping": {"en": "➕ Shopping list", "zh": "➕ 购物清单", "fr": "➕ Liste de courses", "es": "➕ Lista de compras"},
    "btn.cooked.meal": {
        "en": "✅ I cooked this",
        "zh": "✅ 我做了这道菜",
        "fr": "✅ J’ai cuisiné ce plat",
        "es": "✅ Cociné este plato",
    },
    "ingest.shopping_checked": {
        "en": "Shopping list checked off: {names}",
        "zh": "已从购物清单勾选：{names}",
        "fr": "Articles cochés dans la liste de courses : {names}",
        "es": "Marcado en la lista de compras: {names}",
    },
    "history.title": {
        "en": "🍽 Cooked history",
        "zh": "🍽 烹饪记录",
        "fr": "🍽 Historique des repas",
        "es": "🍽 Historial de comidas",
    },
    "history.empty": {
        "en": "No cooked meals recorded yet.",
        "zh": "还没有已记录的烹饪餐食。",
        "fr": "Aucun repas cuisiné enregistré pour le moment.",
        "es": "Aún no hay comidas cocinadas registradas.",
    },
    "history.row": {
        "en": "{date} · {title} · {source}",
        "zh": "{date} · {title} · {source}",
        "fr": "{date} · {title} · {source}",
        "es": "{date} · {title} · {source}",
    },
    "btn.show_alternatives": {"en": "Show alternatives", "zh": "显示替代方案", "fr": "Voir les alternatives", "es": "Ver alternativas"},
    "btn.cook_again": {"en": "Cook this again", "zh": "再做一次", "fr": "Refaire ce plat", "es": "Cocinar de nuevo"},
    "btn.bought": {"en": "Bought ✓", "zh": "已购买 ✓", "fr": "Acheté ✓", "es": "Comprado ✓"},
    # -----------------------------------------------------------------------
    # Terminal states (Task 15)
    # -----------------------------------------------------------------------
    "terminal.cancelled": {"en": "Cancelled.", "zh": "已取消。", "fr": "Annulé.", "es": "Cancelado."},
    "terminal.expired": {
        "en": "This proposal has expired - re-run the command.",
        "zh": "此提案已过期，请重新运行命令。",
        "fr": "Cette proposition a expiré - relancez la commande.",
        "es": "Esta propuesta ha expirado - vuelve a ejecutar el comando.",
    },
    "terminal.stale": {
        "en": "This proposal is stale (the item changed) - re-run the command.",
        "zh": "此提案已过时（项目已更改），请重新运行命令。",
        "fr": "Cette proposition est périmée (l'article a changé) - relancez la commande.",
        "es": "Esta propuesta está desactualizada (el artículo cambió) - vuelve a ejecutar el comando.",
    },
    "terminal.applied": {
        "en": "This proposal was already applied.",
        "zh": "此提案已经应用。",
        "fr": "Cette proposition a déjà été appliquée.",
        "es": "Esta propuesta ya fue aplicada.",
    },
    "terminal.unknown": {
        "en": "This proposal is no longer pending ({status}).",
        "zh": "此提案不再处于待处理状态（{status}）。",
        "fr": "Cette proposition n'est plus en attente ({status}).",
        "es": "Esta propuesta ya no está pendiente ({status}).",
    },
    # -----------------------------------------------------------------------
    # render_correction_diff (Task 15)
    # -----------------------------------------------------------------------
    "correction.header": {
        "en": "Proposed correction for #{item_id} {item_raw_name}:",
        "zh": "#{item_id} {item_raw_name} 的建议更正：",
        "fr": "Correction proposée pour #{item_id} {item_raw_name} :",
        "es": "Corrección propuesta para #{item_id} {item_raw_name}:",
    },
    "correction.field": {
        "en": "  - {field_name}: {old} -> {new}{suffix}",
        "zh": "  - {field_name}：{old} -> {new}{suffix}",
        "fr": "  - {field_name} : {old} -> {new}{suffix}",
        "es": "  - {field_name}: {old} -> {new}{suffix}",
    },
    "correction.back_computed": {
        "en": "  (back-computed from expires_on)",
        "zh": "  （从 expires_on 反推）",
        "fr": "  (calculé à rebours depuis expires_on)",
        "es": "  (calculado desde expires_on)",
    },
    "correction.cache": {
        "en": "  - cache: {cache_action}",
        "zh": "  - 缓存：{cache_action}",
        "fr": "  - cache : {cache_action}",
        "es": "  - caché: {cache_action}",
    },
    "correction.rationale": {
        "en": "Reason: {rationale}",
        "zh": "原因：{rationale}",
        "fr": "Raison : {rationale}",
        "es": "Razón: {rationale}",
    },
    "correction.expires": {
        "en": "Expires in 10 min.",
        "zh": "10 分钟后过期。",
        "fr": "Expire dans 10 min.",
        "es": "Expira en 10 min.",
    },
    "correct.menu_header": {
        "en": "✏️ Correct #{id} {name}\nshelf life {days}d · expires {date}",
        "zh": "✏️ 更正 #{id} {name}\n保质期 {days}天 · 到期 {date}",
        "fr": "✏️ Corriger #{id} {name}\nconservation {days}j · expire {date}",
        "es": "✏️ Corregir #{id} {name}\nvida útil {days}d · vence {date}",
    },
    "remove.confirm": {
        "en": "Remove #{id} {name}?\nThis can't be undone here.",
        "zh": "移除 #{id} {name}？\n此操作无法在这里撤销。",
        "fr": "Retirer #{id} {name} ?\nCeci ne peut pas être annulé ici.",
        "es": "¿Quitar #{id} {name}?\nEsto no se puede deshacer aquí.",
    },
    "correct.freetext_prompt": {
        "en": "Reply to this message with the fix for #{id} {name} — e.g. \"lasts 2 weeks\" or \"it's whole milk\". [correct:#{id}]",
        "zh": "回复此消息以更正 #{id} {name} —— 例如“能放两周”或“是全脂牛奶”。 [correct:#{id}]",
        "fr": "Répondez à ce message avec la correction pour #{id} {name} — par ex. « se garde 2 semaines » ou « c'est du lait entier ». [correct:#{id}]",
        "es": "Responde a este mensaje con la corrección para #{id} {name} — p. ej. «dura 2 semanas» o «es leche entera». [correct:#{id}]",
    },
    # -----------------------------------------------------------------------
    # render_add_diff (Task 15)
    # -----------------------------------------------------------------------
    "add.header": {
        "en": "Proposed add - {name}:",
        "zh": "建议添加 - {name}：",
        "fr": "Ajout proposé - {name} :",
        "es": "Agregar propuesto - {name}:",
    },
    "add.category": {
        "en": "  - category: {category}",
        "zh": "  - 类别：{category}",
        "fr": "  - catégorie : {category}",
        "es": "  - categoría: {category}",
    },
    "add.qty_unit": {
        "en": "  - qty / unit: {qty}{unit}",
        "zh": "  - 数量/单位：{qty}{unit}",
        "fr": "  - qté / unité : {qty}{unit}",
        "es": "  - cant. / unidad: {qty}{unit}",
    },
    "add.expires_on": {
        "en": "  - expires_on: {expires_on}",
        "zh": "  - 到期日：{expires_on}",
        "fr": "  - expire le : {expires_on}",
        "es": "  - vence el: {expires_on}",
    },
    "add.shelf_life": {
        "en": "  - shelf_life_days: {shelf_life_days} (source: {shelf_life_source})",
        "zh": "  - 保质期（天）：{shelf_life_days}（来源：{shelf_life_source}）",
        "fr": "  - durée de conservation : {shelf_life_days} j (source : {shelf_life_source})",
        "es": "  - vida útil (días): {shelf_life_days} (fuente: {shelf_life_source})",
    },
    "add.confidence": {
        "en": "Confidence: {confidence}",
        "zh": "置信度：{confidence}",
        "fr": "Confiance : {confidence}",
        "es": "Confianza: {confidence}",
    },
    # -----------------------------------------------------------------------
    # render_undo_result (Task 15)
    # -----------------------------------------------------------------------
    "undo.expired": {
        "en": "Undo window expired (10 min) - use /delete <id> instead.",
        "zh": "撤销窗口已过期（10 分钟），请改用 /delete <id>。",
        "fr": "Fenêtre d'annulation expirée (10 min) - utilisez /delete <id> à la place.",
        "es": "Ventana de deshacer expirada (10 min) - usa /delete <id> en su lugar.",
    },
    "undo.nothing": {
        "en": "Nothing to undo.",
        "zh": "没有可撤销的内容。",
        "fr": "Rien à annuler.",
        "es": "Nada que deshacer.",
    },
    "undo.removed": {
        "en": "Undone: removed {n} item(s).",
        "zh": "已撤销：移除了 {n} 项。",
        "fr": "Annulé : {n} article(s) supprimé(s).",
        "es": "Deshecho: se eliminaron {n} artículo(s).",
    },
    "undo.skipped": {
        "en": "skipped {skipped}.",
        "zh": "已跳过 {skipped}。",
        "fr": "ignoré {skipped}.",
        "es": "omitido {skipped}.",
    },
    # -----------------------------------------------------------------------
    # render_applied_correction / render_applied_add (Task 15)
    # -----------------------------------------------------------------------
    "applied.correction": {
        "en": "Applied to #{item_id}: {suffix}",
        "zh": "已应用至 #{item_id}：{suffix}",
        "fr": "Appliqué à #{item_id} : {suffix}",
        "es": "Aplicado a #{item_id}: {suffix}",
    },
    "applied.correction.no_changes": {
        "en": "no changes",
        "zh": "无更改",
        "fr": "aucun changement",
        "es": "sin cambios",
    },
    "applied.add": {
        "en": "Added #{item_id} {name} (expires {expires_on})",
        "zh": "已添加 #{item_id} {name}（到期：{expires_on}）",
        "fr": "Ajouté #{item_id} {name} (expire le {expires_on})",
        "es": "Agregado #{item_id} {name} (vence el {expires_on})",
    },
    # -----------------------------------------------------------------------
    # render_stats (Task 15)
    # -----------------------------------------------------------------------
    "stats.header": {
        "en": "Last 30 days",
        "zh": "最近 30 天",
        "fr": "30 derniers jours",
        "es": "Últimos 30 días",
    },
    "stats.receipts": {
        "en": "Receipts: {receipt_count} (unknown-cost: {unknown_cost_receipt_count})",
        "zh": "收据：{receipt_count}（未知费用：{unknown_cost_receipt_count}）",
        "fr": "Reçus : {receipt_count} (coût inconnu : {unknown_cost_receipt_count})",
        "es": "Recibos: {receipt_count} (costo desconocido: {unknown_cost_receipt_count})",
    },
    "stats.tracked": {
        "en": "Tracked items: {tracked_item_count}",
        "zh": "已跟踪项目：{tracked_item_count}",
        "fr": "Articles suivis : {tracked_item_count}",
        "es": "Artículos rastreados: {tracked_item_count}",
    },
    "stats.removed": {
        "en": "Removed (wrong import): {removed_item_count}",
        "zh": "已移除（导入有误）：{removed_item_count}",
        "fr": "Supprimés (import incorrect) : {removed_item_count}",
        "es": "Eliminados (importación incorrecta): {removed_item_count}",
    },
    "stats.cache_hit": {
        "en": "Cache hit rate: {cache_hit}",
        "zh": "缓存命中率：{cache_hit}",
        "fr": "Taux de cache : {cache_hit}",
        "es": "Tasa de aciertos de caché: {cache_hit}",
    },
    "stats.llm_spend": {
        "en": "LLM spend: total {total_cost} avg {avg_cost} / receipt",
        "zh": "LLM 花费：总计 {total_cost} 平均 {avg_cost} / 收据",
        "fr": "Dépense LLM : total {total_cost} moy. {avg_cost} / reçu",
        "es": "Gasto LLM: total {total_cost} prom. {avg_cost} / recibo",
    },
    "stats.corrections": {
        "en": "  Corrections: {count} (${cost_total} total{unknown})",
        "zh": "  更正：{count} （${cost_total} 合计{unknown}）",
        "fr": "  Corrections : {count} ({cost_total} $ total{unknown})",
        "es": "  Correcciones: {count} (${cost_total} total{unknown})",
    },
    "stats.adds": {
        "en": "  Adds: {count} (${cost_total} total{unknown})",
        "zh": "  添加：{count} （${cost_total} 合计{unknown}）",
        "fr": "  Ajouts : {count} ({cost_total} $ total{unknown})",
        "es": "  Agregar: {count} (${cost_total} total{unknown})",
    },
    "stats.meals_cooked": {
        "en": "  Meals cooked: {count}",
        "zh": "  已做餐数：{count}",
        "fr": "  Repas cuisinés : {count}",
        "es": "  Comidas cocinadas: {count}",
    },
    "stats.unknown_suffix": {
        "en": ", {n} unknown",
        "zh": "，{n} 个未知",
        "fr": ", {n} inconnu",
        "es": ", {n} desconocido",
    },
    "stats.cook_sessions": {
        "en": "Cook sessions: {count} (${cost})",
        "zh": "烹饪会话：{count}（${cost}）",
        "fr": "Sessions de cuisine : {count} ({cost} $)",
        "es": "Sesiones de cocina: {count} (${cost})",
    },
    "stats.cooked": {
        "en": "  Cooked: {feedback_count} (liked {liked_count})",
        "zh": "  已烹饪：{feedback_count}（喜欢 {liked_count}）",
        "fr": "  Cuisiné : {feedback_count} (aimé {liked_count})",
        "es": "  Cocinado: {feedback_count} (gustó {liked_count})",
    },
    "stats.waste_rate": {
        "en": "Waste rate: {waste_rate}",
        "zh": "浪费率：{waste_rate}",
        "fr": "Taux de gaspillage : {waste_rate}",
        "es": "Tasa de desperdicio: {waste_rate}",
    },
    # -----------------------------------------------------------------------
    # render_profile (Task 15)
    # -----------------------------------------------------------------------
    "profile.header": {
        "en": "Your food profile:",
        "zh": "您的饮食档案：",
        "fr": "Votre profil alimentaire :",
        "es": "Tu perfil alimentario:",
    },
    "profile.diet": {
        "en": "  Diet: {diet}",
        "zh": "  饮食：{diet}",
        "fr": "  Régime : {diet}",
        "es": "  Dieta: {diet}",
    },
    "profile.avoid": {
        "en": "  Avoid: {exclusions}",
        "zh": "  避免：{exclusions}",
        "fr": "  Éviter : {exclusions}",
        "es": "  Evitar: {exclusions}",
    },
    "profile.cuisines": {
        "en": "  Cuisines: {cuisines}",
        "zh": "  菜系：{cuisines}",
        "fr": "  Cuisines : {cuisines}",
        "es": "  Cocinas: {cuisines}",
    },
    "profile.max_cook": {
        "en": "  Max cook time: {cook}",
        "zh": "  最长烹饪时间：{cook}",
        "fr": "  Temps de cuisson max : {cook}",
        "es": "  Tiempo máximo de cocción: {cook}",
    },
    "profile.household_size": {
        "en": "  Household size: {household_size}",
        "zh": "  家庭人数：{household_size}",
        "fr": "  Taille du foyer : {household_size}",
        "es": "  Tamaño del hogar: {household_size}",
    },
    "profile.notes": {
        "en": "  Notes: {note}",
        "zh": "  备注：{note}",
        "fr": "  Notes : {note}",
        "es": "  Notas: {note}",
    },
    "profile.update_hint": {
        "en": "Update by typing: /prefs <sentence>  (e.g. /prefs I'm vegan, no peanuts)",
        "zh": "输入以更新：/prefs <语句>  （例如 /prefs I'm vegan, no peanuts）",
        "fr": "Mettez à jour en tapant : /prefs <phrase>  (ex. /prefs I'm vegan, no peanuts)",
        "es": "Actualiza escribiendo: /prefs <frase>  (ej. /prefs I'm vegan, no peanuts)",
    },
    "profile.no_limit": {
        "en": "no limit",
        "zh": "无限制",
        "fr": "sans limite",
        "es": "sin límite",
    },
    "profile.none_value": {
        "en": "none",
        "zh": "无",
        "fr": "aucun",
        "es": "ninguno",
    },
    "profile.any_value": {
        "en": "any",
        "zh": "任意",
        "fr": "tout",
        "es": "cualquiera",
    },
    "profile.note_none": {
        "en": "(none)",
        "zh": "（无）",
        "fr": "(aucune)",
        "es": "(ninguna)",
    },
    "profile.cook_minutes": {
        "en": "{minutes} min",
        "zh": "{minutes} 分钟",
        "fr": "{minutes} min",
        "es": "{minutes} min",
    },
    # -----------------------------------------------------------------------
    # /help and /start (Task 19)
    # -----------------------------------------------------------------------
    "help.body": {
        "en": (
            "Commands:\n"
            "  /start - setup status\n"
            "  /tz <IANA> - set timezone\n"
            "  /lang [en|zh|fr|es] - set your language\n"
            "  /digest_at <0..23> - set digest hour\n"
            "  /list [category|week|expired] - show pantry\n"
            "  /pantry [digest|<id>] - interactive pantry controls\n"
            "  /add <free text> - propose new items in natural language.\n"
            "      Replies with a diff per item; tap Apply or Cancel.\n"
            "      Proposals expire after 10 min.\n"
            "  /ate <id> - mark eaten\n"
            "  /toss <id> - mark tossed\n"
            "  /snooze <id> [days=2] - suppress reminders 1..30d\n"
            "  /correct <id> <free text> - propose a correction in natural\n"
            "      language (name, category, expires, days). Replies with a\n"
            "      diff; tap Apply or Cancel. Proposal expires after 10 min.\n"
            "  /delete <id> - remove a wrong/duplicate import\n"
            "  /stats - last 30 days\n"
            "  /llm [anthropic|openai|gemini|deepseek] - show or switch LLM provider\n"
            "  /prefs [sentence] - show or update your food profile\n"
            "  /cook - get a recipe from your pantry\n"
            "  /history - show meals your household cooked\n"
            "  /plan [3-7] - plan dinners for the week (default 5 days)\n"
            "  /shopping - view your to-buy list; tap an item when bought\n"
            "  /favorites - view saved recipes; tap to re-cook against your pantry\n"
            "  /invite [family] - invite one person (or 'family' for a reusable link)\n"
            "  /join <code> - join a household you were invited to\n"
            "  /household - list household members\n"
            "  /leave - leave your household\n"
            "  /remove <id> - (owner) remove a member\n"
            "  /quota - show household usage\n"
            "  /buy - choose a plan or top-up\n"
            "  /billing - show subscription status\n"
            "  /help - this message\n"
            "Send a receipt photo to log it."
        ),
        "zh": (
            "命令列表：\n"
            "  /start - 查看设置状态\n"
            "  /tz <IANA> - 设置时区\n"
            "  /lang [en|zh|fr|es] - 设置语言\n"
            "  /digest_at <0..23> - 设置每日摘要时间\n"
            "  /list [category|week|expired] - 显示食品储藏\n"
            "  /pantry [digest|<id>] - 交互式食品储藏管理\n"
            "  /add <自然语言> - 以自然语言提议添加食品。\n"
            "      每项显示差异；点击应用或取消。\n"
            "      提议10分钟后过期。\n"
            "  /ate <id> - 标记为已食用\n"
            "  /toss <id> - 标记为已丢弃\n"
            "  /snooze <id> [days=2] - 暂停提醒 1..30天\n"
            "  /correct <id> <自然语言> - 以自然语言提议更正\n"
            "      （名称、类别、到期日、天数）。显示差异；\n"
            "      点击应用或取消。提议10分钟后过期。\n"
            "  /delete <id> - 删除错误/重复导入\n"
            "  /stats - 最近30天统计\n"
            "  /llm [anthropic|openai|gemini|deepseek] - 显示或切换LLM提供商\n"
            "  /prefs [语句] - 显示或更新您的饮食档案\n"
            "  /cook - 从您的储藏获取食谱\n"
            "  /history - 查看家庭烹饪记录\n"
            "  /plan [3-7] - 规划本周晚餐（默认5天）\n"
            "  /shopping - 查看待购清单；点击已购买的物品\n"
            "  /favorites - 查看已保存食谱；点击重新烹饪\n"
            "  /invite [family] - 邀请一人（或用 'family' 生成可重复使用的链接）\n"
            "  /join <code> - 加入您被邀请的家庭\n"
            "  /household - 列出家庭成员\n"
            "  /leave - 离开您的家庭\n"
            "  /remove <id> -（所有者）移除成员\n"
            "  /quota - 查看家庭用量\n"
            "  /buy - 选择套餐或加购\n"
            "  /billing - 查看订阅状态\n"
            "  /help - 本消息\n"
            "发送收据照片以记录。"
        ),
        "fr": (
            "Commandes :\n"
            "  /start - état de la configuration\n"
            "  /tz <IANA> - définir le fuseau horaire\n"
            "  /lang [en|zh|fr|es] - définir votre langue\n"
            "  /digest_at <0..23> - heure du résumé quotidien\n"
            "  /list [category|week|expired] - afficher le garde-manger\n"
            "  /pantry [digest|<id>] - contrôles interactifs du garde-manger\n"
            "  /add <texte libre> - proposer de nouveaux articles en langage naturel.\n"
            "      Répond avec un diff par article ; appuyez sur Appliquer ou Annuler.\n"
            "      Les propositions expirent après 10 min.\n"
            "  /ate <id> - marquer comme mangé\n"
            "  /toss <id> - marquer comme jeté\n"
            "  /snooze <id> [days=2] - suspendre les rappels 1..30j\n"
            "  /correct <id> <texte libre> - proposer une correction en langage\n"
            "      naturel (nom, catégorie, expiration, jours). Répond avec un diff ;\n"
            "      appuyez sur Appliquer ou Annuler. Expire après 10 min.\n"
            "  /delete <id> - supprimer une importation erronée/dupliquée\n"
            "  /stats - 30 derniers jours\n"
            "  /llm [anthropic|openai|gemini|deepseek] - afficher ou changer de fournisseur LLM\n"
            "  /prefs [phrase] - afficher ou mettre à jour votre profil alimentaire\n"
            "  /cook - obtenir une recette depuis votre garde-manger\n"
            "  /history - afficher les repas cuisinés du foyer\n"
            "  /plan [3-7] - planifier les dîners de la semaine (5 jours par défaut)\n"
            "  /shopping - voir votre liste de courses ; appuyez sur un article acheté\n"
            "  /favorites - voir les recettes sauvegardées ; appuyez pour recuire\n"
            "  /invite [family] - inviter une personne (ou 'family' pour un lien réutilisable)\n"
            "  /join <code> - rejoindre un foyer où vous êtes invité\n"
            "  /household - lister les membres du foyer\n"
            "  /leave - quitter votre foyer\n"
            "  /remove <id> - (propriétaire) retirer un membre\n"
            "  /quota - afficher l'utilisation du foyer\n"
            "  /buy - choisir un forfait ou une recharge\n"
            "  /billing - afficher l'abonnement\n"
            "  /help - ce message\n"
            "Envoyez une photo de reçu pour l'enregistrer."
        ),
        "es": (
            "Comandos:\n"
            "  /start - estado de configuración\n"
            "  /tz <IANA> - establecer zona horaria\n"
            "  /lang [en|zh|fr|es] - establecer tu idioma\n"
            "  /digest_at <0..23> - hora del resumen diario\n"
            "  /list [category|week|expired] - mostrar despensa\n"
            "  /pantry [digest|<id>] - controles interactivos de despensa\n"
            "  /add <texto libre> - proponer nuevos artículos en lenguaje natural.\n"
            "      Responde con un diff por artículo; toca Aplicar o Cancelar.\n"
            "      Las propuestas expiran después de 10 min.\n"
            "  /ate <id> - marcar como comido\n"
            "  /toss <id> - marcar como tirado\n"
            "  /snooze <id> [days=2] - suspender recordatorios 1..30d\n"
            "  /correct <id> <texto libre> - proponer una corrección en lenguaje\n"
            "      natural (nombre, categoría, vencimiento, días). Responde con un\n"
            "      diff; toca Aplicar o Cancelar. Expira después de 10 min.\n"
            "  /delete <id> - eliminar una importación errónea/duplicada\n"
            "  /stats - últimos 30 días\n"
            "  /llm [anthropic|openai|gemini|deepseek] - mostrar o cambiar proveedor LLM\n"
            "  /prefs [frase] - mostrar o actualizar tu perfil alimentario\n"
            "  /cook - obtener una receta de tu despensa\n"
            "  /history - ver las comidas cocinadas del hogar\n"
            "  /plan [3-7] - planifica las cenas de la semana (5 días por defecto)\n"
            "  /shopping - ver tu lista de compras; toca un artículo comprado\n"
            "  /favorites - ver recetas guardadas; toca para volver a cocinar\n"
            "  /invite [family] - invitar a una persona (o 'family' para un enlace reutilizable)\n"
            "  /join <code> - unirte a un hogar al que te invitaron\n"
            "  /household - listar miembros del hogar\n"
            "  /leave - salir de tu hogar\n"
            "  /remove <id> - (propietario) quitar un miembro\n"
            "  /quota - ver el uso del hogar\n"
            "  /buy - elegir un plan o recarga\n"
            "  /billing - ver el estado de suscripción\n"
            "  /help - este mensaje\n"
            "Envía una foto de un recibo para registrarlo."
        ),
    },
    "help.overview": {
        "en": (
            "I track your groceries and remind you before they expire.\n\n"
            "Just message me:\n"
            "  \"bought milk and two avocados\"\n"
            "  \"ate the yogurt\"\n"
            "  \"how long does salmon keep?\"\n"
            "…or send a photo of a receipt.\n\n"
            "Core commands: /pantry · /cook · /help\n"
            "Pick a topic for the full command list:"
        ),
        "zh": (
            "我帮你追踪食品杂货，并在过期前提醒你。\n\n"
            "直接给我发消息：\n"
            "  “买了牛奶和两个牛油果”\n"
            "  “吃完了酸奶”\n"
            "  “三文鱼能放多久？”\n"
            "……或发送收据照片。\n\n"
            "核心命令：/pantry · /cook · /help\n"
            "选择一个主题查看完整命令列表："
        ),
        "fr": (
            "Je suis les courses et vous rappelle avant qu'elles n'expirent.\n\n"
            "Écrivez-moi simplement :\n"
            "  « acheté du lait et deux avocats »\n"
            "  « mangé le yaourt »\n"
            "  « combien de temps se conserve le saumon ? »\n"
            "…ou envoyez une photo d'un reçu.\n\n"
            "Commandes principales : /pantry · /cook · /help\n"
            "Choisissez un sujet pour la liste complète des commandes :"
        ),
        "es": (
            "Sigo tus compras y te aviso antes de que caduquen.\n\n"
            "Solo escríbeme:\n"
            "  \"compré leche y dos aguacates\"\n"
            "  \"me comí el yogur\"\n"
            "  \"¿cuánto dura el salmón?\"\n"
            "…o envía una foto de un recibo.\n\n"
            "Comandos principales: /pantry · /cook · /help\n"
            "Elige un tema para ver la lista completa de comandos:"
        ),
    },
    "help.topic.pantry": {
        "en": (
            "🥕 Pantry commands:\n"
            "  /list [category|week|expired] - show pantry\n"
            "  /pantry [digest|<id>] - interactive pantry controls\n"
            "  /add <free text> - propose new items in natural language\n"
            "  /ate <id> - mark eaten\n"
            "  /toss <id> - mark tossed\n"
            "  /snooze <id> [days=2] - suppress reminders 1..30d\n"
            "  /correct <id> <free text> - propose a correction\n"
            "  /delete <id> - remove a wrong/duplicate import\n"
            "  /stats - last 30 days"
        ),
        "zh": (
            "🥕 食品储藏命令：\n"
            "  /list [category|week|expired] - 显示食品储藏\n"
            "  /pantry [digest|<id>] - 交互式食品储藏管理\n"
            "  /add <自然语言> - 以自然语言提议添加食品\n"
            "  /ate <id> - 标记为已食用\n"
            "  /toss <id> - 标记为已丢弃\n"
            "  /snooze <id> [days=2] - 延后提醒 1..30 天\n"
            "  /correct <id> <自然语言> - 提议更正\n"
            "  /delete <id> - 移除错误/重复的记录\n"
            "  /stats - 最近30天统计"
        ),
        "fr": (
            "🥕 Commandes garde-manger :\n"
            "  /list [category|week|expired] - afficher le garde-manger\n"
            "  /pantry [digest|<id>] - contrôles interactifs\n"
            "  /add <texte libre> - proposer de nouveaux articles\n"
            "  /ate <id> - marquer comme mangé\n"
            "  /toss <id> - marquer comme jeté\n"
            "  /snooze <id> [days=2] - reporter les rappels 1..30j\n"
            "  /correct <id> <texte libre> - proposer une correction\n"
            "  /delete <id> - retirer un import erroné/en double\n"
            "  /stats - 30 derniers jours"
        ),
        "es": (
            "🥕 Comandos de despensa:\n"
            "  /list [category|week|expired] - mostrar despensa\n"
            "  /pantry [digest|<id>] - controles interactivos\n"
            "  /add <texto libre> - proponer nuevos artículos\n"
            "  /ate <id> - marcar como comido\n"
            "  /toss <id> - marcar como desechado\n"
            "  /snooze <id> [days=2] - posponer recordatorios 1..30d\n"
            "  /correct <id> <texto libre> - proponer una corrección\n"
            "  /delete <id> - quitar una importación errónea/duplicada\n"
            "  /stats - últimos 30 días"
        ),
    },
    "help.topic.cook": {
        "en": (
            "🍳 Cook commands:\n"
            "  /cook - get a recipe from your pantry\n"
            "  /plan [3-7] - plan dinners for the week (default 5 days)\n"
            "  /shopping - view your to-buy list; tap an item when bought\n"
            "  /favorites - view saved recipes; tap to re-cook against your pantry"
        ),
        "zh": (
            "🍳 做菜命令：\n"
            "  /cook - 根据食品储藏获取菜谱\n"
            "  /plan [3-7] - 规划本周晚餐（默认5天）\n"
            "  /shopping - 查看待购清单；购买后点击\n"
            "  /favorites - 查看已保存菜谱；点击可重新做菜"
        ),
        "fr": (
            "🍳 Commandes cuisine :\n"
            "  /cook - obtenir une recette à partir de votre garde-manger\n"
            "  /plan [3-7] - planifier les dîners de la semaine (5 jours par défaut)\n"
            "  /shopping - voir votre liste d'achats ; touchez un article une fois acheté\n"
            "  /favorites - voir les recettes enregistrées ; touchez pour recuisiner"
        ),
        "es": (
            "🍳 Comandos de cocina:\n"
            "  /cook - obtener una receta de tu despensa\n"
            "  /plan [3-7] - planifica las cenas de la semana (5 días por defecto)\n"
            "  /shopping - ver tu lista de compras; toca un artículo al comprarlo\n"
            "  /favorites - ver recetas guardadas; toca para volver a cocinar"
        ),
    },
    "help.topic.household": {
        "en": (
            "👪 Household commands:\n"
            "  /invite [family] - invite one person (or 'family' for a reusable link)\n"
            "  /join <code> - join a household you were invited to\n"
            "  /household - list household members\n"
            "  /leave - leave your household\n"
            "  /remove <id> - (owner) remove a member"
        ),
        "zh": (
            "👪 家庭命令：\n"
            "  /invite [family] - 邀请一人（或用 'family' 生成可重复使用的链接）\n"
            "  /join <code> - 加入被邀请的家庭\n"
            "  /household - 列出家庭成员\n"
            "  /leave - 离开你的家庭\n"
            "  /remove <id> - （所有者）移除成员"
        ),
        "fr": (
            "👪 Commandes foyer :\n"
            "  /invite [family] - inviter une personne (ou « family » pour un lien réutilisable)\n"
            "  /join <code> - rejoindre un foyer auquel vous avez été invité\n"
            "  /household - lister les membres du foyer\n"
            "  /leave - quitter votre foyer\n"
            "  /remove <id> - (propriétaire) retirer un membre"
        ),
        "es": (
            "👪 Comandos de hogar:\n"
            "  /invite [family] - invitar a una persona (o 'family' para un enlace reutilizable)\n"
            "  /join <code> - unirte a un hogar al que te invitaron\n"
            "  /household - listar miembros del hogar\n"
            "  /leave - salir de tu hogar\n"
            "  /remove <id> - (propietario) quitar un miembro"
        ),
    },
    "help.topic.settings": {
        "en": (
            "⚙️ Settings commands:\n"
            "  /tz <IANA> - set timezone\n"
            "  /lang [en|zh|fr|es] - set your language\n"
            "  /digest_at <0..23> - set digest hour\n"
            "  /llm [anthropic|openai|gemini|deepseek] - show or switch LLM provider\n"
            "  /prefs [sentence] - show or update your food profile"
        ),
        "zh": (
            "⚙️ 设置命令：\n"
            "  /tz <IANA> - 设置时区\n"
            "  /lang [en|zh|fr|es] - 设置语言\n"
            "  /digest_at <0..23> - 设置每日摘要时间\n"
            "  /llm [anthropic|openai|gemini|deepseek] - 查看或切换LLM提供商\n"
            "  /prefs [sentence] - 查看或更新你的饮食偏好"
        ),
        "fr": (
            "⚙️ Commandes de réglages :\n"
            "  /tz <IANA> - définir le fuseau horaire\n"
            "  /lang [en|zh|fr|es] - définir votre langue\n"
            "  /digest_at <0..23> - définir l'heure du résumé\n"
            "  /llm [anthropic|openai|gemini|deepseek] - afficher ou changer de fournisseur LLM\n"
            "  /prefs [sentence] - afficher ou mettre à jour votre profil alimentaire"
        ),
        "es": (
            "⚙️ Comandos de ajustes:\n"
            "  /tz <IANA> - establecer zona horaria\n"
            "  /lang [en|zh|fr|es] - establecer tu idioma\n"
            "  /digest_at <0..23> - establecer hora del resumen\n"
            "  /llm [anthropic|openai|gemini|deepseek] - mostrar o cambiar proveedor LLM\n"
            "  /prefs [sentence] - mostrar o actualizar tu perfil alimentario"
        ),
    },
    "btn.help.pantry": {"en": "🥕 Pantry", "zh": "🥕 储藏", "fr": "🥕 Garde-manger", "es": "🥕 Despensa"},
    "btn.help.cook": {"en": "🍳 Cook", "zh": "🍳 做菜", "fr": "🍳 Cuisiner", "es": "🍳 Cocinar"},
    "btn.help.household": {"en": "👪 Household", "zh": "👪 家庭", "fr": "👪 Foyer", "es": "👪 Hogar"},
    "btn.help.settings": {"en": "⚙️ Settings", "zh": "⚙️ 设置", "fr": "⚙️ Réglages", "es": "⚙️ Ajustes"},
    "btn.help.back": {"en": "⬅ Back", "zh": "⬅ 返回", "fr": "⬅ Retour", "es": "⬅ Atrás"},
    "start.tour": {
        "en": (
            "Here's how to use me:\n\n"
            "📸 Send a photo of a grocery receipt — I'll track everything on it.\n"
            "💬 Or just tell me things:\n"
            "  \"bought milk and two avocados\"\n"
            "  \"ate the yogurt\"\n"
            "  \"how long does salmon keep?\"\n\n"
            "🌅 Every morning I'll send a digest of what's expiring.\n"
            "Useful commands: /pantry (what you have) · /cook (recipe from your pantry) · /help (everything else)"
        ),
        "zh": (
            "使用方法：\n\n"
            "📸 发送一张购物收据照片——我会记录上面的所有食品。\n"
            "💬 或者直接告诉我：\n"
            "  “买了牛奶和两个牛油果”\n"
            "  “吃完了酸奶”\n"
            "  “三文鱼能放多久？”\n\n"
            "🌅 我每天早上会发送一份即将过期的摘要。\n"
            "常用命令：/pantry（你有什么）· /cook（根据你的食品储藏推荐菜谱）· /help（其他所有命令）"
        ),
        "fr": (
            "Voici comment m'utiliser :\n\n"
            "📸 Envoyez une photo d'un reçu d'épicerie — je suivrai tout ce qui s'y trouve.\n"
            "💬 Ou dites-moi simplement :\n"
            "  « acheté du lait et deux avocats »\n"
            "  « mangé le yaourt »\n"
            "  « combien de temps se conserve le saumon ? »\n\n"
            "🌅 Chaque matin, je vous enverrai un résumé de ce qui expire.\n"
            "Commandes utiles : /pantry (ce que vous avez) · /cook (recette à partir de votre garde-manger) · /help (le reste)"
        ),
        "es": (
            "Así es como usarme:\n\n"
            "📸 Envía una foto de un recibo de compras — rastrearé todo lo que contenga.\n"
            "💬 O simplemente dime cosas:\n"
            "  \"compré leche y dos aguacates\"\n"
            "  \"me comí el yogur\"\n"
            "  \"¿cuánto dura el salmón?\"\n\n"
            "🌅 Cada mañana te enviaré un resumen de lo que está por caducar.\n"
            "Comandos útiles: /pantry (lo que tienes) · /cook (receta de tu despensa) · /help (todo lo demás)"
        ),
    },
    "start.ready": {
        "en": (
            "Pantry bot ready.\n"
            "Timezone: {tz} (change with /tz <IANA>)\n"
            "Daily digest hour: {digest_hour}:00 "
            "(change with /digest_at <0..23>)\n"
            "Type /help to see all commands."
        ),
        "zh": (
            "食品储藏机器人已就绪。\n"
            "时区：{tz}（使用 /tz <IANA> 更改）\n"
            "每日摘要时间：{digest_hour}:00（使用 /digest_at <0..23> 更改）\n"
            "输入 /help 查看所有命令。"
        ),
        "fr": (
            "Bot garde-manger prêt.\n"
            "Fuseau horaire : {tz} (changez avec /tz <IANA>)\n"
            "Heure du résumé quotidien : {digest_hour}:00 "
            "(changez avec /digest_at <0..23>)\n"
            "Tapez /help pour voir toutes les commandes."
        ),
        "es": (
            "Bot de despensa listo.\n"
            "Zona horaria: {tz} (cambia con /tz <IANA>)\n"
            "Hora del resumen diario: {digest_hour}:00 "
            "(cambia con /digest_at <0..23>)\n"
            "Escribe /help para ver todos los comandos."
        ),
    },
    # -----------------------------------------------------------------------
    # Localized confirmations (Task 19 Part C)
    # -----------------------------------------------------------------------
    "prefs.updated": {
        "en": "Updated.",
        "zh": "已更新。",
        "fr": "Mis à jour.",
        "es": "Actualizado.",
    },
    "digest.pantry_clear": {
        "en": "Pantry is clear for the next 7 days.",
        "zh": "未来7天食品储藏无到期提醒。",
        "fr": "Le garde-manger est vide pour les 7 prochains jours.",
        "es": "La despensa está despejada para los próximos 7 días.",
    },
    "pantry.all_clear": {
        "en": "Your pantry is clear.",
        "zh": "您的食品储藏室已清空。",
        "fr": "Votre garde-manger est vide.",
        "es": "Tu despensa está vacía.",
    },
    "pantry.usage": {
        "en": "usage: /pantry [digest|<id>]",
        "zh": "用法：/pantry [digest|<id>]",
        "fr": "usage : /pantry [digest|<id>]",
        "es": "uso: /pantry [digest|<id>]",
    },
    "pantry.no_item": {
        "en": "no item #{id}",
        "zh": "未找到项目 #{id}",
        "fr": "aucun article #{id}",
        "es": "no hay artículo #{id}",
    },
    "pantry.item_inactive": {
        "en": "#{id} is {status}; cannot manage",
        "zh": "#{id} 状态为 {status}；无法管理",
        "fr": "#{id} est {status} ; impossible de le gérer",
        "es": "#{id} está {status}; no se puede gestionar",
    },
    # -----------------------------------------------------------------------
    # Household invites & membership (v4.2)
    # -----------------------------------------------------------------------
    "invite.created": {
        "en": (
            "Invite created - single use, expires in 24h.\n\n"
            "Tap to join: {link}\n"
            "Or send this to /join: {code}\n\n"
            "Whoever joins shares this household's pantry, shopping list, and preferences."
        ),
        "zh": (
            "邀请已创建 - 一次性使用，24小时后过期。\n\n"
            "点击加入：{link}\n"
            "或将此发送给 /join：{code}\n\n"
            "加入者将共享此家庭的食品储藏、购物清单和偏好设置。"
        ),
        "fr": (
            "Invitation créée - usage unique, expire dans 24 h.\n\n"
            "Appuyez pour rejoindre : {link}\n"
            "Ou envoyez ceci à /join : {code}\n\n"
            "Qui rejoint partage le garde-manger, la liste de courses et les préférences de ce foyer."
        ),
        "es": (
            "Invitación creada - un solo uso, caduca en 24 h.\n\n"
            "Toca para unirte: {link}\n"
            "O envía esto a /join: {code}\n\n"
            "Quien se una comparte la despensa, la lista de compras y las preferencias de este hogar."
        ),
    },
    "join.success": {
        "en": "You've joined the household. You now share its pantry, shopping list, and preferences. Type /help to get started.",
        "zh": "您已加入家庭。现在您将共享其食品储藏、购物清单和偏好设置。输入 /help 开始使用。",
        "fr": "Vous avez rejoint le foyer. Vous partagez désormais son garde-manger, sa liste de courses et ses préférences. Tapez /help pour commencer.",
        "es": "Te has unido al hogar. Ahora compartes su despensa, lista de compras y preferencias. Escribe /help para empezar.",
    },
    "join.invalid": {
        "en": "That invite is invalid, expired, or already used. Ask a household member for a fresh /invite.",
        "zh": "该邀请无效、已过期或已被使用。请向家庭成员索取新的 /invite。",
        "fr": "Cette invitation est invalide, expirée ou déjà utilisée. Demandez un nouveau /invite à un membre du foyer.",
        "es": "Esa invitación no es válida, caducó o ya se usó. Pide un nuevo /invite a un miembro del hogar.",
    },
    "join.already_member": {
        "en": "You're already in a household. Use /leave first if you want to switch.",
        "zh": "您已在一个家庭中。如需切换，请先使用 /leave。",
        "fr": "Vous êtes déjà dans un foyer. Utilisez d'abord /leave pour en changer.",
        "es": "Ya estás en un hogar. Usa /leave primero si quieres cambiarte.",
    },
    "household.title": {
        "en": "Household members ({n}):",
        "zh": "家庭成员（{n}）：",
        "fr": "Membres du foyer ({n}) :",
        "es": "Miembros del hogar ({n}):",
    },
    "household.role.owner": {"en": "owner", "zh": "所有者", "fr": "propriétaire", "es": "propietario"},
    "household.role.member": {"en": "member", "zh": "成员", "fr": "membre", "es": "miembro"},
    "household.you": {"en": " (you)", "zh": "（您）", "fr": " (vous)", "es": " (tú)"},
    "leave.success": {
        "en": "You've left the household and no longer have access. Ask a member for a new /invite to rejoin.",
        "zh": "您已离开家庭，不再拥有访问权限。如需重新加入，请向成员索取新的 /invite。",
        "fr": "Vous avez quitté le foyer et n'y avez plus accès. Demandez un nouveau /invite à un membre pour le rejoindre.",
        "es": "Has salido del hogar y ya no tienes acceso. Pide a un miembro un nuevo /invite para volver a unirte.",
    },
    "leave.owner": {
        "en": "You're the household owner - you can't leave. Use /remove <id> to remove members.",
        "zh": "您是家庭所有者 - 无法离开。请使用 /remove <id> 移除成员。",
        "fr": "Vous êtes le propriétaire du foyer - vous ne pouvez pas partir. Utilisez /remove <id> pour retirer des membres.",
        "es": "Eres el propietario del hogar - no puedes salir. Usa /remove <id> para quitar miembros.",
    },
    "remove.success": {
        "en": "Removed member {id} from your household.",
        "zh": "已从您的家庭中移除成员 {id}。",
        "fr": "Membre {id} retiré de votre foyer.",
        "es": "Se eliminó al miembro {id} de tu hogar.",
    },
    "remove.not_owner": {
        "en": "Only the household owner can remove members.",
        "zh": "只有家庭所有者才能移除成员。",
        "fr": "Seul le propriétaire du foyer peut retirer des membres.",
        "es": "Solo el propietario del hogar puede quitar miembros.",
    },
    "remove.self": {
        "en": "You can't remove yourself.",
        "zh": "您不能移除自己。",
        "fr": "Vous ne pouvez pas vous retirer vous-même.",
        "es": "No puedes eliminarte a ti mismo.",
    },
    "remove.not_found": {
        "en": "No member with id {id} in your household.",
        "zh": "您的家庭中没有 id 为 {id} 的成员。",
        "fr": "Aucun membre avec l'id {id} dans votre foyer.",
        "es": "No hay ningún miembro con id {id} en tu hogar.",
    },
    "invite.created_reusable": {
        "en": (
            "Reusable invite created - anyone can join until it expires in 24h.\n\n"
            "Tap to join: {link}\n"
            "Or send this to /join: {code}\n\n"
            "Everyone who joins shares this household's pantry, shopping list, and preferences."
        ),
        "zh": (
            "可重复使用的邀请已创建 - 24小时内任何人都可加入。\n\n"
            "点击加入：{link}\n"
            "或将此发送给 /join：{code}\n\n"
            "每位加入者都将共享此家庭的食品储藏、购物清单和偏好设置。"
        ),
        "fr": (
            "Invitation réutilisable créée - tout le monde peut rejoindre avant son expiration dans 24 h.\n\n"
            "Appuyez pour rejoindre : {link}\n"
            "Ou envoyez ceci à /join : {code}\n\n"
            "Tous ceux qui rejoignent partagent le garde-manger, la liste de courses et les préférences de ce foyer."
        ),
        "es": (
            "Invitación reutilizable creada - cualquiera puede unirse hasta que caduque en 24 h.\n\n"
            "Toca para unirte: {link}\n"
            "O envía esto a /join: {code}\n\n"
            "Todos los que se unan comparten la despensa, la lista de compras y las preferencias de este hogar."
        ),
    },
    "household.member_joined": {
        "en": "A new member (id {id}) joined your household.",
        "zh": "新成员（id {id}）已加入您的家庭。",
        "fr": "Un nouveau membre (id {id}) a rejoint votre foyer.",
        "es": "Un nuevo miembro (id {id}) se unió a tu hogar.",
    },
    "cooked.header": {
        "en": "🍳 Cooked: {dish}\n\nWhich did you use up?",
        "zh": "🍳 已做：{dish}\n\n用完了哪些？",
        "fr": "🍳 Cuisiné : {dish}\n\nQu'avez-vous utilisé ?",
        "es": "🍳 Cocinado: {dish}\n\n¿Qué usaste?",
    },
    "cooked.empty": {
        "en": "🍳 Cooked: {dish}\n\nNothing in your pantry matched this recipe.",
        "zh": "🍳 已做：{dish}\n\n库存中没有与此食谱匹配的食材。",
        "fr": "🍳 Cuisiné : {dish}\n\nRien dans votre garde-manger ne correspond à cette recette.",
        "es": "🍳 Cocinado: {dish}\n\nNada en tu despensa coincide con esta receta.",
    },
    "cooked.done": {
        "en": "🍳 Marked cooked. Ate: {names}",
        "zh": "🍳 已记录。吃掉：{names}",
        "fr": "🍳 Enregistré. Consommé : {names}",
        "es": "🍳 Registrado. Comido: {names}",
    },
    "cooked.done_none": {
        "en": "🍳 Marked cooked. Pantry unchanged.",
        "zh": "🍳 已记录。库存未变。",
        "fr": "🍳 Enregistré. Garde-manger inchangé.",
        "es": "🍳 Registrado. Despensa sin cambios.",
    },
    "btn.cooked.confirm": {
        "en": "Confirm",
        "zh": "确认",
        "fr": "Confirmer",
        "es": "Confirmar",
    },
    "btn.cooked.none": {
        "en": "Nothing used",
        "zh": "没有用完",
        "fr": "Rien utilisé",
        "es": "Nada usado",
    },
    # -----------------------------------------------------------------------
    # Callback toasts + inline body text (cook/actions/pending callbacks)
    # -----------------------------------------------------------------------
    "toast.unrecognized_action": {
        "en": "unrecognized action",
        "zh": "无法识别的操作",
        "fr": "action non reconnue",
        "es": "acción no reconocida",
    },
    "toast.cook_expired": {
        "en": "this cook session expired - start a new /cook",
        "zh": "此次做菜已过期 - 请重新 /cook",
        "fr": "cette session de cuisine a expiré - relancez /cook",
        "es": "esta sesión de cocina expiró - inicia un nuevo /cook",
    },
    "toast.cook_in_progress": {
        "en": "cook is still in progress",
        "zh": "仍在处理中",
        "fr": "la recherche est encore en cours",
        "es": "la cocina sigue en curso",
    },
    "toast.already_cooking": {
        "en": "already cooking",
        "zh": "已在处理中",
        "fr": "déjà en cours",
        "es": "ya en curso",
    },
    "toast.already_searching": {
        "en": "already searching",
        "zh": "已在搜索中",
        "fr": "recherche déjà en cours",
        "es": "ya buscando",
    },
    "toast.already_answered": {
        "en": "already answered",
        "zh": "已回答",
        "fr": "déjà répondu",
        "es": "ya respondido",
    },
    "toast.showing_alternatives": {
        "en": "showing alternatives",
        "zh": "正在显示替代方案",
        "fr": "affichage des alternatives",
        "es": "mostrando alternativas",
    },
    "toast.no_household_profile": {
        "en": "couldn't load your household profile",
        "zh": "无法加载您的家庭档案",
        "fr": "impossible de charger le profil de votre foyer",
        "es": "no se pudo cargar el perfil de tu hogar",
    },
    "toast.cook_update_failed": {
        "en": "couldn't update this cook session - try /cook again",
        "zh": "无法更新此次做菜会话 - 请重试 /cook",
        "fr": "impossible de mettre à jour cette session de cuisine - réessayez /cook",
        "es": "no se pudo actualizar esta sesión de cocina - intenta /cook de nuevo",
    },
    "toast.liked": {
        "en": "got it 👍",
        "zh": "好的 👍",
        "fr": "noté 👍",
        "es": "anotado 👍",
    },
    "toast.disliked": {
        "en": "noted 👎",
        "zh": "已记录 👎",
        "fr": "noté 👎",
        "es": "anotado 👎",
    },
    "toast.nothing_to_use": {
        "en": "nothing to use here",
        "zh": "此处无可用内容",
        "fr": "rien à utiliser ici",
        "es": "nada que usar aquí",
    },
    "toast.already_saved": {
        "en": "already saved",
        "zh": "已保存",
        "fr": "déjà sauvegardé",
        "es": "ya guardado",
    },
    "toast.saved": {
        "en": "saved ★",
        "zh": "已保存 ★",
        "fr": "sauvegardé ★",
        "es": "guardado ★",
    },
    "toast.added_to_shopping": {
        "en": "added {n} to shopping list",
        "zh": "已将 {n} 项加入购物清单",
        "fr": "{n} article(s) ajouté(s) à la liste de courses",
        "es": "se añadieron {n} a la lista de compras",
    },
    "toast.already_on_list": {
        "en": "already on your list",
        "zh": "已在您的清单中",
        "fr": "déjà sur votre liste",
        "es": "ya está en tu lista",
    },
    "toast.have_everything": {
        "en": "you have everything!",
        "zh": "您已备齐所有食材！",
        "fr": "vous avez déjà tout !",
        "es": "¡ya tienes todo!",
    },
    "toast.bought": {
        "en": "bought ✓",
        "zh": "已购买 ✓",
        "fr": "acheté ✓",
        "es": "comprado ✓",
    },
    "toast.already_done": {
        "en": "already done",
        "zh": "已完成",
        "fr": "déjà fait",
        "es": "ya hecho",
    },
    "toast.not_found": {
        "en": "not found",
        "zh": "未找到",
        "fr": "introuvable",
        "es": "no encontrado",
    },
    "toast.heres_plan": {
        "en": "here's the plan",
        "zh": "计划如下",
        "fr": "voici le plan",
        "es": "aquí está el plan",
    },
    "toast.nothing_due": {
        "en": "nothing due",
        "zh": "没有即将到期的项目",
        "fr": "rien à échéance",
        "es": "nada por vencer",
    },
    "toast.undone": {
        "en": "undone",
        "zh": "已撤销",
        "fr": "annulé",
        "es": "deshecho",
    },
    "toast.nothing_undone": {
        "en": "nothing undone",
        "zh": "没有可撤销的操作",
        "fr": "rien à annuler",
        "es": "nada deshecho",
    },
    "toast.item_not_found": {
        "en": "item not found",
        "zh": "未找到该项目",
        "fr": "article introuvable",
        "es": "artículo no encontrado",
    },
    "toast.item_updated": {
        "en": "#{id} -> {action}",
        "zh": "#{id} -> {action}",
        "fr": "#{id} -> {action}",
        "es": "#{id} -> {action}",
    },
    "toast.item_already_updated": {
        "en": "#{id} already updated",
        "zh": "#{id} 已更新",
        "fr": "#{id} déjà mis à jour",
        "es": "#{id} ya actualizado",
    },
    "toast.already_status": {
        "en": "already {status}",
        "zh": "已{status}",
        "fr": "déjà {status}",
        "es": "ya {status}",
    },
    "toast.cancelled": {
        "en": "cancelled",
        "zh": "已取消",
        "fr": "annulé",
        "es": "cancelado",
    },
    "toast.item_gone": {
        "en": "item gone",
        "zh": "项目已不存在",
        "fr": "article disparu",
        "es": "artículo ya no existe",
    },
    "toast.item_inactive": {
        "en": "item no longer active",
        "zh": "项目已不再有效",
        "fr": "article n'est plus actif",
        "es": "el artículo ya no está activo",
    },
    "toast.applied": {
        "en": "applied",
        "zh": "已应用",
        "fr": "appliqué",
        "es": "aplicado",
    },
    "toast.unknown_action": {
        "en": "unknown action",
        "zh": "未知操作",
        "fr": "action inconnue",
        "es": "acción desconocida",
    },
    "toast.added": {
        "en": "added",
        "zh": "已添加",
        "fr": "ajouté",
        "es": "agregado",
    },
    "pending.item_gone_body": {
        "en": "Item gone - proposal cancelled.",
        "zh": "项目已不存在 - 提案已取消。",
        "fr": "Article disparu - proposition annulée.",
        "es": "El artículo ya no existe - propuesta cancelada.",
    },
    "pending.item_inactive_body": {
        "en": "Item is no longer active - proposal cancelled.",
        "zh": "项目已不再有效 - 提案已取消。",
        "fr": "L'article n'est plus actif - proposition annulée.",
        "es": "El artículo ya no está activo - propuesta cancelada.",
    },
    "cook.which_cuisine": {
        "en": "Which cuisine?",
        "zh": "选择菜系？",
        "fr": "Quelle cuisine ?",
        "es": "¿Qué cocina?",
    },
    "cook.thinking": {
        "en": "Thinking...",
        "zh": "思考中…",
        "fr": "Réflexion…",
        "es": "Pensando…",
    },
    "cook.fetch_more_failed": {
        "en": "Couldn't fetch more recipes right now - try again.",
        "zh": "暂时无法获取更多菜谱 - 请重试。",
        "fr": "Impossible de récupérer plus de recettes pour le moment - réessayez.",
        "es": "No se pudieron obtener más recetas ahora - inténtalo de nuevo.",
    },
    "cook.what_cooking": {
        "en": "What are you cooking?",
        "zh": "您想做什么菜？",
        "fr": "Que cuisinez-vous ?",
        "es": "¿Qué vas a cocinar?",
    },
    "cook.no_profile_body": {
        "en": "Couldn't load your household profile - try /cook again.",
        "zh": "无法加载您的家庭档案 - 请重试 /cook。",
        "fr": "Impossible de charger le profil de votre foyer - réessayez /cook.",
        "es": "No se pudo cargar el perfil de tu hogar - intenta /cook de nuevo.",
    },
    "cook.not_enough_items": {
        "en": "Not enough usable items - send a receipt or /add a few things.",
        "zh": "可用食材不足 - 请发送收据或 /add 一些食品。",
        "fr": "Pas assez d'articles utilisables - envoyez un reçu ou /add quelques articles.",
        "es": "No hay suficientes artículos utilizables - envía un recibo o /add algunas cosas.",
    },
    "cook.build_failed": {
        "en": "Couldn't build a recipe right now - try /cook again.",
        "zh": "暂时无法生成菜谱 - 请重试 /cook。",
        "fr": "Impossible de créer une recette pour le moment - réessayez /cook.",
        "es": "No se pudo generar una receta ahora - intenta /cook de nuevo.",
    },
}


def t(key: str, lang: str, /, **kwargs: object) -> str:
    variants = MESSAGES[key]
    en_result = variants["en"].format(**kwargs)
    if lang == DEFAULT_LANG or lang not in variants:
        return en_result
    try:
        return variants[lang].format(**kwargs)
    except (KeyError, IndexError):
        log.warning("i18n_format_failed", extra={"key": key, "lang": lang})
        return en_result


_MONTH_ABBR: dict[str, tuple[str, ...]] = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "zh": ("1月", "2月", "3月", "4月", "5月", "6月",
           "7月", "8月", "9月", "10月", "11月", "12月"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "es": ("ene", "feb", "mar", "abr", "may", "jun",
           "jul", "ago", "sep", "oct", "nov", "dic"),
}

_WEEKDAY_ABBR: dict[str, tuple[str, ...]] = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "zh": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
}


def _months(lang: str) -> tuple[str, ...]:
    return _MONTH_ABBR.get(lang, _MONTH_ABBR["en"])


def weekday_abbr(value: date, *, lang: str) -> str:
    table = _WEEKDAY_ABBR.get(lang, _WEEKDAY_ABBR["en"])
    return table[value.weekday()]


def format_date(value: date, *, today: date, lang: str) -> str:
    base = f"{_months(lang)[value.month - 1]} {value.day}"
    if value.year != today.year:
        return f"{base} {value.year}"
    return base

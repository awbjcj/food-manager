from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

LANGS: tuple[str, ...] = ("en", "zh", "fr", "es")
DEFAULT_LANG = "en"

# Catalog. English is mandatory for every key; other languages are optional and
# fall back to English. Keys are added incrementally by later tasks.
MESSAGES: dict[str, dict[str, str]] = {
    "digest.section.expired": {"en": "Expired", "zh": "已过期", "fr": "Périmé", "es": "Caducado"},
    "digest.section.today": {"en": "Today", "zh": "今天", "fr": "Aujourd'hui", "es": "Hoy"},
    "digest.section.tomorrow": {"en": "Tomorrow", "zh": "明天", "fr": "Demain", "es": "Mañana"},
    "digest.section.this_week": {"en": "This week", "zh": "本周", "fr": "Cette semaine", "es": "Esta semana"},
    "digest.more": {
        "en": "... and {n} more - tap [show all]",
        "zh": "... 还有 {n} 项 - 点击 [show all]",
        "fr": "... et {n} de plus - appuyez sur [show all]",
        "es": "... y {n} más - toca [show all]",
    },
    "item.tail.today": {"en": "today", "zh": "今天", "fr": "aujourd'hui", "es": "hoy"},
    "item.tail.expired": {"en": "expired {n}d", "zh": "已过期 {n}天",
                          "fr": "périmé {n}j", "es": "caducado hace {n}d"},
    "item.tail.days": {"en": "({n}d)", "zh": "({n}天)", "fr": "({n}j)", "es": "({n}d)"},
    "list.empty": {"en": "no items match this filter"},
    "digest.title": {"en": "Pantry digest - {weekday} {date}",
                     "zh": "食品储藏提醒 - {weekday} {date}",
                     "fr": "Inventaire du garde-manger - {weekday} {date}",
                     "es": "Resumen de despensa - {weekday} {date}"},
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
        "en": "  Health {score}/100 - {effort} - {minutes} min",
        "zh": "  健康 {score}/100 - {effort} - {minutes} 分钟",
        "fr": "  Santé {score}/100 - {effort} - {minutes} min",
        "es": "  Salud {score}/100 - {effort} - {minutes} min",
    },
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
        "en": "Shopping list:",
        "zh": "购物清单：",
        "fr": "Liste de courses :",
        "es": "Lista de compras:",
    },
    "favorites.empty": {
        "en": "No saved recipes yet. Tap ★ Save on a /cook result.",
        "zh": "还没有保存的食谱。在 /cook 结果中点击 ★ Save。",
        "fr": "Aucune recette sauvegardée. Appuyez sur ★ Save dans un résultat /cook.",
        "es": "Aún no hay recetas guardadas. Toca ★ Save en un resultado de /cook.",
    },
    "favorites.title": {
        "en": "Saved recipes:",
        "zh": "已保存的食谱：",
        "fr": "Recettes sauvegardées :",
        "es": "Recetas guardadas:",
    },
    # -----------------------------------------------------------------------
    # Button labels (Task 15)
    # -----------------------------------------------------------------------
    "btn.ate": {"en": "Ate", "zh": "吃掉", "fr": "Mangé", "es": "Comido"},
    "btn.tossed": {"en": "Tossed", "zh": "扔掉", "fr": "Jeté", "es": "Tirado"},
    "btn.snooze2": {"en": "Remind +2d", "zh": "提醒 +2天", "fr": "Rappel +2j", "es": "Recordar +2d"},
    "btn.freeze": {"en": "❄️ Freeze", "zh": "❄️ 冷冻", "fr": "❄️ Congeler", "es": "❄️ Congelar"},
    "btn.show_all": {"en": "show all", "zh": "显示全部", "fr": "tout afficher", "es": "ver todo"},
    "btn.undo": {"en": "Undo", "zh": "撤销", "fr": "Annuler", "es": "Deshacer"},
    "btn.apply": {"en": "Apply", "zh": "应用", "fr": "Appliquer", "es": "Aplicar"},
    "btn.cancel": {"en": "Cancel", "zh": "取消", "fr": "Annuler", "es": "Cancelar"},
    "btn.liked": {"en": "👍 Liked", "zh": "👍 喜欢", "fr": "👍 Aimé", "es": "👍 Me gusta"},
    "btn.disliked": {"en": "👎 Not for me", "zh": "👎 不喜欢", "fr": "👎 Pas pour moi", "es": "👎 No es para mí"},
    "btn.save": {"en": "★ Save", "zh": "★ 保存", "fr": "★ Sauvegarder", "es": "★ Guardar"},
    "btn.shopping": {"en": "➕ Shopping list", "zh": "➕ 购物清单", "fr": "➕ Liste de courses", "es": "➕ Lista de compras"},
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
        "en": "LLM spend: total {total_cost}  avg {avg_cost} / receipt",
        "zh": "LLM 花费：总计 {total_cost}  平均 {avg_cost} / 收据",
        "fr": "Dépense LLM : total {total_cost}  moy. {avg_cost} / reçu",
        "es": "Gasto LLM: total {total_cost}  prom. {avg_cost} / recibo",
    },
    "stats.corrections": {
        "en": "  Corrections: {count}  (${cost_total} total{unknown})",
        "zh": "  更正：{count}  （${cost_total} 合计{unknown}）",
        "fr": "  Corrections : {count}  ({cost_total} $ total{unknown})",
        "es": "  Correcciones: {count}  (${cost_total} total{unknown})",
    },
    "stats.adds": {
        "en": "  Adds:        {count}  (${cost_total} total{unknown})",
        "zh": "  添加：        {count}  （${cost_total} 合计{unknown}）",
        "fr": "  Ajouts :     {count}  ({cost_total} $ total{unknown})",
        "es": "  Agregar:     {count}  (${cost_total} total{unknown})",
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
            "  /llm [anthropic|openai] - show or switch LLM provider\n"
            "  /prefs [sentence] - show or update your food profile\n"
            "  /cook - get a recipe from your pantry\n"
            "  /shopping - view your to-buy list; tap an item when bought\n"
            "  /favorites - view saved recipes; tap to re-cook against your pantry\n"
            "  /invite [family] - invite one person (or 'family' for a reusable link)\n"
            "  /join <code> - join a household you were invited to\n"
            "  /household - list household members\n"
            "  /leave - leave your household\n"
            "  /remove <id> - (owner) remove a member\n"
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
            "  /llm [anthropic|openai] - 显示或切换LLM提供商\n"
            "  /prefs [语句] - 显示或更新您的饮食档案\n"
            "  /cook - 从您的储藏获取食谱\n"
            "  /shopping - 查看待购清单；点击已购买的物品\n"
            "  /favorites - 查看已保存食谱；点击重新烹饪\n"
            "  /invite [family] - 邀请一人（或用 'family' 生成可重复使用的链接）\n"
            "  /join <code> - 加入您被邀请的家庭\n"
            "  /household - 列出家庭成员\n"
            "  /leave - 离开您的家庭\n"
            "  /remove <id> -（所有者）移除成员\n"
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
            "  /llm [anthropic|openai] - afficher ou changer de fournisseur LLM\n"
            "  /prefs [phrase] - afficher ou mettre à jour votre profil alimentaire\n"
            "  /cook - obtenir une recette depuis votre garde-manger\n"
            "  /shopping - voir votre liste de courses ; appuyez sur un article acheté\n"
            "  /favorites - voir les recettes sauvegardées ; appuyez pour recuire\n"
            "  /invite [family] - inviter une personne (ou 'family' pour un lien réutilisable)\n"
            "  /join <code> - rejoindre un foyer où vous êtes invité\n"
            "  /household - lister les membres du foyer\n"
            "  /leave - quitter votre foyer\n"
            "  /remove <id> - (propriétaire) retirer un membre\n"
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
            "  /llm [anthropic|openai] - mostrar o cambiar proveedor LLM\n"
            "  /prefs [frase] - mostrar o actualizar tu perfil alimentario\n"
            "  /cook - obtener una receta de tu despensa\n"
            "  /shopping - ver tu lista de compras; toca un artículo comprado\n"
            "  /favorites - ver recetas guardadas; toca para volver a cocinar\n"
            "  /invite [family] - invitar a una persona (o 'family' para un enlace reutilizable)\n"
            "  /join <code> - unirte a un hogar al que te invitaron\n"
            "  /household - listar miembros del hogar\n"
            "  /leave - salir de tu hogar\n"
            "  /remove <id> - (propietario) quitar un miembro\n"
            "  /help - este mensaje\n"
            "Envía una foto de un recibo para registrarlo."
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

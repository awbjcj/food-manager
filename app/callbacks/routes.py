from __future__ import annotations

from app.callback_dispatch import CallbackResult
from app.callbacks.actions import handle_callback
from app.callbacks.cook import handle_cook_callback
from app.callbacks.help import handle_help_callback
from app.callbacks.items import handle_item_callback
from app.callbacks.plan import handle_plan_callback
from app.callbacks import EXPECTED_CALLBACK_ROUTES
from app.callbacks.registry import CallbackRegistry


def build_callback_registry() -> CallbackRegistry:
    registry = CallbackRegistry()

    @registry.register("help")
    async def help_route(_request, context):
        async def run():
            await handle_help_callback(
                context.callback, session_factory=context.session_factory
            )
            return None

        return CallbackResult(deferred=run)

    @registry.register("plan_swap", "plan_shop", "plan_cancel")
    async def plan_route(_request, context):
        async def run():
            await handle_plan_callback(
                context.callback,
                session_factory=context.session_factory,
                now_provider=context.now_provider,
                clients=context.clients,
                recipe_sources=context.recipe_sources,
                translation_llm=context.translation_llm,
            )
            return None

        return CallbackResult(deferred=run)

    @registry.register(
        "cook_pick", "cook_alt", "cook_more", "cook_adjust", "cook_more_opts"
    )
    async def cook_route(_request, context):
        if not context.clients.cook_configured:
            return CallbackResult(ack="cook is not configured yet")

        async def run():
            await handle_cook_callback(
                context.callback,
                session_factory=context.session_factory,
                now_provider=context.now_provider,
                clients=context.clients,
                spawn=context.spawn,
                bot=context.bot,
                translation_llm=context.translation_llm,
                recipe_sources=context.recipe_sources,
            )
            return None

        return CallbackResult(deferred=run)

    @registry.register(
        "item_open",
        "item_list",
        "item_corr",
        "item_nudge",
        "item_ctext",
        "item_rm",
        "item_rmok",
    )
    async def item_route(_request, context):
        async def run():
            await handle_item_callback(
                context.callback,
                session_factory=context.session_factory,
                now_provider=context.now_provider,
                translation_llm=context.translation_llm,
            )
            return None

        return CallbackResult(deferred=run)

    @registry.register(
        "ate",
        "toss",
        "snooze2",
        "freeze",
        "fridge",
        "show_all",
        "apply",
        "cancel",
        "undo_receipt",
        "undo_add",
        "cook_like",
        "cook_dislike",
        "cook_save",
        "cook_shop",
        "shop_done",
        "fav_cook",
    )
    async def action_route(_request, context):
        async def run():
            await handle_callback(
                context.callback,
                session_factory=context.session_factory,
                now_provider=context.now_provider,
                clients=context.clients,
                translation_llm=context.translation_llm,
            )
            return None

        return CallbackResult(deferred=run)

    assert registry.routes == EXPECTED_CALLBACK_ROUTES
    return registry

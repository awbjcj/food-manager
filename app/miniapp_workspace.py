"""In-app presentation of the canonical command and callback workflows.

The browser never supplies Telegram identities, callback payloads, or reply text.
It selects buttons on server-owned cards. Domain handlers retain responsibility
for authorization, household scoping, quotas, and confirmation transactions.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web

from app.bot import _MESSAGE_COMMANDS, _QUICK_ACCESS_COMMANDS
from app.callback_dispatch import apply
from app.callbacks import CallbackContext
from app.callbacks.routes import build_callback_registry
from app.commands import parse_callback_request
from app.handlers.meta import handle_nl_message, handle_photo
from app.handlers.pantry import handle_correct_reply
from app.handlers.plan import handle_plan_current
from app.i18n import t

log = logging.getLogger(__name__)
COMMANDS = {name: (handler, deps) for name, handler, deps in _MESSAGE_COMMANDS}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass
class Workspace:
    user_id: int
    household_id: int | None
    lang: str = "en"
    id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    cards: dict[int, Any] = field(default_factory=dict)
    task: asyncio.Task | None = None
    notices: list[str] = field(default_factory=list)
    error: str | None = None
    touched: float = field(default_factory=time.monotonic)
    requests: set[str] = field(default_factory=set)

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def snapshot(self):
        return {
            "id": self.id,
            "busy": self.busy,
            "cards": [card.payload() for card in self.cards.values()],
            "notices": self.notices,
            "error": self.error,
        }


class AppMessage:
    def __init__(self, transport, *, text="", message_id=None):
        self.bot = transport
        self.from_user = SimpleNamespace(id=transport.workspace.user_id)
        self.chat = SimpleNamespace(id=transport.workspace.user_id, type="private")
        # Negative, random IDs cannot collide with real Telegram message IDs.
        self.message_id = message_id or -secrets.randbits(52)
        self.text = text
        self.reply_markup = None
        self.reply_to_message = None
        self.photo = []
        self.document = None
        self.revision = secrets.token_urlsafe(12)

    def model_copy(self, *, update):
        result = copy(self)
        for key, value in update.items():
            setattr(result, key, value)
        return result

    async def answer(self, text, reply_markup=None, **_kwargs):
        result = AppMessage(self.bot, text=text)
        result.reply_markup = reply_markup
        self.bot.workspace.cards[result.message_id] = result
        return result

    async def edit_text(self, text, reply_markup=None, **_kwargs):
        self.text = text
        self.reply_markup = reply_markup
        self.revision = secrets.token_urlsafe(12)
        return self

    async def edit_reply_markup(self, reply_markup=None, **_kwargs):
        self.reply_markup = reply_markup
        self.revision = secrets.token_urlsafe(12)
        return self

    async def delete(self):
        self.bot.workspace.cards.pop(self.message_id, None)

    async def answer_document(self, document, caption="", **_kwargs):
        result = await self.answer(caption)
        result.document = {
            "name": document.filename,
            "data": base64.b64encode(document.data).decode("ascii"),
        }
        return result

    def payload(self):
        keyboard = getattr(self.reply_markup, "inline_keyboard", [])
        return {
            "id": self.message_id,
            "text": self.text,
            "buttons": [
                [
                    {
                        "text": button.text,
                        "url": button.url,
                        "action": f"{self.revision}:{row_index}:{index}"
                        if button.callback_data
                        else None,
                    }
                    for index, button in enumerate(row)
                    if button.callback_data or button.url
                ]
                for row_index, row in enumerate(keyboard)
            ],
            "reply": bool(getattr(self.reply_markup, "force_reply", False)),
            "document": self.document,
        }


class AppTransport:
    def __init__(self, workspace, bot):
        self.workspace = workspace
        self.real_bot = bot

    async def get_me(self):
        return await self.real_bot.get_me()

    async def send_chat_action(self, **_kwargs):
        return True

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id == self.workspace.user_id:
            return await AppMessage(self).answer(text, **kwargs)
        # Existing household join notifications still go to the other members.
        return await self.real_bot.send_message(chat_id=chat_id, text=text, **kwargs)

    async def edit_message_text(self, *, chat_id, message_id, text, **kwargs):
        if chat_id != self.workspace.user_id or message_id not in self.workspace.cards:
            raise ValueError("card no longer available")
        return await self.workspace.cards[message_id].edit_text(text, **kwargs)


@dataclass
class AppCallback:
    data: str | None
    message: object | None
    from_user: Any
    workspace: Workspace

    async def answer(self, text="", *, show_alert=False):
        if text:
            self.workspace.notices.append(text)


class WorkspaceRuntime:
    def __init__(
        self,
        *,
        session_factory,
        clients,
        bot=None,
        reschedule=None,
        unschedule=None,
        translation_llm=None,
        recipe_sources=(),
        intent_agent=None,
        composer=None,
        payments=None,
        hosted_features_enabled=True,
    ):
        self.workspaces: dict[int, Workspace] = {}
        self.bot = bot
        self.deps = {
            "session_factory": session_factory,
            "clients": clients,
            "now_provider": lambda tz: datetime.now(ZoneInfo(tz)),
            "on_user_created": reschedule or (lambda _user: None),
            "reschedule": reschedule or (lambda _user: None),
            "unschedule": unschedule or (lambda _id: None),
            "translation_llm": translation_llm,
            "recipe_sources": recipe_sources,
            "intent_agent": intent_agent,
            "composer": composer,
            "payments": payments,
            "hosted_features_enabled": hosted_features_enabled,
        }

    def get(self, user_id, household_id, lang="en"):
        now = time.monotonic()
        for key, old in list(self.workspaces.items()):
            if not old.busy and now - old.touched > 3600:
                del self.workspaces[key]
        workspace = self.workspaces.get(user_id)
        if workspace is not None and workspace.household_id != household_id:
            # A leave/join must never reveal cards from the previous household.
            if workspace.busy and workspace.task is not None:
                workspace.task.cancel()
            workspace.cards.clear()
            workspace.notices.clear()
            workspace.household_id = household_id
        if workspace is None:
            if len(self.workspaces) >= 1000:
                raise web.HTTPServiceUnavailable(text=t("miniapp.capacity", lang))
            workspace = Workspace(user_id, household_id)
            self.workspaces[user_id] = workspace
        workspace.lang = lang
        workspace.touched = now
        return workspace

    async def close(self, _app):
        tasks = [
            space.task
            for space in self.workspaces.values()
            if space.task is not None and space.busy
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def submit(self, workspace, body, *, image=None):
        request_id = body.get("requestId")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise web.HTTPBadRequest(text=t("miniapp.request_id", workspace.lang))
        if body.get("workspaceId") != workspace.id:
            raise web.HTTPConflict(text=t("miniapp.expired", workspace.lang))
        if request_id in workspace.requests:
            return
        if workspace.busy:
            raise web.HTTPConflict(text=t("miniapp.busy", workspace.lang))
        kind = body.get("kind")
        if not isinstance(kind, str):
            raise web.HTTPBadRequest(text=t("miniapp.invalid_kind", workspace.lang))
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 4000:
            raise web.HTTPBadRequest(text=t("miniapp.text_length", workspace.lang))
        message = None
        callback_data = None
        if kind in {"callback", "reply"}:
            if type(body.get("cardId")) is not int:
                raise web.HTTPBadRequest(text=t("miniapp.invalid_card", workspace.lang))
            message = workspace.cards.get(body.get("cardId"))
            if message is None:
                raise web.HTTPConflict(text=t("miniapp.card_expired", workspace.lang))
            if kind == "callback":
                try:
                    revision, raw_row, raw_col = body["action"].split(":")
                    if revision != message.revision:
                        raise web.HTTPConflict(
                            text=t("miniapp.card_changed", workspace.lang)
                        )
                    row, col = int(raw_row), int(raw_col)
                    if row < 0 or col < 0:
                        raise ValueError
                    callback_data = message.reply_markup.inline_keyboard[row][
                        col
                    ].callback_data
                    if not callback_data:
                        raise ValueError
                except (
                    AttributeError,
                    KeyError,
                    IndexError,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise web.HTTPBadRequest(text=t("miniapp.invalid_action", workspace.lang)) from exc
            elif not getattr(message.reply_markup, "force_reply", False):
                raise web.HTTPBadRequest(text=t("miniapp.no_reply", workspace.lang))
        elif kind == "command":
            if not isinstance(body.get("command"), str) or body["command"] not in {
                *COMMANDS,
                "plan_current",
            }:
                raise web.HTTPBadRequest(text=t("miniapp.unknown_command", workspace.lang))
        elif kind == "photo":
            if image is None:
                raise web.HTTPBadRequest(text=t("miniapp.receipt_required", workspace.lang))
        elif kind != "text" or not text.strip():
            raise web.HTTPBadRequest(text=t("miniapp.invalid_request", workspace.lang))
        # Retain retry IDs for the lifetime of this workspace, with a hard bound.
        if len(workspace.requests) >= 2000:
            raise web.HTTPConflict(
                text=t("miniapp.limit", workspace.lang)
            )
        workspace.requests.add(request_id)
        workspace.notices = []
        workspace.error = None
        workspace.task = asyncio.create_task(
            self._run(workspace, body, message, callback_data, image)
        )

    async def _run(self, workspace, body, source_message, callback_data, image):
        children = []
        transport = AppTransport(workspace, self.bot)

        def spawn(coro):
            task = asyncio.create_task(coro)
            children.append(task)
            return task

        async def photo_downloader(_id):
            return image

        deps = {
            **self.deps,
            "bot": transport,
            "spawn": spawn,
            "photo_downloader": photo_downloader,
        }

        async def invoke(handler, names, event):
            await handler(event, **{name: deps[name] for name in names})

        async def quick_access(payload, event):
            route = _QUICK_ACCESS_COMMANDS.get(payload)
            if route is None:
                return False
            command, handler, names = route
            await invoke(
                handler, names, event.model_copy(update={"text": f"/{command}"})
            )
            return True

        deps["quick_access"] = quick_access
        event = AppMessage(transport, text=body.get("text", ""))
        try:
            kind = body["kind"]
            if kind == "command":
                command = body["command"]
                event.text = f"/{command} {event.text}".strip()
                if command == "bind":
                    if not self.deps["hosted_features_enabled"] or self.bot is None:
                        raise web.HTTPServiceUnavailable(
                            text=t("miniapp.binding_unavailable", workspace.lang)
                        )
                    group_id = int(body.get("text", ""))
                    chat = await self.bot.get_chat(group_id)
                    member = await self.bot.get_chat_member(group_id, workspace.user_id)
                    if chat.type not in {
                        "group",
                        "supergroup",
                    } or member.status not in {"creator", "administrator"}:
                        raise web.HTTPForbidden(
                            text=t("miniapp.group_admin", workspace.lang)
                        )
                    event.chat = SimpleNamespace(id=group_id, type=chat.type)
                if command == "plan_current":
                    await invoke(
                        handle_plan_current,
                        ("session_factory", "on_user_created", "translation_llm"),
                        event,
                    )
                else:
                    handler, names = COMMANDS[command]
                    await invoke(handler, names, event)
            elif kind == "callback":
                callback = AppCallback(
                    data=callback_data,
                    message=source_message,
                    from_user=event.from_user,
                    workspace=workspace,
                )
                context = CallbackContext(
                    callback=callback,
                    session_factory=deps["session_factory"],
                    now_provider=deps["now_provider"],
                    clients=deps["clients"],
                    translation_llm=deps["translation_llm"],
                    bot=transport,  # type: ignore[arg-type]
                    spawn=spawn,
                    recipe_sources=deps["recipe_sources"],
                )
                result = await build_callback_registry().dispatch(
                    parse_callback_request(callback_data), context
                )
                await apply(callback, result)
            elif kind == "reply":
                event.reply_to_message = source_message
                await invoke(
                    handle_correct_reply,
                    ("session_factory", "now_provider", "clients", "on_user_created"),
                    event,
                )
            elif kind == "photo":
                event.photo = [
                    SimpleNamespace(
                        file_id="miniapp:" + hashlib.sha256(image).hexdigest()
                    )
                ]
                await invoke(
                    handle_photo,
                    (
                        "session_factory",
                        "now_provider",
                        "clients",
                        "photo_downloader",
                        "on_user_created",
                        "spawn",
                        "bot",
                        "translation_llm",
                    ),
                    event,
                )
            elif deps["intent_agent"] is not None:
                await invoke(
                    handle_nl_message,
                    (
                        "session_factory",
                        "now_provider",
                        "intent_agent",
                        "clients",
                        "on_user_created",
                        "translation_llm",
                        "composer",
                        "recipe_sources",
                    ),
                    event,
                )
            else:
                await event.answer(
                    t("miniapp.assistant_unavailable", workspace.lang)
                )
            # Receipt refinement and cook generation can spawn further work.
            index = 0
            while index < len(children):
                await children[index]
                index += 1
        except web.HTTPException as exc:
            workspace.error = exc.text
        except Exception:
            log.exception("mini_app_action_failed", extra={"kind": body.get("kind")})
            workspace.error = t("miniapp.failed", workspace.lang)
        finally:
            for child in children:
                if not child.done():
                    child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            while len(workspace.cards) > 100:
                del workspace.cards[next(iter(workspace.cards))]

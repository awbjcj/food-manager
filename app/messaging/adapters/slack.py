from __future__ import annotations

import httpx

from app.messaging.adapters.http import download, json_request
from app.messaging.contracts import (
    Accepted,
    ActionButton,
    DeliveryRequest,
    DigestNotice,
    ErrorCode,
    MediaRef,
    MessageRef,
    MessagingError,
)


class SlackAdapter:
    def __init__(self, *, connection_id: str, token: str, http: httpx.AsyncClient):
        self.connection_id, self.token, self.http = connection_id, token, http

    async def api(self, method: str, payload: dict, *, sends=False) -> dict:
        data = await json_request(
            self.http,
            "POST",
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {self.token}"},
            json=payload,
            sends=sends,
        )
        if data.get("ok") is not True:
            error = str(data.get("error", ""))
            code = {
                "invalid_auth": ErrorCode.AUTH_FAILED,
                "token_revoked": ErrorCode.AUTH_FAILED,
                "not_authed": ErrorCode.AUTH_FAILED,
                "missing_scope": ErrorCode.FORBIDDEN,
                "channel_not_found": ErrorCode.NOT_FOUND,
                "message_not_found": ErrorCode.NOT_FOUND,
                "cant_update_message": ErrorCode.NOT_FOUND,
                "ratelimited": ErrorCode.RATE_LIMITED,
            }.get(error, ErrorCode.INVALID_INPUT)
            raise MessagingError(code, "Slack rejected the request")
        return data

    async def identity(self) -> tuple[str, dict]:
        value = await self.api("auth.test", {})
        team, bot, user = (
            value.get("team_id"),
            value.get("bot_id"),
            value.get("user_id"),
        )
        if (
            not isinstance(team, str)
            or not team
            or not all(isinstance(v, str) and v for v in (bot, user))
        ):
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "A Slack workspace bot token is required"
            )
        return team, {"botId": bot, "botUserId": user}

    async def deliver(self, request: DeliveryRequest):
        if request.conversation.connection_id != self.connection_id:
            raise MessagingError(ErrorCode.FORBIDDEN, "Wrong connection")
        message = (
            request.content.localized_message
            if isinstance(request.content, DigestNotice)
            else request.content
        )
        chunks = [message.text[i : i + 2800] for i in range(0, len(message.text), 2800)]
        refs = []
        for index, chunk in enumerate(chunks):
            blocks = [
                {"type": "section", "text": {"type": "plain_text", "text": chunk}}
            ]
            if index == len(chunks) - 1:
                buttons = []
                for button in message.buttons:
                    element = {
                        "type": "button",
                        "text": {"type": "plain_text", "text": button.label[:75]},
                    }
                    if isinstance(button, ActionButton):
                        element.update(
                            action_id=f"food_{len(buttons)}", value=button.token
                        )
                    else:
                        element["url"] = button.url
                    buttons.append(element)
                blocks.extend(
                    {"type": "actions", "elements": buttons[i : i + 5]}
                    for i in range(0, len(buttons), 5)
                )
            payload = {
                "channel": request.conversation.external_conversation_id,
                "text": chunk,
                "mrkdwn": False,
                "unfurl_links": False,
                "unfurl_media": False,
                "blocks": blocks,
            }
            if request.conversation.thread_id:
                payload["thread_ts"] = request.conversation.thread_id
            replace = request.replace if len(chunks) == 1 else None
            if replace:
                payload["ts"] = replace.external_message_id
            try:
                result = await self.api(
                    "chat.update" if replace else "chat.postMessage",
                    payload,
                    sends=True,
                )
            except MessagingError as exc:
                if not replace or exc.code != ErrorCode.NOT_FOUND:
                    if refs:
                        raise MessagingError(
                            ErrorCode.DELIVERY_UNKNOWN,
                            "Part of the response was accepted",
                        ) from None
                    raise
                payload.pop("ts", None)
                result = await self.api("chat.postMessage", payload, sends=True)
            if not isinstance(result.get("ts"), str):
                raise MessagingError(
                    ErrorCode.DELIVERY_UNKNOWN,
                    "Slack did not return a message reference",
                )
            refs.append(
                MessageRef(
                    conversation=request.conversation, external_message_id=result["ts"]
                )
            )
        return Accepted(messages=tuple(refs))

    async def download_image(self, media: MediaRef):
        if media.connection_id != self.connection_id:
            raise MessagingError(ErrorCode.FORBIDDEN, "Wrong connection")
        result = await self.api("files.info", {"file": media.external_media_id})
        file = result.get("file")
        if not isinstance(file, dict) or not isinstance(
            file.get("url_private_download"), str
        ):
            raise MessagingError(ErrorCode.NOT_FOUND, "Receipt image is unavailable")
        return await download(
            self.http,
            file["url_private_download"],
            token=self.token,
            hosts=("files.slack.com",),
        )

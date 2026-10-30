from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.i18n import t
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
    Suppressed,
)
from app.pending_service import utc_naive


class WhatsAppAdapter:
    def __init__(
        self,
        *,
        connection_id: str,
        token: str,
        config: dict,
        http: httpx.AsyncClient,
        now: datetime,
        last_customer_message_at: datetime | None = None,
        opted_in: bool = False,
    ):
        self.connection_id, self.token, self.config, self.http = (
            connection_id,
            token,
            config,
            http,
        )
        self.now, self.last_customer_message_at, self.opted_in = (
            utc_naive(now),
            last_customer_message_at,
            opted_in,
        )
        self.root = f"https://graph.facebook.com/{config['apiVersion']}"

    async def api(self, path, payload=None, *, sends=False):
        return await json_request(
            self.http,
            "POST" if payload is not None else "GET",
            f"{self.root}/{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            **({"json": payload} if payload is not None else {}),
            sends=sends,
        )

    async def identity(self):
        # Validate that the configured sender belongs to this business account.
        entries = await self.collection("phone_numbers", "id")
        if not any(x.get("id") == self.config["phoneNumberId"] for x in entries):
            raise MessagingError(
                ErrorCode.AUTH_FAILED,
                "Sender is not accessible in this business account",
            )
        configured = self.config.get("digestTemplates", [])
        if configured:
            templates = await self.collection(
                "message_templates", "name,status,language,components"
            )
            for expected in configured:
                matching = next(
                    (
                        t
                        for t in templates
                        if t.get("name") == expected["name"]
                        and t.get("language") == expected["language"]
                        and t.get("status") == "APPROVED"
                    ),
                    None,
                )
                components = matching.get("components", []) if matching else []
                body = next(
                    (c.get("text", "") for c in components if c.get("type") == "BODY"),
                    "",
                )
                if (
                    body.count("{{1}}") != 1
                    or "{{" in body.replace("{{1}}", "")
                    or any(c.get("type") not in {"BODY", "FOOTER"} for c in components)
                ):
                    raise MessagingError(
                        ErrorCode.INVALID_INPUT,
                        "Reminder template must be approved in this language with one body count parameter and no header or buttons",
                    )
        return self.config["phoneNumberId"], {}

    async def collection(self, name, fields):
        rows, cursor = [], None
        for _ in range(10):
            query = {"fields": fields, "limit": "100"}
            if cursor:
                query["after"] = cursor
            result = await self.api(
                f"{self.config['businessAccountId']}/{name}?{urlencode(query)}"
            )
            page = result.get("data")
            if not isinstance(page, list) or any(
                not isinstance(row, dict) for row in page
            ):
                raise MessagingError(
                    ErrorCode.TEMPORARY_FAILURE,
                    "Provider returned an invalid account response",
                )
            rows.extend(page)
            paging = result.get("paging", {})
            if not paging.get("next"):
                return rows
            cursor = paging.get("cursors", {}).get("after")
            if not isinstance(cursor, str) or len(cursor) > 4096:
                break
        raise MessagingError(
            ErrorCode.TEMPORARY_FAILURE, "Account validation exceeded the page limit"
        )

    async def deliver(self, request: DeliveryRequest):
        if (
            request.conversation.connection_id != self.connection_id
            or request.conversation.kind != "direct"
        ):
            raise MessagingError(
                ErrorCode.FORBIDDEN,
                "WhatsApp requires a direct destination on this connection",
            )
        if isinstance(request.content, DigestNotice) and not self.opted_in:
            return Suppressed(reason="opted_out")
        inside = self.last_customer_message_at and self.now - utc_naive(
            self.last_customer_message_at
        ) < timedelta(hours=24)
        base = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": request.conversation.external_conversation_id,
        }
        if not inside:
            if not isinstance(request.content, DigestNotice):
                return Suppressed(reason="template_required")
            template = next(
                (
                    t
                    for t in self.config.get("digestTemplates", [])
                    if t["language"].split("_")[0] == request.content.language
                ),
                None,
            )
            if template is None:
                return Suppressed(reason="template_required")
            payloads = [
                {
                    **base,
                    "type": "template",
                    "template": {
                        "name": template["name"],
                        "language": {"code": template["language"]},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [
                                    {
                                        "type": "text",
                                        "text": str(request.content.due_count),
                                    }
                                ],
                            }
                        ],
                    },
                }
            ]
        else:
            message = (
                request.content.localized_message
                if isinstance(request.content, DigestNotice)
                else request.content
            )
            text = message.text
            links = [
                f"{b.label}: {b.url}"
                for b in message.buttons
                if not isinstance(b, ActionButton)
            ]
            if links:
                text += "\n" + "\n".join(links)
            chunks = [text[i : i + 3000] for i in range(0, len(text), 3000)]
            payloads = [
                {**base, "type": "text", "text": {"body": chunk, "preview_url": False}}
                for chunk in chunks
            ]
            actions = [b for b in message.buttons if isinstance(b, ActionButton)]
            # Each list supports ten choices. Additional lists retain all actions.
            for offset in range(0, len(actions), 10):
                group = actions[offset : offset + 10]
                payloads.append(
                    {
                        **base,
                        "type": "interactive",
                        "interactive": {
                            "type": "list",
                            "body": {
                                "text": t("messaging.choose_action", message.language)
                            },
                            "action": {
                                "button": t("messaging.actions", message.language),
                                "sections": [
                                    {
                                        "rows": [
                                            {"id": b.token, "title": b.label[:24]}
                                            for b in group
                                        ]
                                    }
                                ],
                            },
                        },
                    }
                )
        refs = []
        for payload in payloads:
            try:
                result = await self.api(
                    f"{self.config['phoneNumberId']}/messages", payload, sends=True
                )
            except MessagingError:
                if refs:
                    raise MessagingError(
                        ErrorCode.DELIVERY_UNKNOWN, "Part of the response was accepted"
                    ) from None
                raise
            messages = result.get("messages")
            if (
                not isinstance(messages, list)
                or not messages
                or not isinstance(messages[0], dict)
                or not isinstance(messages[0].get("id"), str)
            ):
                raise MessagingError(
                    ErrorCode.DELIVERY_UNKNOWN,
                    "WhatsApp did not return a message reference",
                )
            refs.append(
                MessageRef(
                    conversation=request.conversation,
                    external_message_id=messages[0]["id"],
                )
            )
        return Accepted(messages=tuple(refs))

    async def download_image(self, media: MediaRef):
        if (
            media.connection_id != self.connection_id
            or not media.external_media_id.isdigit()
        ):
            raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid media reference")
        result = await self.api(media.external_media_id)
        if not isinstance(result.get("url"), str):
            raise MessagingError(ErrorCode.NOT_FOUND, "Receipt image is unavailable")
        return await download(
            self.http,
            result["url"],
            token=self.token,
            hosts=("lookaside.fbsbx.com", "lookaside.facebook.com"),
        )

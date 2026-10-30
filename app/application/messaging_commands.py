"""Identity and deployment-independent commands for normalized inbound events."""

from __future__ import annotations

from sqlmodel import select

from app.commands import CommandError, parse_digest_at, parse_lang, parse_tz
from app.i18n import t
from app.invite_service import (
    AlreadyMember,
    InviteError,
    InviteInvalid,
    _require_free_seat,
    create_invite,
    leave_household,
    list_members,
    remove_member,
)
from app.messaging.contracts import (
    ChatMessage,
    DeliveryRequest,
    ErrorCode,
    MessagingError,
    TextReceived,
)
from app.messaging.identity_service import (
    authorize,
    confirm_link,
    create_link,
    find_user,
    observe_destination,
    redeem_link,
    select_destination,
)
from app.messaging.queue_service import enqueue
from app.models import ConversationBinding, ExternalIdentity, HouseholdInvite, User
from app.pending_service import utc_naive


def join(session, *, incoming, token, now, provider):
    user = find_user(session, incoming.actor)
    if user and (user.banned or user.membership_active):
        raise AlreadyMember()
    invite = session.exec(
        select(HouseholdInvite).where(HouseholdInvite.token == token)
    ).first()
    if (
        invite is None
        or invite.expires_at <= utc_naive(now)
        or (invite.max_uses is not None and invite.uses >= invite.max_uses)
    ):
        raise InviteInvalid()
    _require_free_seat(session, household_id=invite.household_id, now=now)
    if user is None:
        user = User(
            household_id=invite.household_id,
            role="member",
            llm_provider=provider,
            created_at=utc_naive(now),
        )
        session.add(user)
        session.flush()
        session.add(
            ExternalIdentity(
                connection_id=incoming.actor.connection_id,
                external_user_id=incoming.actor.external_user_id,
                user_id=user.id,
            )
        )
    user.household_id, user.membership_active, user.role = (
        invite.household_id,
        True,
        "member",
    )
    invite.uses += 1
    invite.redeemed_by, invite.redeemed_at = user.id, utc_naive(now)
    session.add(user)
    session.add(invite)
    return user


async def run_identity_command(runtime, session, incoming, *, operation):
    if not isinstance(incoming.content, TextReceived):
        return False
    parts = incoming.content.text.strip().split()
    name = parts[0].lstrip("/").split("@")[0].lower() if parts else ""
    commands = {
        "start",
        "help",
        "join",
        "invite",
        "household",
        "leave",
        "remove",
        "bind",
        "link",
        "confirm_link",
        "reminders",
        "lang",
        "tz",
        "digest_at",
        "quota",
        "buy",
        "billing",
    }
    if name not in commands:
        return False
    now, settings, user = runtime.now(), runtime.settings, None
    try:
        from app.messaging.connection_service import require_connection

        connection = require_connection(session, incoming.actor.connection_id)
        if not connection.enabled:
            raise MessagingError(ErrorCode.FORBIDDEN, "Connection is disabled")
        if name != "bind" and incoming.conversation.kind != "direct":
            raise MessagingError(
                ErrorCode.FORBIDDEN, "Use a direct conversation for account commands"
            )
        if name == "link" and len(parts) == 2:
            redeem_link(session, token=parts[1], actor=incoming.actor, now=now)
            text = t("messaging.link_pending", user.lang if user else "en")
        elif name == "join" or (name == "start" and len(parts) == 2):
            if not settings.hosted_features_enabled or len(parts) != 2:
                raise MessagingError(
                    ErrorCode.FORBIDDEN, "Household invitations are unavailable"
                )
            user = join(
                session,
                incoming=incoming,
                token=parts[1],
                now=now,
                provider=settings.llm_provider,
            )
            text = t("messaging.joined", user.lang if user else "en")
        else:
            # Binding authorizes the actor without treating an unbound group as
            # an already-authorized household conversation.
            conversation = (
                incoming.conversation.model_copy(update={"kind": "direct"})
                if name == "bind"
                else incoming.conversation
            )
            user = authorize(
                session,
                actor=incoming.actor,
                conversation=conversation,
                now=now,
                open_registration=settings.effective_open_registration,
                hosted=settings.hosted_features_enabled,
                default_provider=settings.llm_provider,
                create=name == "start",
            )
            destination = observe_destination(
                session,
                user=user,
                conversation=incoming.conversation,
                now=incoming.occurred_at or incoming.received_at,
            )
            arg = " ".join(parts[1:])
            if name in {"start", "help"}:
                text = t("messaging.help", user.lang)
            elif name == "reminders":
                assert destination is not None  # account commands require a direct chat
                if arg not in {"on", "off"}:
                    raise CommandError("Use reminders on or reminders off")
                select_destination(
                    session, user=user, destination=destination, enabled=arg == "on"
                )
                text = (
                    t("messaging.reminders_on", user.lang)
                    if arg == "on"
                    else t("messaging.reminders_off", user.lang)
                )
            elif name == "link":
                token = create_link(session, user=user, actor=incoming.actor, now=now)
                text = t("messaging.link_code", user.lang, token=token)
            elif name == "confirm_link":
                confirm_link(
                    session, token=arg, user=user, actor=incoming.actor, now=now
                )
                text = t("messaging.linked", user.lang if user else "en")
            elif name in {"lang", "tz", "digest_at"}:
                if name == "lang":
                    user.lang = parse_lang(parts[1:]) or user.lang
                elif name == "tz":
                    user.tz = parse_tz(arg)
                else:
                    user.digest_hour = parse_digest_at(arg)
                text = t(
                    "messaging.settings",
                    user.lang,
                    language=user.lang,
                    tz=user.tz,
                    hour=user.digest_hour,
                )
            elif name in {"buy", "billing"}:
                text = t("messaging.payments", user.lang if user else "en")
            elif name == "quota":
                from app.billing.entitlement import (
                    get_or_create_subscription,
                    roll_period_if_due,
                )

                sub = roll_period_if_due(
                    session,
                    sub=get_or_create_subscription(
                        session, household_id=user.household_id, now=now
                    ),
                    now=now,
                )
                from app.billing.meter import snapshot

                usage = snapshot(session, household_id=user.household_id, now=now)
                text = t(
                    "messaging.quota",
                    user.lang,
                    tier=sub.tier,
                    end=sub.period_end.date(),
                    receipts=usage.receipts_used,
                    receipt_limit=usage.receipts_limit,
                    actions=usage.actions_used,
                    action_limit=usage.actions_limit,
                )
            else:
                if not settings.hosted_features_enabled:
                    raise MessagingError(
                        ErrorCode.FORBIDDEN,
                        "Household sharing is unavailable on this deployment",
                    )
                if name == "invite":
                    result = create_invite(
                        session,
                        household_id=user.household_id,
                        created_by=user.id,
                        now=now,
                        max_uses=None if arg == "family" else 1,
                    )
                    text = t("messaging.invite", user.lang, token=result.token)
                elif name == "household":
                    text = (
                        t(
                            "household.title",
                            user.lang,
                            n=len(
                                list_members(session, household_id=user.household_id)
                            ),
                        )
                        + "\n"
                        + "\n".join(
                            f"{m.telegram_id}: {m.role}"
                            for m in list_members(
                                session, household_id=user.household_id
                            )
                        )
                    )
                elif name == "leave":
                    leave_household(session, telegram_user_id=user.id)
                    text = t("messaging.left", user.lang)
                    user = None
                elif name == "remove":
                    if not arg.isdigit():
                        raise CommandError(
                            "Use remove followed by the member ID shown by household"
                        )
                    remove_member(
                        session,
                        household_id=user.household_id,
                        actor_id=user.id,
                        target_id=int(arg),
                    )
                    text = t("messaging.removed", user.lang if user else "en")
                else:
                    if incoming.conversation.kind != "shared" or user.role != "owner":
                        raise MessagingError(
                            ErrorCode.FORBIDDEN,
                            "Only the household owner can bind a shared conversation",
                        )
                    key = (
                        incoming.actor.connection_id,
                        incoming.conversation.external_conversation_id,
                    )
                    existing = session.get(ConversationBinding, key)
                    if existing and existing.household_id != user.household_id:
                        raise MessagingError(
                            ErrorCode.CONFLICT,
                            "Conversation already belongs to another household",
                        )
                    session.add(
                        ConversationBinding(
                            connection_id=key[0],
                            external_conversation_id=key[1],
                            household_id=user.household_id,
                            bound_by_user_id=user.id,
                        )
                    )
                    text = t("messaging.bound", user.lang if user else "en")
            if user:
                session.add(user)
        if user:
            observe_destination(
                session,
                user=user,
                conversation=incoming.conversation,
                now=incoming.occurred_at or incoming.received_at,
            )
    except (MessagingError, CommandError, InviteError) as exc:
        session.rollback()
        user = None
        text = (
            exc.message
            if isinstance(exc, MessagingError)
            else str(exc) or type(exc).__name__
        )
    enqueue(
        session,
        DeliveryRequest(
            delivery_key=f"{operation}:identity",
            conversation=incoming.conversation,
            content=ChatMessage(text=text),
        ),
        user=user,
        now=now,
    )
    session.commit()
    return True

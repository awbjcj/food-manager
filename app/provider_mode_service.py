"""Which endpoint each provider's traffic goes to, and who decided.

Two inputs settle one provider's credential mode:

1. **Configuration** — a provider defaults to ``subscription`` whenever a
   sub2api token is set for it (``Settings.default_credential_mode``), so the
   gateway is the default route without any allow-list, and a provider you hold
   no subscription for stays on its own metered API automatically.
2. **An operator override** — a ``ProviderModeOverride`` row, set from the
   operator bot, wins over the config default.

An override is honoured only while credentials for that mode still exist. If a
token is later removed, the stale override is ignored rather than producing a
provider that resolves to nothing — the alternative is a client the bot can
never build and a capability that silently disappears.

The resolved modes are an *input to client construction*: ``bin.run`` passes
them to the LLM builder, and an operator flip rebuilds and adopts. Nothing
downstream of the selectors ever sees a mode.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlmodel import Session, select

from app.models import ProviderModeOverride
from app.providers import (
    ALL_CREDENTIAL_MODES,
    ALL_PROVIDERS,
    CredentialMode,
    Provider,
)
from app.settings import Settings

log = logging.getLogger(__name__)


class ProviderModeError(ValueError):
    """An operator asked for a mode that cannot be served."""


class ProviderModeApplyError(RuntimeError):
    """A validated mode could not be applied safely to the live runtime."""

    def __init__(self, *, restored: bool) -> None:
        super().__init__("provider mode apply failed")
        self.restored = restored


ModeSource = Literal["config", "override"]


@dataclass(frozen=True)
class ProviderModeStatus:
    """One provider's resolved mode plus the context an operator needs to act."""

    provider: Provider
    mode: CredentialMode
    #: "override" when an operator set it, "config" when derived from env.
    source: ModeSource
    #: Modes this provider actually has credentials for, in canonical order.
    available_modes: tuple[CredentialMode, ...]
    updated_at: datetime | None = None
    updated_by: int | None = None

    @property
    def usable(self) -> bool:
        return bool(self.available_modes)


def _overrides(session: Session) -> dict[str, ProviderModeOverride]:
    rows = session.exec(select(ProviderModeOverride)).all()
    return {row.provider: row for row in rows}


def _available_modes(settings: Settings, provider: str) -> tuple[CredentialMode, ...]:
    return tuple(
        mode
        for mode in ALL_CREDENTIAL_MODES
        if settings.credentials_for(provider, mode) is not None
    )


def describe_modes(session: Session, settings: Settings) -> list[ProviderModeStatus]:
    """Resolved mode + provenance for every known provider."""
    overrides = _overrides(session)
    statuses: list[ProviderModeStatus] = []
    for provider in ALL_PROVIDERS:
        available = _available_modes(settings, provider)
        row = overrides.get(provider)
        if row is not None and row.mode in available:
            statuses.append(
                ProviderModeStatus(
                    provider=provider,
                    mode=row.mode,  # type: ignore[arg-type]
                    source="override",
                    available_modes=available,
                    updated_at=row.updated_at,
                    updated_by=row.updated_by,
                )
            )
            continue
        if row is not None:
            # The override outlived its credentials (token removed). Report the
            # config default so the operator sees what is actually in effect.
            log.warning(
                "provider_mode_override_stale",
                extra={"provider": provider, "mode": row.mode},
            )
        statuses.append(
            ProviderModeStatus(
                provider=provider,
                mode=settings.default_credential_mode(provider),
                source="config",
                available_modes=available,
            )
        )
    return statuses


def effective_modes(session: Session, settings: Settings) -> dict[str, CredentialMode]:
    """``{provider: mode}`` for every provider — the LLM builder's input."""
    return {
        status.provider: status.mode for status in describe_modes(session, settings)
    }


def set_mode(
    session: Session,
    settings: Settings,
    *,
    provider: str,
    mode: str,
    actor: int | None,
    now: datetime,
) -> ProviderModeStatus:
    """Persist an operator's mode choice for one provider.

    Rejects a mode the provider has no credentials for, so a flip can never
    leave the bot unable to build that provider's clients.
    """
    status = _stage_mode(
        session,
        settings,
        provider=provider,
        mode=mode,
        actor=actor,
        now=now,
    )
    session.commit()
    log.info(
        "provider_mode_set",
        extra={"provider": provider, "mode": mode, "actor": actor},
    )
    return status


def _stage_mode(
    session: Session,
    settings: Settings,
    *,
    provider: str,
    mode: str,
    actor: int | None,
    now: datetime,
) -> ProviderModeStatus:
    """Validate and stage a mode choice without committing the session."""
    if provider not in ALL_PROVIDERS:
        raise ProviderModeError(f"unknown provider {provider!r}")
    if mode not in ALL_CREDENTIAL_MODES:
        raise ProviderModeError(f"unknown mode {mode!r}")
    if settings.credentials_for(provider, mode) is None:
        missing = (
            f"SUB2API_BASE_URL + SUB2API_{provider.upper()}_TOKEN"
            if mode == "subscription"
            else f"{provider.upper()}_API_KEY"
        )
        raise ProviderModeError(
            f"{provider} has no {mode} credentials configured (set {missing})"
        )

    row = session.get(ProviderModeOverride, provider)
    if mode == settings.default_credential_mode(provider):
        if row is not None:
            session.delete(row)
        session.flush()
        return ProviderModeStatus(
            provider=provider,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            source="config",
            available_modes=_available_modes(settings, provider),
        )
    if row is None:
        row = ProviderModeOverride(
            provider=provider, mode=mode, updated_at=now, updated_by=actor
        )
        session.add(row)
    else:
        row.mode = mode
        row.updated_at = now
        row.updated_by = actor
    session.flush()
    return ProviderModeStatus(
        provider=provider,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        source="override",
        available_modes=_available_modes(settings, provider),
        updated_at=now,
        updated_by=actor,
    )


def clear_override(session: Session, *, provider: str) -> bool:
    """Drop an override so the provider returns to its configured default."""
    row = session.get(ProviderModeOverride, provider)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    log.info("provider_mode_cleared", extra={"provider": provider})
    return True


ModeApplier = Callable[[Mapping[str, CredentialMode]], None]


@dataclass
class ProviderModeAdmin:
    """Operator-facing handle: read the modes, change one, push it live.

    ``apply`` is injected by ``bin.run``; it rebuilds the LLM clients from the
    new mode map and adopts them onto the live selectors. Keeping it a plain
    callable here means the operator handlers never import the runtime wiring,
    and a test can pass a recorder instead.
    """

    settings: Settings
    apply: ModeApplier | None = None

    def describe(self, session: Session) -> list[ProviderModeStatus]:
        return describe_modes(session, self.settings)

    def set(
        self,
        session: Session,
        *,
        provider: str,
        mode: str,
        actor: int | None,
        now: datetime,
    ) -> ProviderModeStatus:
        """Apply and persist one choice, rolling back either side on failure."""
        previous_modes = effective_modes(session, self.settings)
        restore_runtime = False
        try:
            status = _stage_mode(
                session,
                self.settings,
                provider=provider,
                mode=mode,
                actor=actor,
                now=now,
            )
            modes = effective_modes(session, self.settings)
            if self.apply is not None:
                restore_runtime = True
                self.apply(modes)
            session.commit()
        except ProviderModeError:
            session.rollback()
            raise
        except Exception:  # noqa: BLE001 - rollback covers DB and arbitrary SDK constructors
            session.rollback()
            restored = True
            if self.apply is not None and restore_runtime:
                try:
                    self.apply(previous_modes)
                except Exception:  # noqa: BLE001 - recovery must contain any SDK failure
                    restored = False
                    log.critical(
                        "provider_mode_runtime_rollback_failed",
                        extra={"provider": provider, "mode": mode},
                    )
            raise ProviderModeApplyError(restored=restored) from None
        log.info(
            "provider_mode_set",
            extra={"provider": provider, "mode": mode, "actor": actor},
        )
        return status

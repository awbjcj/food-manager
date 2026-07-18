"""Telegram transport adapters for renderer-owned UI values."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.renderer import CallbackButton


def to_aiogram_keyboard(rows: list[list[CallbackButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=button.text, url=button.url)
                if button.url
                else InlineKeyboardButton(
                    text=button.text, callback_data=button.callback_data
                )
                for button in row
            ]
            for row in rows
        ]
    )

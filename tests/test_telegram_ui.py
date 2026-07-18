from app.renderer import CallbackButton
from app.telegram_ui import to_aiogram_keyboard


def test_keyboard_adapter_preserves_callback_and_url_buttons():
    keyboard = to_aiogram_keyboard(
        [
            [
                CallbackButton(text="Open", url="https://example.test"),
                CallbackButton(text="Done", callback_data="done:1"),
            ]
        ]
    )

    open_button, done_button = keyboard.inline_keyboard[0]
    assert open_button.url == "https://example.test"
    assert open_button.callback_data is None
    assert done_button.callback_data == "done:1"
    assert done_button.url is None

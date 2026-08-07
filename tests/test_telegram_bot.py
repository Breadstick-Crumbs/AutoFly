from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from autofly.config import TelegramConfig, load_config
from autofly.dashboard.config_store import ConfigStore
from autofly.errors import LockUnavailable
from autofly.locking import ProcessLock
from autofly.telegram_bot import TelegramBotAPI, TelegramControlBot, _token


class FakeTelegramAPI:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list[list[dict[str, str]]] | None]] = []
        self.callbacks: list[str] = []

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        return []

    def send_message(self, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> None:
        self.messages.append((text, keyboard))

    def answer_callback(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)

    def set_commands(self) -> None:
        pass

    def webhook_url(self) -> str:
        return ""


def config_file(tmp_path: Path, *, with_watch: bool = False) -> Path:
    watches: list[dict[str, Any]] = []
    if with_watch:
        watches.append(
            {
                "id": "cok-dxb",
                "enabled": True,
                "origins": ["COK"],
                "destinations": ["DXB"],
                "trip": {"type": "one_way", "adults": 1, "cabin": "economy"},
                "dates": {
                    "mode": "range",
                    "departure_start": "2026-08-10",
                    "departure_end": "2026-08-20",
                },
                "deal": {
                    "currency": "INR",
                    "maximum_price": 25000,
                    "max_stops": 1,
                    "allow_self_transfer": False,
                },
            }
        )
    raw = {
        "version": 1,
        "database": {"path": str(tmp_path / "autofly.db")},
        "scheduler": {
            "interval_hours": 6,
            "jitter_minutes": 10,
            "timezone": "UTC",
            "max_queries_per_cycle": 100,
            "lock_path": str(tmp_path / "autofly.lock"),
        },
        "sources": {
            "flight_goat": {"enabled": False},
            "playwright": {"enabled": False},
        },
        "notifications": {
            "telegram": {"enabled": True, "control_enabled": True},
            "webhook": {"enabled": False},
        },
        "watches": watches,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def message(text: str, *, chat_id: int = 123, chat_type: str = "private") -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type}, "text": text}}


def callback(data: str, *, chat_id: int = 123) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": f"callback-{data}",
            "data": data,
            "message": {"chat": {"id": chat_id, "type": "private"}},
        },
    }


@pytest.fixture(autouse=True)
def telegram_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")


def test_bot_ignores_other_chats_and_groups(tmp_path: Path) -> None:
    api = FakeTelegramAPI()
    bot = TelegramControlBot(config_file(tmp_path), api=api)

    bot.handle_update(message("/menu", chat_id=999))
    bot.handle_update(message("/menu", chat_type="group"))

    assert api.messages == []


def test_menu_and_watch_management_buttons(tmp_path: Path) -> None:
    api = FakeTelegramAPI()
    bot = TelegramControlBot(config_file(tmp_path, with_watch=True), api=api)

    bot.handle_update(message("/watches"))
    assert "Your flight watches (1)" in api.messages[-1][0]
    assert api.messages[-1][1][0][0]["callback_data"] == f"w:{_token('cok-dxb')}"  # type: ignore[index]

    bot.handle_update(callback(f"t:{_token('cok-dxb')}"))
    assert load_config(bot.config_path).watches[0].enabled is False

    bot.handle_update(callback(f"d:{_token('cok-dxb')}"))
    assert "Delete cok-dxb?" in api.messages[-1][0]
    bot.handle_update(callback(f"x:{_token('cok-dxb')}"))
    assert load_config(bot.config_path).watches == []


def test_guided_watch_creation_saves_valid_configuration(tmp_path: Path) -> None:
    api = FakeTelegramAPI()
    path = config_file(tmp_path)
    bot = TelegramControlBot(path, api=api)

    updates = [
        message("/add"),
        message("kerala-uae"),
        message("COK, CCJ, TRV"),
        message("DXB, AUH, AAN"),
        callback("f:trip:one_way"),
        callback("f:cabin:economy"),
        message("1"),
        callback("f:dates:range"),
        message("2026-08-10, 2026-08-20"),
        message("INR"),
        message("25000"),
        callback("f:stops:1"),
        callback("f:layover:skip"),
        callback("f:self:no"),
        message("24"),
        message("1000"),
        callback("save"),
    ]
    for update in updates:
        bot.handle_update(update)

    watch = load_config(path).watches[0]
    assert watch.id == "kerala-uae"
    assert watch.origins == ["COK", "CCJ", "TRV"]
    assert watch.destinations == ["DXB", "AUH", "AAN"]
    assert watch.dates.mode == "range"
    assert watch.deal.maximum_price == 25000
    assert watch.deal.max_stops == 1
    assert "Saved kerala-uae" in api.messages[-2][0]
    assert path.with_suffix(".yaml.bak").exists()


def test_cancel_does_not_change_configuration(tmp_path: Path) -> None:
    api = FakeTelegramAPI()
    path = config_file(tmp_path, with_watch=True)
    original = path.read_text(encoding="utf-8")
    bot = TelegramControlBot(path, api=api)

    bot.handle_update(message("/add"))
    bot.handle_update(message("temporary"))
    bot.handle_update(message("/cancel"))

    assert path.read_text(encoding="utf-8") == original
    assert "Nothing was changed" in api.messages[-1][0]


def test_watch_edits_use_an_interprocess_lock(tmp_path: Path) -> None:
    path = config_file(tmp_path, with_watch=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with ProcessLock(lock_path), pytest.raises(LockUnavailable):
        ConfigStore(path).set_enabled("cok-dxb", False)

    assert load_config(path).watches[0].enabled is True


def test_check_runs_without_blocking_update_loop(tmp_path: Path) -> None:
    api = FakeTelegramAPI()
    bot = TelegramControlBot(
        config_file(tmp_path, with_watch=True),
        api=api,
        check_runner=lambda watch_id: {
            "status": "success",
            "routes_attempted": 1,
            "candidate_count": 12,
            "qualifying_count": 2,
            "notifications_sent": 1,
        },
    )

    bot.handle_update(message("/check"))
    assert bot._check_thread is not None
    bot._check_thread.join(timeout=2)

    assert any("Starting a fare search" in text for text, _ in api.messages)
    assert any("Matching deals: 2" in text for text, _ in api.messages)


def test_telegram_api_uses_bounded_long_polling_and_inline_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        result: Any = [{"update_id": 7}] if method == "getUpdates" else True
        return httpx.Response(200, json={"ok": True, "result": result})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = TelegramBotAPI(TelegramConfig(enabled=True, control_enabled=True), client=client)

    assert api.get_updates(7, 25) == [{"update_id": 7}]
    api.send_message("Menu", [[{"text": "Watches", "callback_data": "m:w"}]])

    update_body = json.loads(seen[0].content)
    message_body = json.loads(seen[1].content)
    assert update_body["offset"] == 7
    assert update_body["limit"] == 20
    assert update_body["allowed_updates"] == ["message", "callback_query"]
    assert message_body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "m:w"

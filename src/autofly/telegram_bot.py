from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import httpx

from autofly.config import (
    IATA_RE,
    WATCH_ID_RE,
    TelegramConfig,
    WatchConfig,
    load_config,
)
from autofly.dashboard.config_store import ConfigStore
from autofly.database import Database
from autofly.engine import WatchEngine
from autofly.errors import AutoFlyError, ConfigError
from autofly.locking import ProcessLock
from autofly.notifications.telegram import NotificationError, TelegramNotifier, format_price
from autofly.notifications.webhook import WebhookNotifier
from autofly.sources.flight_goat import FlightGoatSource
from autofly.sources.playwright import PlaywrightSource

MAX_TELEGRAM_RESPONSE_BYTES = 1_000_000


class TelegramAPI(Protocol):
    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]: ...

    def send_message(
        self, text: str, keyboard: list[list[dict[str, str]]] | None = None
    ) -> None: ...

    def answer_callback(self, callback_id: str) -> None: ...

    def set_commands(self) -> None: ...

    def webhook_url(self) -> str: ...


class TelegramBotAPI:
    """Small, bounded Telegram Bot API client for the private control interface."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.token = os.environ.get(config.bot_token_env)
        self.chat_id = os.environ.get(config.chat_id_env)
        if not self.token or not self.chat_id:
            raise NotificationError(
                f"Telegram requires {config.bot_token_env} and {config.chat_id_env}"
            )
        self.client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
        self._sleep = sleeper

    def _call(self, method: str, payload: dict[str, Any], *, timeout: float | None = None) -> Any:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        last_error: Exception | None = None
        request_timeout = timeout if timeout is not None else self.config.timeout_seconds
        for attempt in range(3):
            try:
                response = self.client.post(url, json=payload, timeout=request_timeout)
                if len(response.content) > MAX_TELEGRAM_RESPONSE_BYTES:
                    raise NotificationError("Telegram response exceeded the safety limit")
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    self._sleep(2**attempt)
                    continue
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("ok") is not True:
                    raise NotificationError("Telegram returned an unsuccessful response")
                return body.get("result")
            except (httpx.HTTPError, ValueError, NotificationError) as exc:
                last_error = exc
                if attempt < 2:
                    self._sleep(2**attempt)
                    continue
        raise NotificationError("Telegram request failed after 3 attempts") from last_error

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "limit": 20,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise NotificationError("Telegram returned malformed updates")
        return result

    def send_message(self, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        self._call("sendMessage", payload)

    def answer_callback(self, callback_id: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id})

    def set_commands(self) -> None:
        commands = [
            {"command": "menu", "description": "Open the AutoFly menu"},
            {"command": "watches", "description": "View and manage flight watches"},
            {"command": "add", "description": "Create a flight watch"},
            {"command": "results", "description": "Show recent matching deals"},
            {"command": "check", "description": "Search all active watches now"},
            {"command": "status", "description": "Show monitor health and schedule"},
            {"command": "cancel", "description": "Cancel the current setup"},
        ]
        self._call("setMyCommands", {"commands": commands})

    def webhook_url(self) -> str:
        result = self._call("getWebhookInfo", {})
        return str(result.get("url") or "") if isinstance(result, dict) else ""


@dataclass
class WatchDraft:
    original_id: str | None = None
    step: str = "id"
    values: dict[str, Any] = field(default_factory=dict)


def _button(text: str, data: str) -> dict[str, str]:
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback data exceeds 64 bytes")
    return {"text": text, "callback_data": data}


def _url_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


def _token(watch_id: str) -> str:
    return hashlib.blake2s(watch_id.encode(), digest_size=6).hexdigest()


def _codes(text: str) -> list[str]:
    values = [item.upper() for item in re.split(r"[\s,]+", text.strip()) if item]
    if not values:
        raise ValueError("Enter at least one airport code.")
    if any(not IATA_RE.fullmatch(item) for item in values):
        raise ValueError("Use three-letter IATA codes, separated by commas.")
    if len(values) != len(set(values)):
        raise ValueError("Remove duplicate airport codes.")
    return values


class TelegramControlBot:
    """Private, chat-ID-bound conversational control surface for AutoFly."""

    def __init__(
        self,
        config_path: Path,
        *,
        api: TelegramAPI | None = None,
        check_runner: Callable[[str | None], dict[str, Any]] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config_path = config_path
        config = load_config(config_path)
        telegram = config.notifications.telegram
        if not telegram.enabled or not telegram.control_enabled:
            raise ConfigError(
                "Telegram control requires notifications.telegram.enabled and control_enabled"
            )
        self.chat_id = os.environ.get(telegram.chat_id_env, "")
        if not self.chat_id.isdigit():
            raise ConfigError("Telegram control requires a private TELEGRAM_CHAT_ID")
        self.api = api or TelegramBotAPI(telegram)
        self.poll_timeout = telegram.poll_timeout_seconds
        self.store = ConfigStore(config_path)
        self.drafts: dict[str, WatchDraft] = {}
        self._check_runner = check_runner or self._run_check
        self._check_thread: threading.Thread | None = None
        self._check_guard = threading.Lock()
        self._sleep = sleeper

    def run_forever(self) -> None:
        if self.api.webhook_url():
            raise ConfigError(
                "Telegram getUpdates cannot run while a webhook is configured; remove it first"
            )
        self.api.set_commands()
        self._send("AutoFly Telegram controls are online. Use /menu to begin.", self._menu())
        offset: int | None = None
        failures = 0
        while True:
            try:
                updates = self.api.get_updates(offset, self.poll_timeout)
                failures = 0
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    self.handle_update(update)
            except KeyboardInterrupt:
                return
            except (AutoFlyError, httpx.HTTPError, ValueError):
                failures += 1
                self._sleep(min(60, 2 ** min(failures, 6)))

    def handle_update(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            message = callback.get("message")
            if not self._authorized(message):
                return
            callback_id = callback.get("id")
            if isinstance(callback_id, str):
                self.api.answer_callback(callback_id)
            data = callback.get("data")
            if isinstance(data, str):
                self._handle_callback(data)
            return
        message = update.get("message")
        if not self._authorized(message) or not isinstance(message, dict):
            return
        text = message.get("text")
        if not isinstance(text, str):
            self._send("Please send text or use the buttons below.", self._menu())
            return
        text = text.strip()
        if text.startswith("/"):
            self._handle_command(text.split()[0].split("@")[0].lower())
        elif self.chat_id in self.drafts:
            self._handle_draft_text(text)
        else:
            self._send("Use the menu to manage AutoFly.", self._menu())

    def _authorized(self, message: object) -> bool:
        if not isinstance(message, dict):
            return False
        chat = message.get("chat")
        return (
            isinstance(chat, dict)
            and str(chat.get("id")) == self.chat_id
            and chat.get("type") == "private"
        )

    def _send(self, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> None:
        self.api.send_message(text, keyboard)

    def _menu(self) -> list[list[dict[str, str]]]:
        return [
            [_button("✈️ My watches", "m:w"), _button("+ Add watch", "m:a")],
            [_button("💰 Recent deals", "m:r"), _button("🔎 Check now", "m:c")],
            [_button("🟢 Status", "m:s"), _button("Help", "m:h")],
        ]

    def _handle_command(self, command: str) -> None:
        actions: dict[str, Callable[[], None]] = {
            "/start": self._show_menu,
            "/menu": self._show_menu,
            "/help": self._show_help,
            "/watches": self._show_watches,
            "/add": self._start_add,
            "/results": self._show_results,
            "/status": self._show_status,
            "/check": lambda: self._start_check(None),
            "/cancel": self._cancel_draft,
        }
        action = actions.get(command)
        if action is None:
            self._send("Unknown command. Use /menu.", self._menu())
        else:
            action()

    def _handle_callback(self, data: str) -> None:
        if data == "m:w":
            self._show_watches()
        elif data == "m:a":
            self._start_add()
        elif data == "m:r":
            self._show_results()
        elif data == "m:c":
            self._start_check(None)
        elif data == "m:s":
            self._show_status()
        elif data == "m:h":
            self._show_help()
        elif data == "m:m":
            self._show_menu()
        elif data == "cancel":
            self._cancel_draft()
        elif data == "save":
            self._save_draft()
        elif data.startswith("f:"):
            self._handle_draft_choice(data)
        elif data.startswith(("w:", "t:", "e:", "d:", "x:", "c:")):
            action, token = data.split(":", 1)
            watch = self._watch_for_token(token)
            if watch is None:
                self._send("That watch no longer exists.", self._menu())
            elif action == "w":
                self._show_watch(watch)
            elif action == "t":
                self.store.set_enabled(watch.id, not watch.enabled)
                self._send(f"{watch.id} is now {'paused' if watch.enabled else 'active'}.")
                self._show_watches()
            elif action == "e":
                self._start_edit(watch)
            elif action == "d":
                self._confirm_delete(watch)
            elif action == "x":
                self.store.delete_watch(watch.id)
                self._send(f"Deleted watch {watch.id}.")
                self._show_watches()
            elif action == "c":
                self._start_check(watch.id)

    def _show_menu(self) -> None:
        self._send(
            "✈️ AutoFly\n\nManage watches, search fares, and review deals from this private chat.",
            self._menu(),
        )

    def _show_help(self) -> None:
        self._send(
            "AutoFly never books flights. It searches configured routes and sends links when "
            "every saved rule passes.\n\nUse Add watch for a guided setup. Use My watches "
            "to edit, pause, delete, or search one watch. /cancel exits setup at any time.",
            self._menu(),
        )

    def _show_watches(self) -> None:
        watches = self.store.load().watches
        if not watches:
            self._send(
                "No watches yet. Create your first flight watch.",
                [[_button("+ Add watch", "m:a")]],
            )
            return
        keyboard = [
            [_button(f"{'🟢' if watch.enabled else '⏸'} {watch.id}", f"w:{_token(watch.id)}")]
            for watch in watches
        ]
        keyboard.append([_button("+ Add watch", "m:a"), _button("Main menu", "m:m")])
        self._send(f"Your flight watches ({len(watches)}):", keyboard)

    def _watch_for_token(self, token: str) -> WatchConfig | None:
        return next(
            (watch for watch in self.store.load().watches if _token(watch.id) == token), None
        )

    def _show_watch(self, watch: WatchConfig) -> None:
        dates = watch.dates.model_dump(mode="json", by_alias=True, exclude_none=True)
        date_text = ", ".join(f"{key}: {value}" for key, value in dates.items() if key != "mode")
        text = (
            f"{'🟢 Active' if watch.enabled else '⏸ Paused'} · {watch.id}\n\n"
            f"Routes: {', '.join(watch.origins)} → {', '.join(watch.destinations)}\n"
            f"Trip: {watch.trip.type.replace('_', ' ')}, {watch.trip.cabin}, "
            f"{watch.trip.adults} adult(s)\n"
            f"Dates: {watch.dates.mode} · {date_text}\n"
            f"Deal: below {watch.deal.currency} {watch.deal.maximum_price:g}\n"
            f"Stops: {'any' if watch.deal.max_stops is None else watch.deal.max_stops}\n"
            f"Self-transfer: {'allowed' if watch.deal.allow_self_transfer else 'not allowed'}"
        )
        token = _token(watch.id)
        keyboard = [
            [_button("Search now", f"c:{token}"), _button("Edit", f"e:{token}")],
            [
                _button("Pause" if watch.enabled else "Resume", f"t:{token}"),
                _button("Delete", f"d:{token}"),
            ],
            [_button("Back to watches", "m:w")],
        ]
        self._send(text, keyboard)

    def _confirm_delete(self, watch: WatchConfig) -> None:
        token = _token(watch.id)
        self._send(
            f"Delete {watch.id}? Fare history remains in SQLite, but monitoring stops.",
            [[_button("Yes, delete", f"x:{token}"), _button("Keep it", f"w:{token}")]],
        )

    def _start_add(self) -> None:
        self.drafts[self.chat_id] = WatchDraft()
        self._prompt_draft()

    def _start_edit(self, watch: WatchConfig) -> None:
        values = watch.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.drafts[self.chat_id] = WatchDraft(original_id=watch.id, step="origins", values=values)
        self._send(f"Editing {watch.id}. Send new values as prompted; /cancel keeps the original.")
        self._prompt_draft()

    def _cancel_draft(self) -> None:
        if self.drafts.pop(self.chat_id, None) is None:
            self._send("There is no setup in progress.", self._menu())
        else:
            self._send("Watch setup cancelled. Nothing was changed.", self._menu())

    def _prompt_draft(self) -> None:
        draft = self.drafts[self.chat_id]
        prompts: dict[str, tuple[str, list[list[dict[str, str]]] | None]] = {
            "id": ("Choose a short watch ID, for example kerala-uae.", None),
            "origins": (
                "Send origin airport codes separated by commas, for example COK, CCJ, TRV.",
                None,
            ),
            "destinations": ("Send destination airport codes, for example DXB, AUH, AAN.", None),
            "trip": (
                "One-way or round-trip?",
                [
                    [
                        _button("One way", "f:trip:one_way"),
                        _button("Round trip", "f:trip:round_trip"),
                    ]
                ],
            ),
            "cabin": (
                "Choose a cabin.",
                [
                    [
                        _button("Economy", "f:cabin:economy"),
                        _button("Premium economy", "f:cabin:premium_economy"),
                    ],
                    [_button("Business", "f:cabin:business"), _button("First", "f:cabin:first")],
                ],
            ),
            "adults": ("How many adults? Send a number from 1 to 9.", None),
            "date_mode": ("How should dates work?", self._date_mode_keyboard(draft)),
            "dates": (self._date_prompt(draft), None),
            "currency": (
                "Send the three-letter fare currency, for example INR, GBP, USD, or EUR.",
                None,
            ),
            "maximum_price": (
                "What is the maximum total price? Send a number without a currency symbol.",
                None,
            ),
            "max_stops": (
                "Maximum stops?",
                [
                    [_button("Direct", "f:stops:0"), _button("Up to 1", "f:stops:1")],
                    [_button("Up to 2", "f:stops:2"), _button("Any stops", "f:stops:any")],
                ],
            ),
            "max_layover": (
                "Maximum layover hours? Send a number from 0 to 72, or skip.",
                [[_button("Skip limit", "f:layover:skip")]],
            ),
            "self_transfer": (
                "Allow self-transfer itineraries?",
                [[_button("No", "f:self:no"), _button("Yes", "f:self:yes")]],
            ),
            "cooldown": (
                "Hours to wait before alerting on a reappearing deal? "
                "Send 0 or more (24 is typical).",
                None,
            ),
            "price_drop": (
                "Minimum price drop for another alert? "
                "Send a positive amount in the watch currency.",
                None,
            ),
        }
        if draft.step == "review":
            self._review_draft()
            return
        text, keyboard = prompts[draft.step]
        cancel = [_button("Cancel setup", "cancel")]
        self._send(text, [*(keyboard or []), cancel])

    def _date_mode_keyboard(self, draft: WatchDraft) -> list[list[dict[str, str]]]:
        if draft.values.get("trip", {}).get("type") == "round_trip":
            return [[_button("Exact return dates", "f:dates:exact")]]
        return [
            [_button("Exact date", "f:dates:exact"), _button("Date range", "f:dates:range")],
            [_button("Rolling window", "f:dates:rolling")],
        ]

    def _date_prompt(self, draft: WatchDraft) -> str:
        mode = draft.values.get("date_mode")
        if mode == "rolling":
            return "Send start and end days from now, for example 1, 30."
        if mode == "range":
            return "Send first and last departure dates as YYYY-MM-DD, YYYY-MM-DD."
        if draft.values.get("trip", {}).get("type") == "round_trip":
            return "Send departure and return dates as YYYY-MM-DD, YYYY-MM-DD."
        return "Send the departure date as YYYY-MM-DD."

    def _handle_draft_choice(self, data: str) -> None:
        draft = self.drafts.get(self.chat_id)
        if draft is None:
            self._send("That setup has expired. Use Add watch to begin again.", self._menu())
            return
        _, field_name, value = data.split(":", 2)
        expected = {
            "trip": "trip",
            "cabin": "cabin",
            "dates": "date_mode",
            "stops": "max_stops",
            "layover": "max_layover",
            "self": "self_transfer",
        }.get(field_name)
        if expected != draft.step:
            self._send("Please use the latest question.")
            self._prompt_draft()
            return
        if field_name == "trip":
            draft.values["trip"] = {"type": value}
            draft.step = "cabin"
        elif field_name == "cabin":
            draft.values.setdefault("trip", {})["cabin"] = value
            draft.step = "adults"
        elif field_name == "dates":
            draft.values["date_mode"] = value
            draft.step = "dates"
        elif field_name == "stops":
            draft.values.setdefault("deal", {})["max_stops"] = (
                None if value == "any" else int(value)
            )
            draft.step = "max_layover"
        elif field_name == "layover":
            draft.values.setdefault("deal", {})["max_layover_hours"] = None
            draft.step = "self_transfer"
        elif field_name == "self":
            draft.values.setdefault("deal", {})["allow_self_transfer"] = value == "yes"
            draft.step = "cooldown"
        self._prompt_draft()

    def _handle_draft_text(self, text: str) -> None:
        draft = self.drafts[self.chat_id]
        try:
            if draft.step == "id":
                value = text.lower()
                if not WATCH_ID_RE.fullmatch(value):
                    raise ValueError("Use lowercase letters, numbers, hyphens, or underscores.")
                if any(watch.id == value for watch in self.store.load().watches):
                    raise ValueError("That watch ID already exists.")
                draft.values["id"] = value
                draft.values["enabled"] = True
                draft.step = "origins"
            elif draft.step == "origins":
                draft.values["origins"] = _codes(text)
                draft.step = "destinations"
            elif draft.step == "destinations":
                destinations = _codes(text)
                if set(destinations) & set(draft.values["origins"]):
                    raise ValueError("An origin cannot also be a destination.")
                draft.values["destinations"] = destinations
                draft.step = "trip"
            elif draft.step == "adults":
                adults = int(text)
                if not 1 <= adults <= 9:
                    raise ValueError("Adults must be between 1 and 9.")
                draft.values.setdefault("trip", {})["adults"] = adults
                draft.step = "date_mode"
            elif draft.step == "dates":
                draft.values["dates"] = self._parse_dates(draft, text)
                draft.values.pop("date_mode", None)
                draft.step = "currency"
            elif draft.step == "currency":
                currency = text.upper()
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    raise ValueError("Currency must be a three-letter code such as INR.")
                draft.values.setdefault("deal", {})["currency"] = currency
                draft.step = "maximum_price"
            elif draft.step == "maximum_price":
                price = float(text.replace(",", ""))
                if price <= 0:
                    raise ValueError("Maximum price must be greater than zero.")
                draft.values.setdefault("deal", {})["maximum_price"] = price
                draft.step = "max_stops"
            elif draft.step == "max_layover":
                hours = float(text)
                if not 0 <= hours <= 72:
                    raise ValueError("Layover hours must be between 0 and 72.")
                draft.values.setdefault("deal", {})["max_layover_hours"] = hours
                draft.step = "self_transfer"
            elif draft.step == "cooldown":
                hours = float(text)
                if hours < 0:
                    raise ValueError("Cooldown cannot be negative.")
                draft.values.setdefault("notifications", {})["cooldown_hours"] = hours
                draft.step = "price_drop"
            elif draft.step == "price_drop":
                amount = float(text.replace(",", ""))
                if amount <= 0:
                    raise ValueError("Price drop must be greater than zero.")
                draft.values.setdefault("notifications", {})["alert_on_price_drop"] = {
                    "amount": amount
                }
                draft.step = "review"
            else:
                raise ValueError("Use the buttons for this question.")
        except (KeyError, TypeError, ValueError) as exc:
            self._send(f"I couldn't use that: {exc}")
        self._prompt_draft()

    def _parse_dates(self, draft: WatchDraft, text: str) -> dict[str, Any]:
        parts = [item.strip() for item in text.split(",") if item.strip()]
        mode = draft.values.get("date_mode")
        if mode == "rolling":
            if len(parts) != 2:
                raise ValueError("Send two numbers, for example 1, 30.")
            start_day, end_day = (int(item) for item in parts)
            return {"mode": "rolling", "days_from_now": start_day, "days_to": end_day}
        if mode == "range":
            if len(parts) != 2:
                raise ValueError("Send two dates separated by a comma.")
            start_date, end_date = (date.fromisoformat(item) for item in parts)
            return {
                "mode": "range",
                "departure_start": start_date,
                "departure_end": end_date,
            }
        if draft.values.get("trip", {}).get("type") == "round_trip":
            if len(parts) != 2:
                raise ValueError("Send departure and return dates separated by a comma.")
            departure, return_date = (date.fromisoformat(item) for item in parts)
            return {"mode": "exact", "departure": departure, "return": return_date}
        if len(parts) != 1:
            raise ValueError("Send one date as YYYY-MM-DD.")
        return {"mode": "exact", "departure": date.fromisoformat(parts[0])}

    def _draft_watch(self, draft: WatchDraft) -> WatchConfig:
        values = {**draft.values}
        values.pop("date_mode", None)
        return WatchConfig.model_validate(values)

    def _review_draft(self) -> None:
        draft = self.drafts[self.chat_id]
        try:
            watch = self._draft_watch(draft)
        except ValueError as exc:
            self._send(f"This watch is not valid: {exc}\nUse /cancel and try again.")
            return
        self._send(
            f"Review {watch.id}\n\n"
            f"{', '.join(watch.origins)} → {', '.join(watch.destinations)}\n"
            f"{watch.trip.type.replace('_', ' ')} · {watch.trip.cabin} · "
            f"{watch.trip.adults} adult(s)\n"
            f"Dates: {watch.dates.mode}\n"
            f"Alert below {watch.deal.currency} {watch.deal.maximum_price:g}\n"
            f"Maximum stops: {'any' if watch.deal.max_stops is None else watch.deal.max_stops}\n"
            f"Self-transfers: {'allowed' if watch.deal.allow_self_transfer else 'not allowed'}",
            [[_button("Save watch", "save"), _button("Cancel", "cancel")]],
        )

    def _save_draft(self) -> None:
        draft = self.drafts.get(self.chat_id)
        if draft is None or draft.step != "review":
            self._send("There is no completed watch to save.", self._menu())
            return
        try:
            watch = self._draft_watch(draft)
            self.store.save_watch(watch, original_id=draft.original_id)
        except (ConfigError, ValueError) as exc:
            self._send(f"I couldn't save this watch: {exc}\nNothing was changed.")
            return
        self.drafts.pop(self.chat_id, None)
        self._send(f"Saved {watch.id}. Automatic monitoring will include it.")
        self._show_watch(watch)

    def _show_results(self) -> None:
        config = self.store.load()
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            rows = database.dashboard_history(limit=5, qualifying=True)
        finally:
            database.close()
        if not rows:
            self._send("No recent fares have passed every watch rule yet.", self._menu())
            return
        lines = ["💰 Recent matching deals"]
        keyboard: list[list[dict[str, str]]] = []
        for row in rows:
            lines.append(
                f"\n{row['origin']} → {row['destination']} · "
                f"{format_price(Decimal(row['price']), row['currency'])}\n"
                f"{row.get('airline') or 'Airline unknown'} · {row['departure_at']}"
            )
            url = row.get("booking_url")
            if isinstance(url, str) and url.startswith("https://"):
                keyboard.append([_url_button(f"Open {row['origin']} → {row['destination']}", url)])
        keyboard.append([_button("Main menu", "m:m")])
        self._send("\n".join(lines), keyboard)

    def _show_status(self) -> None:
        config = self.store.load()
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            summary = database.dashboard_summary()
            cycles = database.recent_cycles(1)
        finally:
            database.close()
        active = sum(watch.enabled for watch in config.watches)
        last = cycles[0] if cycles else None
        last_text = (
            f"{last['display_status']} · {last['started_at']}" if last else "No searches recorded"
        )
        self._send(
            f"🟢 AutoFly status\n\n"
            f"Active watches: {active}/{len(config.watches)}\n"
            f"Schedule: every {config.scheduler.interval_hours:g} hours "
            f"(+ up to {config.scheduler.jitter_minutes} min jitter)\n"
            f"Tracked itineraries: {summary['available_itineraries']}\n"
            f"Last search: {last_text}",
            self._menu(),
        )

    def _start_check(self, watch_id: str | None) -> None:
        with self._check_guard:
            if self._check_thread is not None and self._check_thread.is_alive():
                self._send("A fare search is already running. I'll report when it finishes.")
                return
            label = watch_id or "all active watches"
            self._send(f"🔎 Starting a fare search for {label}. This can take a few minutes.")

            def worker() -> None:
                try:
                    result = self._check_runner(watch_id)
                    self._send(
                        f"Search finished: {result.get('status', 'unknown')}\n"
                        f"Routes attempted: {result.get('routes_attempted', 0)}\n"
                        f"Fares reviewed: {result.get('candidate_count', 0)}\n"
                        f"Matching deals: {result.get('qualifying_count', 0)}\n"
                        f"Notifications sent: {result.get('notifications_sent', 0)}",
                        self._menu(),
                    )
                except Exception:
                    self._send(
                        "The fare search could not finish. "
                        "Check AutoFly's service logs for details.",
                        self._menu(),
                    )

            self._check_thread = threading.Thread(
                target=worker, name="autofly-telegram-check", daemon=True
            )
            self._check_thread.start()

    def _run_check(self, watch_id: str | None) -> dict[str, Any]:
        config = load_config(self.config_path)
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            if config.sources.flight_goat.enabled:
                source: Any = FlightGoatSource(config.sources.flight_goat)
            elif config.sources.playwright.enabled:
                source = PlaywrightSource(config.sources.playwright)
            else:
                raise ConfigError("Enable at least one fare source")
            notifiers: list[Any] = []
            if config.notifications.telegram.enabled:
                notifiers.append(TelegramNotifier(config.notifications.telegram))
            if config.notifications.webhook.enabled:
                notifiers.append(WebhookNotifier(config.notifications.webhook))
            with ProcessLock(config.scheduler.lock_path):
                return WatchEngine(config, database, source, notifiers).run(
                    {watch_id} if watch_id else None
                )
        finally:
            database.close()

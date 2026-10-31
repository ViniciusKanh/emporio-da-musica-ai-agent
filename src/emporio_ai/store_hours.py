"""'Is the store open now?' computed from the opening hours extracted from the policy manual.

Campo Grande/MS uses UTC-4 all year (no daylight saving time since 2019), so a fixed
offset is used and no time-zone database is required (works on Windows without tzdata).
Public holidays are not in the data, so the answer always carries that caveat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from .text_utils import normalize

STORE_TZ = timezone(timedelta(hours=-4), "America/Campo_Grande")
DAY_NAMES_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
DAY_NAMES_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass(frozen=True)
class OpenStatus:
    is_open: bool
    now: datetime
    closes_at: time | None = None
    next_open_day: int | None = None
    next_open_at: time | None = None


def _days_of(label: str) -> list[int]:
    key = normalize(label)
    if "segunda a sexta" in key:
        return [0, 1, 2, 3, 4]
    if "sabado" in key:
        return [5]
    if "domingo" in key:
        return [6]
    return []


def weekly_schedule(hours: list[tuple[str, str]]) -> dict[int, tuple[time, time] | None]:
    schedule: dict[int, tuple[time, time] | None] = {}
    for label, value in hours:
        match = re.search(r"(\d{2}):(\d{2})\s+[àa]s\s+(\d{2}):(\d{2})", value)
        span = (time(int(match.group(1)), int(match.group(2))), time(int(match.group(3)), int(match.group(4)))) \
            if match else None
        for day in _days_of(label):
            schedule[day] = span
    return schedule


def open_status(hours: list[tuple[str, str]], now: datetime | None = None) -> OpenStatus | None:
    schedule = weekly_schedule(hours)
    if not schedule:
        return None
    now = (now or datetime.now(STORE_TZ)).astimezone(STORE_TZ)
    today = schedule.get(now.weekday())
    if today and today[0] <= now.time() < today[1]:
        return OpenStatus(True, now, closes_at=today[1])
    for offset in range(0, 8):
        day = (now.weekday() + offset) % 7
        span = schedule.get(day)
        if span and (offset > 0 or now.time() < span[0]):
            return OpenStatus(False, now, next_open_day=day, next_open_at=span[0])
    return OpenStatus(False, now)


def describe(status: OpenStatus, lang: str = "pt") -> str:
    now = status.now
    if lang == "en":
        stamp = f"{DAY_NAMES_EN[now.weekday()]} {now:%H:%M} in Campo Grande"
        if status.is_open:
            return f"Right now ({stamp}) the store is **open** until {status.closes_at:%H:%M}."
        nxt = "" if status.next_open_day is None else \
            f" It opens again {'today' if status.next_open_day == now.weekday() else DAY_NAMES_EN[status.next_open_day]}" \
            f" at {status.next_open_at:%H:%M}."
        return f"Right now ({stamp}) the store is **closed**.{nxt}"
    stamp = f"{DAY_NAMES_PT[now.weekday()]}, {now:%H:%M} em Campo Grande"
    if status.is_open:
        return f"Agora ({stamp}) a loja está **aberta** até as {status.closes_at:%H:%M}."
    if status.next_open_day is None:
        return f"Agora ({stamp}) a loja está **fechada**."
    when = "hoje" if status.next_open_day == now.weekday() else \
        ("amanhã" if status.next_open_day == (now.weekday() + 1) % 7 else DAY_NAMES_PT[status.next_open_day])
    return f"Agora ({stamp}) a loja está **fechada** — abre {when} às {status.next_open_at:%H:%M}."

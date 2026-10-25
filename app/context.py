"""What every handler gets: who is talking, the bot's 'today' and the database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.engine import Engine

from app.config import Config


@dataclass
class Ctx:
    chat_id: int
    user_id: int
    today: date
    engine: Engine
    cfg: Config

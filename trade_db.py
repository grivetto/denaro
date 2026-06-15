"""Trade database module for Squadra bot."""
import json
import os
import sqlite3
import threading
from typing import Optional


class TradeDB:
    """Minimal SQLite-based trade state persistence."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS bot_state ("
                "  bot_name TEXT PRIMARY KEY,"
                "  state TEXT"
                ")"
            )
            conn.commit()
            conn.close()

    def save_bot_state(self, bot_name: str, **kwargs) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            state = json.dumps(kwargs)
            conn.execute(
                "INSERT OR REPLACE INTO bot_state (bot_name, state) VALUES (?, ?)",
                (bot_name, state),
            )
            conn.commit()
            conn.close()

    def load_bot_state(self, bot_name: str) -> Optional[dict]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "SELECT state FROM bot_state WHERE bot_name = ?", (bot_name,)
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
            return None

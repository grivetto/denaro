"""denaro-antigravity services/dashboard.py – FastAPI Web Server.

Serves the front-end dashboard and exposes endpoints to fetch stats, trades, and trigger actions.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger

from core.engine import Settings, settings

if TYPE_CHECKING:
    from main import TradingBot

BASE = Path(__file__).resolve().parents[1]

def make_app(bot: TradingBot) -> FastAPI:
    app = FastAPI(title="denaro-antigravity Dashboard")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Root HTML Route ───────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def get_index():
        html_file = BASE / "templates" / "index.html"
        if not html_file.exists():
            # Fallback inline minimal HTML if index.html is missing
            return HTMLResponse(
                "<html><body><h1>denaro-antigravity Dashboard</h1><p>index.html not found.</p></body></html>"
            )
        return HTMLResponse(html_file.read_text(encoding="utf-8"))

    # ── API Endpoints ─────────────────────────────────────────────────────────
    @app.get("/api/state")
    async def get_state():
        try:
            # Collect exchange balances in parallel with timeout
            balances = {}
            async def _fetch_balance(name: str, wrapper):
                try:
                    bal = await asyncio.wait_for(wrapper.fetch_balance(), timeout=10.0)
                    return name, bal.get("free", {})
                except asyncio.TimeoutError:
                    logger.warning(f"Dashboard: Balance fetch timed out for {name}")
                    return name, {}
                except Exception as e:
                    logger.error(f"Dashboard: Balance fetch failed for {name}: {e}")
                    return name, {}
            
            results = await asyncio.gather(
                *[_fetch_balance(name, wrapper) for name, wrapper in bot.exchanges.items()]
            )
            for name, bal in results:
                balances[name] = bal

            # Collect strategies information
            strategies_data = []
            for s in bot.strategies:
                s_info = {
                    "name": s.name,
                    "symbol": s.symbol,
                    "capital": s.capital,
                    "is_paused": s.is_paused,
                    "positions_count": len(s._positions),
                    "open_positions": [
                        {
                            "symbol": p.symbol,
                            "side": p.side.value,
                            "amount": p.amount,
                            "entry_price": p.entry_price,
                            "tp_price": p.tp_price,
                            "sl_price": p.sl_price,
                            "pnl": p.pnl,
                            "order_id": p.order_id
                        }
                        for p in s._positions.values()
                    ]
                }
                
                # Fetch grid levels if grid trader
                if s.name == "GridTrader" and hasattr(s, "get_status"):
                    grid_stats = await s.get_status()
                    s_info["grid"] = grid_stats
                    
                strategies_data.append(s_info)

            # System Statistics
            stats = bot.db.stats()
            recent_trades = bot.db.get_recent_trades(20)

            return {
                "system": {
                    "is_halted": bot.risk.is_halted,
                    "dry_run": settings.dry_run,
                    "daily_pnl": bot.risk.daily_pnl
                },
                "balances": balances,
                "strategies": strategies_data,
                "stats": stats,
                "recent_trades": recent_trades
            }
        except Exception as e:
            logger.error(f"Dashboard state fetch failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Operational Endpoints ─────────────────────────────────────────────────
    @app.post("/api/action/pause")
    async def post_pause():
        for s in bot.strategies:
            s.pause()
        logger.warning("All strategies paused via Dashboard request.")
        return {"status": "ok", "message": "All strategies paused."}

    @app.post("/api/action/resume")
    async def post_resume():
        bot.risk.resume_all()
        for s in bot.strategies:
            s.resume()
        logger.info("All strategies resumed via Dashboard request.")
        return {"status": "ok", "message": "All strategies resumed."}

    @app.post("/api/action/halt")
    async def post_halt():
        bot.risk.halt_all()
        for s in bot.strategies:
            s.pause()
        logger.critical("Emergency halt activated via Dashboard request.")
        return {"status": "ok", "message": "Emergency halt activated."}

    @app.post("/api/action/grid_reset")
    async def post_grid_reset():
        grid = next((s for s in bot.strategies if s.name == "GridTrader"), None)
        if grid is None:
            raise HTTPException(status_code=400, detail="GridTrader strategy not active.")
        try:
            ticker = await grid.exchange.fetch_ticker(grid.symbol)
            mid = float(ticker["last"])
            await grid.reset_grid(mid)
            return {"status": "ok", "message": f"Grid reset successfully around {mid:.4f}."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app

class DashboardServer:
    def __init__(self, bot: TradingBot, settings_ref: Settings = settings):
        self._bot = bot
        self.settings = settings_ref
        self._app = make_app(self._bot)
        self._server_task: asyncio.Task | None = None

    async def start(self) -> None:
        cfg = uvicorn.Config(
            app=self._app,
            host=self.settings.dashboard_host,
            port=self.settings.dashboard_port,
            log_level="warning",
            loop="asyncio"
        )
        server = uvicorn.Server(cfg)
        self._server_task = asyncio.create_task(server.serve())
        logger.info(f"FastAPI Web Dashboard server launched on http://{self.settings.dashboard_host}:{self.settings.dashboard_port}")

    async def stop(self) -> None:
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            logger.info("FastAPI Web Dashboard server stopped successfully.")

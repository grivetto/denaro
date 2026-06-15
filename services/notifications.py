"""denaro-antigravity services/notifications.py – Telegram Notification Service.

Sends outbound alerts and provides command handlers (/status, /pnl, /pause, /resume, /halt, /grid_reset) to control the bot.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

import asyncio
from loguru import logger
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from core.engine import Settings, settings, TradeDB
from core.optimizer import ParameterOptimizer

if TYPE_CHECKING:
    from main import TradingBot

def get_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["💰 Bilancio", "📊 Grid Bot"],
            ["🎯 MC2 Sniper", "🛡️ Servizi"],
            ["💵 DCA", "🔐 Cassaforte"],
            ["🖥️ Sistema", "🌐 Dashboard"]
        ],
        resize_keyboard=True
    )

class NotificationService:
    def __init__(self, trading_bot: TradingBot, settings_ref: Settings = settings):
        self._bot = trading_bot
        self.settings = settings_ref
        self._app: Application | None = None
        self._enabled = False

    async def start(self) -> None:
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id

        if not token or not chat_id or token.startswith("your_") or chat_id.startswith("your_"):
            logger.warning("Telegram configuration missing or using placeholders. Outbound alerts and command triggers are DISABLED.")
            return

        try:
            self._app = Application.builder().token(token).build()

            # Register standard interactive handlers
            handlers = [
                ("start", self._cmd_start),
                ("status", self._cmd_status),
                ("pause", self._cmd_pause),
                ("resume", self._cmd_resume),
                ("halt", self._cmd_halt),
                ("pnl", self._cmd_pnl),
                ("grid_reset", self._cmd_grid_reset),
                ("optimize", self._cmd_optimize),
                ("apply_opt", self._cmd_apply_opt),
            ]
            for cmd, fn in handlers:
                self._app.add_handler(CommandHandler(cmd, fn))

            # Register text message dashboard handler
            self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

            await self._app.initialize()
            await self._app.start()
            if self.settings.telegram_polling:
                await self._app.updater.start_polling(allowed_updates=["message"])
                logger.info("Telegram notification service initialized and polling successfully.")
            else:
                logger.info("Telegram notification service initialized (sending alerts only; polling disabled).")
            self._enabled = True
            await self.send("🚀 <b>denaro-antigravity</b> online | Dry Run Mode = " + str(self.settings.dry_run))
        except Exception as e:
            logger.error(f"Failed to initialize Telegram Service: {e}. Bot will run without Telegram notifications.")

    async def stop(self) -> None:
        if self._app and self._enabled:
            try:
                await self.send("🛑 <b>denaro-antigravity</b> offline.")
                if self.settings.telegram_polling:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                logger.info("Telegram notification service shutdown successfully.")
            except Exception as e:
                logger.error(f"Telegram service shutdown error: {e}")

    async def send(self, text: str) -> None:
        if not self._enabled or not self._app:
            return
        try:
            await self._app.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")

    # ── Outbound Alerts ───────────────────────────────────────────────────────
    async def alert_trade(self, strategy: str, side: str, symbol: str, amount: float, price: float, reason: str = "") -> None:
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        mode_tag = " [DRY]" if self.settings.dry_run else ""
        msg = (
            f"{emoji} <b>TRADE {side.upper()} ({strategy}){mode_tag}</b>\n"
            f"  Symbol: {symbol}\n"
            f"  Amount: {amount:.6f}\n"
            f"  Price: {price:.4f} USD\n"
        )
        if reason:
            msg += f"  Reason: {reason}\n"
        await self.send(msg)

    async def alert_pnl(self, strategy: str, pnl: float, symbol: str = "") -> None:
        emoji = "💰" if pnl >= 0 else "📉"
        msg = f"{emoji} <b>PnL ({strategy})</b> {symbol} {pnl:+.4f} USD"
        await self.send(msg)

    async def alert_halt(self, reason: str) -> None:
        await self.send(f"🚨 <b>EMERGENCY HALT</b>\n{reason}")

    async def alert_error(self, strategy: str, error: str) -> None:
        await self.send(f"⚠️ <b>ERROR ({strategy})</b>\n{html.escape(error)}")

    # ── Command Handlers ──────────────────────────────────────────────────────
    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_html(
                "🤖 <b>denaro-antigravity Bot</b> online.\n"
                "Benvenuto Sergio.\n\n"
                "Usa il menu in basso o i pulsanti della console per monitorare la flotta di trading.",
                reply_markup=get_keyboard()
            )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
            
        lines = ["📊 <b>Stato Portafoglio</b>"]
        lines.append(f"  • Halted (Bloccato): <code>{self._bot.risk.is_halted}</code>")
        lines.append(f"  • Dry Run Mode: <code>{self.settings.dry_run}</code>")
        lines.append(f"  • PnL Giornaliero: <code>{self._bot.risk.daily_pnl:+.4f} USD</code>\n")
        
        lines.append("🤖 <b>Strategie Attive:</b>")
        for s in self._bot.strategies:
            state = "⏸ IN PAUSA" if s.is_paused else "▶ IN ESECUZIONE"
            lines.append(f"  • <b>{s.name}</b> ({s.symbol}): {state}")
            lines.append(f"    Posizioni attive: <code>{len(s._positions)}</code>")
            
            # Additional detail for grid
            if s.name == "GridTrader" and hasattr(s, "get_status"):
                status = await s.get_status()
                lines.append(f"    Griglia: {status['open_buys']} BUY | {status['open_sells']} SELL (tot {status['total_levels']})")
                
        await update.message.reply_html("\n".join(lines), reply_markup=get_keyboard())

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        for s in self._bot.strategies:
            s.pause()
        await update.message.reply_html("⏸ <b>Tutte le strategie sono state messe in pausa.</b>", reply_markup=get_keyboard())

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self._bot.risk.resume_all()
        for s in self._bot.strategies:
            s.resume()
        await update.message.reply_html("▶ <b>Trading riavviato. Tutte le strategie sono attive.</b>", reply_markup=get_keyboard())

    async def _cmd_halt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        self._bot.risk.halt_all()
        for s in self._bot.strategies:
            s.pause()
        await update.message.reply_html("🚨 <b>ARRESTO D'EMERGENZA ATTIVATO.</b> Strategie in pausa.", reply_markup=get_keyboard())

    async def _cmd_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        pnl = self._bot.risk.daily_pnl
        emoji = "💰" if pnl >= 0 else "📉"
        await update.message.reply_html(f"{emoji} <b>PnL Giornaliero Netto:</b> <code>{pnl:+.4f} USD</code>", reply_markup=get_keyboard())

    async def _cmd_grid_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        grid = next((s for s in self._bot.strategies if s.name == "GridTrader"), None)
        if grid is None:
            await update.message.reply_html("❌ Strategia GridTrader non attiva.", reply_markup=get_keyboard())
            return
            
        try:
            ticker = await grid.exchange.fetch_ticker(grid.symbol)
            mid = float(ticker["last"])
            await grid.reset_grid(mid)
            await update.message.reply_html(f"✅ <b>Griglia ricalibrata con successo</b> attorno a: <code>{mid:.4f} USD</code>", reply_markup=get_keyboard())
        except Exception as e:
            await update.message.reply_html(f"❌ <b>Ricalibrazione fallita:</b> {e}", reply_markup=get_keyboard())

    # ── Text Keyboard Dashboard Handlers ─────────────────────────────────────
    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        
        text = update.message.text.strip()
        resp = ""
        
        if "Bilancio" in text:
            resp = await self._cmd_balance_text()
        elif "Grid Bot" in text:
            resp = await self._cmd_grid_text()
        elif "MC2 Sniper" in text:
            resp = await self._cmd_mc2_text()
        elif "Servizi" in text:
            resp = await self._cmd_services_text()
        elif "DCA" in text:
            resp = await self._cmd_dca_text()
        elif "Cassaforte" in text:
            resp = await self._cmd_vault_text()
        elif "Sistema" in text:
            resp = await self._cmd_system_text()
        elif "Dashboard" in text:
            resp = f"🌐 <b>Dashboard Web</b>\n\n• Nuvola Master: <code>http://87.106.3.15:8000</code>\n• MC2 Secondary: <code>http://192.168.1.99:8000</code>\n• MARCODG1 Secondary: <code>http://87.106.222.123:8000</code>"
        else:
            resp = "Clicca un pulsante del menu."
            
        if resp:
            await update.message.reply_html(resp, reply_markup=get_keyboard())

    async def _cmd_balance_text(self) -> str:
        try:
            ex = self._bot.exchanges.get("binance")
            if not ex:
                return "⚠️ Exchange Binance non configurato."
            
            bal = await ex.fetch_balance()
            free = bal.get("free", {})
            
            eur_free = float(free.get("EUR", 0.0) or 0.0)
            usdt_free = float(free.get("USDT", 0.0) or 0.0)
            btc_free = float(free.get("BTC", 0.0) or 0.0)
            sol_free = float(free.get("SOL", 0.0) or 0.0)
            bnb_free = float(free.get("BNB", 0.0) or 0.0)
            
            btc_price = 60000.0
            sol_price = 150.0
            usdt_price = 0.92
            
            try:
                t = await ex.fetch_ticker("BTC/EUR")
                btc_price = float(t.get("last", btc_price))
            except: pass
            
            try:
                t = await ex.fetch_ticker("SOL/EUR")
                sol_price = float(t.get("last", sol_price))
            except: pass
            
            try:
                t = await ex.fetch_ticker("EUR/USDT")
                usdt_price = 1.0 / float(t.get("last", 1.08))
            except: pass
            
            total_eur = eur_free + (usdt_free * usdt_price) + (btc_free * btc_price) + (sol_free * sol_price)
            
            from datetime import datetime
            mode = "DRY RUN" if self.settings.dry_run else "REALE"
            return (
                f"💰 <b>BILANCIO BINANCE ({mode})</b>\n"
                f"─────────────────\n"
                f"📅 Ora: {datetime.now().strftime('%d/%m %H:%M')}\n"
                f"💶 EUR: <b>€{eur_free:.2f}</b>\n"
                f"💵 USDT: ${usdt_free:.2f}\n"
                f"₿ BTC: {btc_free:.6f} (€{btc_free*btc_price:.2f})\n"
                f"☀️ SOL: {sol_free:.4f} (€{sol_free*sol_price:.2f})\n"
                f"🔸 BNB: {bnb_free:.4f} (fees)\n"
                f"─────────────────\n"
                f"💰 <b>TOTALE: €{total_eur:.2f}</b>\n"
                f"📉 Capitale iniziale: €{self.settings.total_capital_eur:.2f}"
            )
        except Exception as e:
            return f"⚠️ Errore lettura bilancio: {e}"

    async def _cmd_grid_text(self) -> str:
        grid = next((s for s in self._bot.strategies if s.name == "GridTrader"), None)
        if not grid:
            return "❌ Strategia GridTrader non attiva su questa istanza."
            
        try:
            status = await grid.get_status()
            stats = self._bot.db.stats("GridTrader")
            return (
                f"📊 <b>GRID BOT — {grid.symbol}</b>\n"
                f"─────────────────\n"
                f"Stato: {'⏸ IN PAUSA' if grid.is_paused else '▶ IN ESECUZIONE'}\n"
                f"Prezzo Medio: {status['mid_price']:.4f} USD\n"
                f"Livelli Attivi: {status['total_levels']} / {self.settings.grid_levels}\n"
                f"Ordini Aperti: {status['open_buys']} BUY | {status['open_sells']} SELL\n"
                f"─────────────────\n"
                f"📈 PnL Oggi: <b>{self._bot.db.daily_pnl('GridTrader'):+.4f} USD</b>\n"
                f"🏆 PnL Storico: <b>{stats.get('total_pnl', 0.0):+.4f} USD</b> (Win: {stats.get('win_rate', 0.0)}%)"
            )
        except Exception as e:
            return f"⚠️ Errore lettura griglia: {e}"

    async def _cmd_mc2_text(self) -> str:
        scalper = next((s for s in self._bot.strategies if s.name == "Scalper"), None)
        if not scalper:
            return "❌ Strategia Scalper non attiva su questa istanza."
            
        try:
            stats = self._bot.db.stats("Scalper")
            open_pos = len(scalper._positions)
            return (
                f"🎯 <b>SENTINEL / TEMPEST SCALPER</b>\n"
                f"─────────────────\n"
                f"Coppia Tradata: {scalper.symbol}\n"
                f"Stato: {'⏸ IN PAUSA' if scalper.is_paused else '▶ IN ESECUZIONE'}\n"
                f"Posizioni Attive: <code>{open_pos}</code>\n"
                f"─────────────────\n"
                f"📈 PnL Oggi: <b>{self._bot.db.daily_pnl('Scalper'):+.4f} USD</b>\n"
                f"🏆 PnL Storico: <b>{stats.get('total_pnl', 0.0):+.4f} USD</b> (Win: {stats.get('win_rate', 0.0)}%)"
            )
        except Exception as e:
            return f"⚠️ Errore scalper: {e}"

    async def _cmd_services_text(self) -> str:
        services = [
            ("nuvola", "vulcan.service", "VULCAN Grid SOL/EUR"),
            ("mc2", "sentinel.service", "SENTINEL Scalper BTC/EUR"),
            ("MARCODG1", "tempest.service", "TEMPEST Scalper SOL/EUR")
        ]
        
        lines = ["🛡️ <b>STATO SERVIZI SYSTEMD</b>", "─────────────────"]
        active = 0
        for host, svc, desc in services:
            try:
                if host == "nuvola":
                    proc = await asyncio.create_subprocess_exec(
                        "systemctl", "is-active", svc,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        "ssh", host, f"systemctl is-active {svc}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
                is_active = stdout.decode().strip() == "active"
            except Exception as e:
                logger.error(f"Error checking service {svc} on {host}: {e}")
                is_active = False
                
            icon = "✅" if is_active else "❌"
            if is_active: active += 1
            lines.append(f"{icon} <b>{host}</b>: {desc}")
            
        lines.append("─────────────────")
        lines.append(f"Totale: {active} attivi su {len(services)}")
        return "\n".join(lines)

    async def _cmd_dca_text(self) -> str:
        mode = "DRY RUN (Simulazione)" if self.settings.dry_run else "LIVE TRADING (Reale)"
        return (
            f"💵 <b>denaro-antigravity DCA</b>\n"
            f"─────────────────\n"
            f"Stato: 🟢 <b>ATTIVO</b>\n"
            f"Modalità: <code>{mode}</code>\n"
            f"Capitale Allocato: €{self.settings.total_capital_eur:.2f}\n"
            f"Soglia minima DCA: €5.00"
        )

    async def _cmd_vault_text(self) -> str:
        try:
            stats = self._bot.db.stats()
            total_pnl = stats.get("total_pnl", 0.0)
            trades_count = stats.get("count", 0)
            win_rate = stats.get("win_rate", 0.0)
            return (
                f"🔐 <b>CASSAFORTE ANTIGRAVITY</b>\n"
                f"─────────────────\n"
                f"PnL Realizzato: <b>{total_pnl:+.4f} USD</b>\n"
                f"Esecuzioni Totali: {trades_count}\n"
                f"Tasso di Vincita: {win_rate}%\n"
                f"📅 Aggiornato in tempo reale"
            )
        except Exception as e:
            return f"⚠️ Errore lettura cassaforte: {e}"

    async def _cmd_system_text(self) -> str:
        try:
            import psutil
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent
            return (
                f"🖥️ <b>NUVOLA SYSTEM TELEMETRY</b>\n"
                f"─────────────────\n"
                f"CPU: {cpu}%\n"
                f"RAM: {ram}%\n"
                f"Disk: {disk}%"
            )
        except Exception as e:
            return f"⚠️ Errore lettura sistema: {e}"

    async def _cmd_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
            
        await update.message.reply_html(
            "🧠 <b>OPTIMIZER: Avvio Agente di Ottimizzazione</b>\n"
            "Sto scaricando le candele storiche 1m da Binance e avviando la griglia di backtesting per ottimizzare i parametri dello Scalper. Attendere..."
        )
        
        async def run_opt_task():
            try:
                symbol = self.settings.scalper_symbol
                exchange = self.settings.scalper_exchange
                
                optimizer = ParameterOptimizer(symbol, exchange)
                success = await optimizer.fetch_historical_data(limit=1000)
                if not success:
                    await self.send("❌ <b>OPTIMIZER: Errore</b>\nImpossibile scaricare i dati storici.")
                    return
                    
                best_cfg = optimizer.optimize()
                self._last_optimized_cfg = best_cfg
                
                msg = (
                    f"🏆 <b>OTTIMIZZAZIONE COMPLETATA</b>\n"
                    f"Mercato: {symbol} | Exchange: {exchange}\n"
                    f"─────────────────\n"
                    f"💡 <b>Vecchi Parametri:</b>\n"
                    f"  • EMA: {self.settings.scalper_ema_fast}/{self.settings.scalper_ema_slow}\n"
                    f"  • RSI: {self.settings.scalper_rsi_period} (Buy: {self.settings.scalper_rsi_buy} / Sell: {self.settings.scalper_rsi_sell})\n\n"
                    f"🚀 <b>Migliori Parametri Trovati:</b>\n"
                    f"  • EMA: <b>{best_cfg['ema_fast']}/{best_cfg['ema_slow']}</b>\n"
                    f"  • RSI: <b>{best_cfg['rsi_period']}</b> (Buy: {best_cfg['rsi_buy']:.1f} / Sell: {best_cfg['rsi_sell']:.1f})\n"
                    f"─────────────────\n"
                    f"📈 <b>Performance Teorica (Ultimi 1000m):</b>\n"
                    f"  • Net PnL: <b>{best_cfg['metrics']['net_pnl']:+.4f} EUR/USD</b>\n"
                    f"  • Esecuzioni: {best_cfg['metrics']['trades']}\n"
                    f"  • Win Rate: <b>{best_cfg['metrics']['win_rate']}%</b>\n\n"
                    f"Premi /apply_opt per salvare i parametri nel file .env e riavviare la flotta."
                )
                await self.send(msg)
            except Exception as e:
                await self.send(f"❌ <b>OPTIMIZER: Eccezione:</b> {e}")
                
        asyncio.create_task(run_opt_task())

    async def _cmd_apply_opt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
            
        cfg = getattr(self, "_last_optimized_cfg", None)
        if not cfg:
            await update.message.reply_html("❌ Nessuna configurazione ottimizzata in memoria. Esegui prima /optimize.")
            return
            
        try:
            symbol = self.settings.scalper_symbol
            exchange = self.settings.scalper_exchange
            optimizer = ParameterOptimizer(symbol, exchange)
            success = optimizer.apply_settings(cfg)
            if success:
                await update.message.reply_html(
                    "✅ <b>Parametri salvati con successo nel file .env!</b>\n"
                    "Riavvio la flotta di trading locale per rendere effettivi i nuovi valori..."
                )
                # Determine current systemd service name
                svc_name = "vulcan.service"
                if "scalper" in self.settings.active_strategies:
                    svc_name = "sentinel.service" if self.settings.scalper_symbol == "BTC/EUR" else "tempest.service"
                
                import subprocess
                subprocess.Popen(["sudo", "systemctl", "restart", svc_name])
            else:
                await update.message.reply_html("❌ Impossibile scrivere i parametri nel file .env.")
        except Exception as e:
            await update.message.reply_html(f"❌ Errore durante il salvataggio dei parametri: {e}")


# Mobile Trading Bot - denaro

Una piattaforma di trading intelligente ottimizzata per smartphone con una singola macchina.

## 🚀 Caratteristiche Principali

- **Semplice**: Una singola macchina che gestisce tutto il trading
- **Intelligente**: Algoritmi di trading avanzati con gestione del rischio
- **Mobile-Friendly**: Dashboard web ottimizzata per smartphone
- **Notifiche**: Alert Telegram per monitoraggio remoto
- **Database SQLite**: Archiviazione locale efficiente
- **Strategie Multiple**: Scalping, Grid Trading, RSI Mean Reversion

## 📱 Dashboard Mobile

La dashboard è ottimizzata per smartphone con:
- Layout responsive
- Grafici compatti
- Notifiche push
- Controllo rapido delle posizioni
- Stato del sistema in tempo reale

## 🏗️ Architettura Semplificata

```
denaro/
├── core/           # Motore di trading, gestione rischi, database
├── strategies/     # Strategie di trading implementate
├── services/       # Dashboard web, notifiche, API
├── config/         # Configurazioni e settings
├── main.py         # Main orchestrator
├── trade_db.py     # Database management
├── requirements.txt # Dipendenze Python
└── .env           # Variabili d'ambiente
```

## 🎯 Strategie Disponibili

### 1. Scalper Strategy
- **Tecnica**: EMA fast/slow + RSI + ATR
- **Capital**: 70% del capitale totale
- **Simbolo**: BTC/USDT
- **Timeframe**: 1m
- **Features**: Dynamic position sizing, trailing stop

### 2. Grid Trading
- **Tecnica**: Grid trading classico con livelli predefiniti
- **Capital**: 30% del capitale totale
- **Simbolo**: SOL/USDT
- **Levels**: 8 livelli
- **Spacing**: 0.45%
- **Features**: Adaptive stop-loss, riciclo automatico

### 3. Dynamic Grid (Opzionale)
- **Tecnica**: Grid che si adatta alla volatilità
- **Capital**: Configurabile
- **Features**: Spacing dinamico, sincronizzazione con order book

## ⚙️ Setup Rapido

1. **Installa dipendenze**:
```bash
pip install -r requirements.txt
```

2. **Configura .env**:
```bash
# Exchange API Keys
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_secret_key

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Trading Settings
TOTAL_CAPITAL_EUR=49.0
SCALPER_EXCHANGE=binance
GRID_EXCHANGE=binance
SCALPER_SYMBOL=BTC/USDT
GRID_SYMBOL=SOL/USDT

# Dashboard
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8000

# Features
DRY_RUN=true
TELEGRAM_POLLING=true
```

3. **Avvia il bot**:
```bash
python main.py
```

4. **Accedi alla dashboard**:
```
http://localhost:8000
```

## 📊 Mobile Dashboard Features

- **Stato Sistema**: Online/Offline, Dry Run/Live
- **Bilanci**: Saldo totale per exchange
- **P&L**: Daily P&L, totale, win rate
- **Posizioni**: Aperte, chiuse, in attesa
- **Strategie**: Stato attivo/pausato
- **Notifiche**: Alert Telegram
- **Controllo**: Start/Stop, pause/resume

## 🔔 Notifiche Telegram

Comandi disponibili:
- `/start` - Messaggio di benvenuto
- `/status` - Stato del sistema
- `/pnl` - P&L attuale
- `/pause` - Pausa strategie
- `/resume` - Riprendi strategie
- `/halt` - Ferma tutto
- `/grid_reset` - Reset grid

## 🛡️ Gestione Rischio

- **Max Daily Loss**: 5% del capitale
- **Max Drawdown**: 15% del capitale
- **Dynamic Position Sizing**: Adatta il rischio in base al drawdown
- **Trailing Stop**: Protegge i profitti
- **Stop Loss**: Automatico per ogni posizione

## 📱 Ottimizzazione Mobile

- **Touch-friendly**: Pulsanti grandi, facile tocco
- **Responsive**: Si adatta a schermi piccoli
- **Performance**: Grafici ottimizzati
- **Offline**: Cache locale per dati critici
- **Notifiche**: Push per eventi importanti

## 🚀 Avvio Automatico

Per eseguire il bot in background su Linux:
```bash
nohup python main.py > denaro.log 2>&1 &
```

## 📈 Monitoraggio

- **Dashboard Web**: Accesso da qualsiasi dispositivo
- **Telegram**: Notifiche in tempo reale
- **Log File**: Registro completo delle operazioni
- **Database**: Storico completo dei trade

## 🔧 Configurazione Avanzata

Modifica i parametri in `core/engine.py`:
- Cambiare simboli di trading
- Modificare capital allocation
- Aggiornare timeframe e indicatori
- Configurare risk parameters

## 🎯 Mobile Best Practices

1. **Battery Saving**: Ottimizzazione per lunghe sessioni
2. **Data Usage**: Compressione dati per riduzione traffico
3. **Offline Mode**: Funzionamento senza connessione
4. **Push Notifications**: Solo eventi importanti
5. **Quick Actions**: Tocchi rapidi per controllo immediato

---

**Nota**: Questo progetto è ottimizzato per una singola macchina con focus su mobile accessibility.
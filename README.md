# Denaro - Smart Trading for Mobile

Una piattaforma di trading intelligente ottimizzata per smartphone con una singola macchina.

## Caratteristiche Principali

- **Semplice**: Una singola macchina che gestisce tutto il trading
- **Intelligente**: Algoritmi di trading avanzati con gestione del rischio
- **Mobile-Friendly**: Dashboard web ottimizzata per smartphone
- **Notifiche**: Alert Telegram per monitoraggio remoto
- **Database SQLite**: Archiviazione locale efficiente
- **Strategie Multiple**: Scalping, Grid Trading, RSI Mean Reversion

## Architettura

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

## Strutture Chiave

### Core (`core/`)
- `engine.py`: Wrapper exchange, risk management, database
- `settings.py`: Configurazioni centralizzate

### Strategie (`strategies/`)
- `base.py`: Base class per tutte le strategie
- `scalper.py`: Strategia di scalping ad alta frequenza
- `grid.py`: Grid trading classico
- `dynamic_grid.py`: Grid trading dinamico
- `rsi_mean_rev.py`: RSI mean reversion strategy

### Servizi (`services/`)
- `dashboard.py`: Dashboard web FastAPI ottimizzata per mobile
- `notifications.py`: Servizio di notifiche Telegram

## Setup

1. Installa le dipendenze:
```bash
pip install -r requirements.txt
```

2. Configura `.env` con le tue API keys:
```
BINANCE_API_KEY=your_key
BINANCE_SECRET_KEY=your_secret
TELEGRAM_BOT_TOKEN=your_token
```

3. Esegui il bot:
```bash
python main.py
```

## Dashboard Mobile

La dashboard è ottimizzata per smartphone con:
- Layout responsive
- Grafici compatti
- Notifiche push
- Controllo rapido delle posizioni
- Stato del sistema in tempo reale

## Strategie Disponibili

1. **Scalper**: Trading ad alta frequenza per piccoli profitti rapidi
2. **Grid Trading**: Acquisto/vendita su livelli predefiniti
3. **Dynamic Grid**: Grid che si adatta alle condizioni di mercato
4. **RSI Mean Reversion**: Sfrutta le inversioni basate su RSI

## Gestione del Rischio

- Limiti di posizione per exchange
- Stop loss automatico
- Risk management centralizzato
- Monitoraggio delle perdite

## Notifiche

- Alert Telegram per trade eseguiti
- Notifiche di errore
- Report periodici
- Allerte rischio

## Mobile Features

- Dashboard touch-friendly
- Grafici ottimizzati per schermi piccoli
- Notifiche push
- Accesso remoto via web
- Controllo rapido start/stop
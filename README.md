# Samadhan Delta Operator + Momentum Scanner

Run:
```bash
pip install -r requirements.txt
streamlit run app.py
```

Python 3.11+ recommended (3.12 works). This dashboard uses public Delta Exchange India market data and does not need an API key. It does not place orders.

Important: Early Watch is only a proxy for possible activity, not proof that an operator entered. This first version uses OI availability but does not yet calculate OI change across candles. Backtest and paper-trade before live decisions.

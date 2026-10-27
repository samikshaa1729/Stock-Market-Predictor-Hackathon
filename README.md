# Stock Predictor - Random Forest + K-Means market regimes

Flask + MySQL (Aiven) + Render, with yfinance and scikit-learn. Data is downloaded live, always up to today
(last 5 years). Tables are created automatically on startup.

## Features
| Feature | How it is done |
|---|---|
| Live data up to today | `fetch_stock_data()` downloads the last 5 years ending today |
| Next-day close, high, low | Random Forest regressor predicts the % change, converted to prices |
| % increase / decrease | Predicted next close vs today's close |
| **Market regimes** | K-Means groups every trading day by 5-day return and 10-day volatility into Calm / Trending / Volatile |
| Regime map + statistics | Scatter plot of all days, regime share, win rate, average next-day move, current streak |
| Grouping quality | Silhouette score for 2 to 6 groups (3 is used) |
| Regime-aware Buy / Hold / Sell | Needs a predicted move of 0.25% (0.5% in volatile phases); confidence High / Medium / Low from the regime |
| Stock grouping | "Compare stocks" tab groups 3-12 tickers into Stable / Balanced / Aggressive |
| Candlestick chart | Last 90 days tinted by regime, dashed forecast candle, hover tooltip |
| Error metrics | MAE and R2 on a time-ordered 20% test set |
| Short summary + 52-week range | Generated sentence in the selected language |
| Multilingual | English, Hindi, Marathi (text in `static/i18n.js`) |

Features are ratios only (returns, moving-average gaps, volatility, high-low range, volume change, RSI),
so the model works at any price level. Full write-up: see the project documentation.

## Project files
- app.py                Flask backend + all ML code
- templates/index.html  Page
- static/app.js         UI logic, candlestick chart, rendering
- static/i18n.js        English / Hindi / Marathi text
- static/style.css      Styling
- requirements.txt, Procfile, schema.sql, .env.example, .gitignore

## Run locally
1. `pip install -r requirements.txt`
2. Make sure MySQL is running and a database exists: `CREATE DATABASE STOCK_MARKET;`
3. Set variables (Windows cmd; use `export` on Mac/Linux):
   ```
   set DB_PASSWORD=your_mysql_password
   set SECRET_KEY=any-long-random-string
   ```
4. `python app.py` -> open http://127.0.0.1:5000

## Deploy: Aiven (MySQL) + Render (web app)

### 1. Create the database on Aiven
1. Sign up at aiven.io (no credit card needed for the free plan).
2. **Create service -> MySQL -> Free plan** -> pick a region -> **Create free service**.
3. Wait until the status is **Running**, then open the service **Overview** and copy the
   *Connection information*: **Host, Port, User (avnadmin), Password, Database name (defaultdb)**.
4. Leave *Allowed IP addresses* open (0.0.0.0/0). Render's free servers have changing IPs.

Aiven requires SSL; this project handles that when `DB_SSL=true` (already set in `render.yaml`).
You do **not** need to create tables: the app creates them on first start.

### 2. Push the code to GitHub
```
git init
git add .
git commit -m "Stock predictor v2"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 3. Deploy on Render
1. render.com -> log in with GitHub -> **New -> Blueprint** -> choose the repo.
   Render reads `render.yaml` and proposes a free web service named `stock-predictor`.
2. Fill the five values it asks for (from Aiven):
   `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.
   (`SECRET_KEY` is generated automatically.)
3. Click **Apply**. The first build takes a few minutes.
4. Open `https://stock-predictor-xxxx.onrender.com`, sign up, log in, predict.
   `/health` should return `{"status":"ok"}`.

Prefer manual setup instead of the Blueprint? New -> Web Service -> your repo, then:
Build command `pip install -r requirements.txt`, Start command
`gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 180`,
and add the variables from `.env.example` under **Environment**.

### Free-tier things to know
- Render free sleeps after 15 minutes idle; the first visit afterwards takes about a minute.
  **Open the site a few minutes before your demo.**
- The free instance has 512 MB RAM and 0.1 CPU, so a prediction can take 20-60 seconds.
  `RF_TREES=80` keeps it lighter (raise it if you upgrade the plan).
- Aiven free MySQL allows about 20 connections; this app opens one per request and closes it,
  which is fine for a college demo.
- Yahoo Finance sometimes blocks cloud servers. If you see "No stock data found" on the live
  site, wait a few minutes and retry.

### Troubleshooting
| Symptom | Fix |
|---|---|
| Login says "Database error" | Check the 5 DB variables on Render; confirm Aiven shows **Running** |
| `SSL connection error` / `Access denied` | `DB_SSL` must be `true`; use user `avnadmin` and the password from Aiven (not your Aiven login) |
| Build fails | Check the Render logs; make sure `PYTHON_VERSION` is `3.11.9` |
| Timeout on first prediction | Retry once (cold start); lower `RF_TREES` |

## Tips
- Indian stocks: use `.NS` (NSE) or `.BO` (BSE), e.g. `RELIANCE.NS`. Prices show in rupees.
- First prediction takes a few seconds (download + training); the same ticker within
  10 minutes reuses the downloaded data.
- "No stock data found": Yahoo may rate-limit cloud IPs, retry later.

*Educational project. Not investment advice.*

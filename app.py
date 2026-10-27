# -*- coding: utf-8 -*-
"""
Stock Predictor - Flask web app
  * Random Forest regressor -> next-day close / high / low (+ MAE, R2)
  * K-Means clustering      -> market regimes (calm / trending / volatile),
                               regime-based signal, and grouping of stocks by behaviour
  * Buy / Hold / Sell signal, % change, candlestick data, 52-week range
  * Data is always downloaded up to TODAY (no fixed end date)
"""
import base64
import datetime as dt
import io
import os
import re
import time
from contextlib import contextmanager
from functools import wraps

import bcrypt
import mysql.connector
import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, render_template, request, session
from matplotlib.figure import Figure
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score, silhouette_score
from sklearn.preprocessing import StandardScaler

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
)


# ---------------- CONFIG ----------------
def env(*names, default=""):
    """First environment variable that is set (DB_* first, then Railway's MYSQL*)."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


DB_CONFIG = dict(
    host=env("DB_HOST", "MYSQLHOST", default="localhost"),
    port=int(env("DB_PORT", "MYSQLPORT", default="3306")),
    user=env("DB_USER", "MYSQLUSER", default="root"),
    password=env("DB_PASSWORD", "MYSQLPASSWORD", default="sama@sql"),
    database=env("DB_NAME", "MYSQLDATABASE", default="STOCK_MARKET"),
    connection_timeout=15,
)
# Aiven (and most cloud MySQL) only accepts SSL connections.
#   DB_SSL=true     -> encrypted connection
#   DB_SSL_CA=/path -> (optional) also verify the server with Aiven's CA certificate
if env("DB_SSL", default="false").lower() in ("1", "true", "yes"):
    DB_CONFIG["ssl_disabled"] = False
    if env("DB_SSL_CA"):
        DB_CONFIG["ssl_ca"] = env("DB_SSL_CA")
        DB_CONFIG["ssl_verify_cert"] = True

HISTORY_YEARS = 5            # data window: today minus 5 years ... today
CACHE_SECONDS = 600          # re-use a download for 10 minutes (avoids Yahoo rate limits)
CANDLES_SHOWN = 90           # candles drawn on the chart
REGIME_NAMES = ("calm", "trending", "volatile")
N_REGIMES = 3                # calm / trending / volatile
SIGNAL_THRESHOLD = 0.25      # % move needed before we say Buy / Sell (x2 in volatile phases)
RF_TREES = int(os.environ.get("RF_TREES", "150"))   # lower on small free servers


# ---------------- DATABASE ----------------
@contextmanager
def db():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        yield conn, cur
    finally:
        cur.close()
        conn.close()


def ensure_column(cur, table, column, ddl):
    """Add a column to an existing (old-version) table if it is missing."""
    cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE "
                "TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                (table, column))
    if cur.fetchone()[0] == 0:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    """Create tables if missing and upgrade the v1 table (safe to run every startup)."""
    with db() as (conn, cur):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(100) NOT NULL
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock_searches (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                ticker VARCHAR(20) NOT NULL,
                predicted_price DOUBLE NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )""")
        ensure_column(cur, "stock_searches", "signal_type", "VARCHAR(10) NULL")
        ensure_column(cur, "stock_searches", "pct_change", "DOUBLE NULL")
        ensure_column(cur, "stock_searches", "created_at",
                      "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.commit()


try:
    init_db()
except Exception as e:  # app still starts; errors show up on login/signup
    print("WARNING: could not initialise database:", e)


# ---------------- DATA ----------------
_cache = {}


def fetch_stock_data(ticker):
    """Daily OHLCV from (today - 5 years) up to today. Cached for a few minutes."""
    hit = _cache.get(ticker)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1].copy()
    today = dt.date.today()
    start = (today - dt.timedelta(days=HISTORY_YEARS * 365)).isoformat()
    end = (today + dt.timedelta(days=1)).isoformat()      # end date is exclusive
    data = yf.download(ticker, start=start, end=end,
                       auto_adjust=True, progress=False)
    if data.empty:
        raise ValueError("No stock data found. Check the ticker symbol.")
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data = data.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
    data = data.dropna().reset_index(drop=True)
    _cache[ticker] = (time.time(), data)
    return data.copy()


# ---------------- FEATURES ----------------
FEATURES = ["ret_1", "ret_2", "ret_3", "ret_5", "ma5_gap", "ma10_gap", "ma20_gap",
            "vol_5", "vol_10", "hl_range", "vol_chg", "rsi_14"]
TARGETS = ["t_close", "t_high", "t_low"]


def add_features(data):
    """Every feature is a ratio / % so it works for any price level (important
    because today's prices are higher than anything in the training period)."""
    d = data.copy()
    c = d["Close"]
    d["ret_1"] = c.pct_change()
    for n in (2, 3, 5):
        d[f"ret_{n}"] = c.pct_change(n)
    for n in (5, 10, 20):
        d[f"ma{n}_gap"] = c / c.rolling(n).mean() - 1
    d["vol_5"] = d["ret_1"].rolling(5).std()
    d["vol_10"] = d["ret_1"].rolling(10).std()
    d["hl_range"] = (d["High"] - d["Low"]) / c
    d["vol_chg"] = (d["Volume"].pct_change()
                    .replace([np.inf, -np.inf], np.nan).fillna(0).clip(-1, 1))
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi_14"] = (100 - 100 / (1 + gain / loss)).replace([np.inf, -np.inf], 100).fillna(50)
    # targets = what happens on the NEXT trading day, relative to today's close
    d["t_close"] = c.shift(-1) / c - 1
    d["t_high"] = d["High"].shift(-1) / c - 1
    d["t_low"] = d["Low"].shift(-1) / c - 1
    return d


# ---------------- MODEL 1: RANDOM FOREST (price, high, low) ----------------
def run_regression(train, latest_X):
    split = int(len(train) * 0.8)                      # time-ordered 80/20 split
    X, Y = train[FEATURES].values, train[TARGETS].values
    rf = RandomForestRegressor(n_estimators=RF_TREES, min_samples_leaf=3,
                               random_state=42, n_jobs=-1)
    rf.fit(X[:split], Y[:split])
    pred = rf.predict(X[split:])
    base = train["Close"].values[split:]
    actual_price = base * (1 + Y[split:, 0])
    pred_price = base * (1 + pred[:, 0])
    metrics = dict(mae=mean_absolute_error(actual_price, pred_price),
                   r2=r2_score(actual_price, pred_price))
    final = RandomForestRegressor(n_estimators=RF_TREES, min_samples_leaf=3,
                                  random_state=42, n_jobs=-1).fit(X, Y)  # refit on all data
    nxt = final.predict(latest_X)[0]
    return nxt, metrics, train["Date"].values[split:], actual_price, pred_price


# ---------------- MODEL 2: K-MEANS (market regimes) ----------------
def market_regimes(d):
    """Cluster every trading day by 5-day return (trend) and 10-day volatility.
    The 3 clusters are named Calm / Trending / Volatile from their centres."""
    cols = d[["ret_5", "vol_10"]]
    ok = cols.dropna().index
    X = StandardScaler().fit_transform(cols.loc[ok].values)
    quality, labels = {}, None
    for k in range(2, 7):                              # silhouette score for k = 2..6
        lab_k = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
        quality[str(k)] = round(float(silhouette_score(
            X, lab_k, sample_size=min(800, len(X)), random_state=42)), 3)
        if k == N_REGIMES:
            labels = lab_k
    lab = pd.Series(labels, index=ok)
    vol = d.loc[ok, "vol_10"].groupby(lab).mean()
    trend = d.loc[ok, "ret_5"].abs().groupby(lab).mean()
    volatile = vol.idxmax()                            # highest volatility
    trending = trend.drop(volatile).idxmax()           # biggest moves among the rest
    calm = [c for c in vol.index if c not in (volatile, trending)][0]
    named = lab.map({volatile: "volatile", trending: "trending", calm: "calm"}).reindex(d.index)
    total = named.notna().sum()
    summary = {}
    for n in REGIME_NAMES:
        m = named == n
        nxt = d.loc[m, "t_close"].dropna()             # what happened the day after
        summary[n] = dict(
            share=round(m.sum() / total * 100, 1),
            avg_vol=round(float(d.loc[m, "vol_10"].mean() * 100), 2),
            avg_ret5=round(float(d.loc[m, "ret_5"].mean() * 100), 2),
            win_rate=round(float((nxt > 0).mean() * 100), 1) if len(nxt) else 0.0,
            avg_next=round(float(nxt.mean() * 100), 2) if len(nxt) else 0.0)
    valid = named.dropna()
    current = valid.iloc[-1]
    streak = 0                                         # days spent in the current regime
    for v in valid[::-1]:
        if v != current:
            break
        streak += 1
    points = [[round(float(v * 100), 2), round(float(r * 100), 2), REGIME_NAMES.index(named[i])]
              for i, v, r in zip(ok, d.loc[ok, "vol_10"], d.loc[ok, "ret_5"])]
    return named, current, summary, dict(quality=quality, streak=streak, points=points)


def cluster_tickers(rows):
    """Group several stocks by behaviour: return, volatility, worst drawdown."""
    df = pd.DataFrame(rows)
    X = StandardScaler().fit_transform(df[["ann_return", "ann_vol", "max_dd"]].values)
    k = 2 if len(df) < 5 else 3
    df["cluster"] = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
    order = df.groupby("cluster")["ann_vol"].mean().sort_values().index.tolist()
    names = ["stable", "aggressive"] if k == 2 else ["stable", "balanced", "aggressive"]
    df["group"] = df["cluster"].map({c: names[i] for i, c in enumerate(order)})
    rank = {n: i for i, n in enumerate(names)}
    return df.sort_values(by="group", key=lambda s: s.map(rank)).drop(columns="cluster") \
             .to_dict("records")


# ---------------- SIGNAL (Random Forest move + K-Means regime) ----------------
REGIME_STRICTNESS = {"calm": 1, "trending": 1, "volatile": 2}     # volatile = need a bigger move
REGIME_CONFIDENCE = {"calm": "high", "trending": "medium", "volatile": "low"}


def make_signal(pct, regime):
    thr = SIGNAL_THRESHOLD * REGIME_STRICTNESS[regime]
    if pct >= thr:
        sig = "BUY"
    elif pct <= -thr:
        sig = "SELL"
    else:
        sig = "HOLD"
    return sig, REGIME_CONFIDENCE[regime]


# ---------------- PLOT (model fit on the test period) ----------------
def make_plot(dates, actual, predicted, ticker):
    fig = Figure(figsize=(10, 4), facecolor="#16233a")
    ax = fig.subplots()
    ax.set_facecolor("#16233a")
    ax.plot(dates, actual, color="#8fa2c0", linewidth=1.4, label="Actual close")
    ax.plot(dates, predicted, color="#f5b942", linewidth=1.1, label="Random Forest prediction")
    ax.set_title(f"{ticker}: model vs actual (test period)", color="#e8eef8")
    ax.tick_params(colors="#8fa2c0")
    for s in ax.spines.values():
        s.set_color("#27395a")
    ax.legend(facecolor="#16233a", edgecolor="#27395a", labelcolor="#e8eef8")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=fig.get_facecolor())
    return base64.b64encode(buf.getvalue()).decode()


def currency_of(ticker):
    return "\u20b9" if ticker.endswith((".NS", ".BO")) else "$"


# ---------------- FULL ANALYSIS ----------------
def analyze(ticker):
    data = fetch_stock_data(ticker)
    if len(data) < 120:
        raise ValueError("Not enough historical stock data.")
    d = add_features(data)
    latest_X = d[FEATURES].dropna().iloc[[-1]].values
    train = d.dropna(subset=FEATURES + TARGETS).reset_index(drop=True)

    nxt, reg, test_dates, actual, predicted = run_regression(train, latest_X)
    named, regime, regimes, info = market_regimes(d)

    last = d.iloc[-1]
    last_close = float(last["Close"])
    price = last_close * (1 + nxt[0])
    high = max(last_close * (1 + nxt[1]), price)
    low = min(last_close * (1 + nxt[2]), price)
    pct = float(nxt[0] * 100)
    signal, confidence = make_signal(pct, regime)

    wk = d.tail(252)
    hi52, lo52 = float(wk["High"].max()), float(wk["Low"].min())
    pos = (last_close - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50.0

    tail = d.tail(CANDLES_SHOWN)
    candles = [[row.Date.strftime("%d %b %y"), round(float(row.Open), 2), round(float(row.High), 2),
                round(float(row.Low), 2), round(float(row.Close), 2), named.get(i)]
               for i, row in tail.iterrows()]
    next_day = (last["Date"] + pd.offsets.BDay(1)).strftime("%d %b %Y")

    return dict(
        ticker=ticker, currency=currency_of(ticker),
        last_date=last["Date"].strftime("%d %b %Y"), next_date=next_day,
        last_close=round(last_close, 2), price=round(price, 2), pct=round(pct, 2),
        high=round(high, 2), low=round(low, 2),
        signal=signal, confidence=confidence,
        mae=round(reg["mae"], 2), r2=round(reg["r2"], 3),
        regime=regime, regimes=regimes, streak=info["streak"],
        quality=info["quality"], points=info["points"],
        week52=dict(high=round(hi52, 2), low=round(lo52, 2), position=round(pos, 1)),
        candles=candles, rows=len(d), data_from=d["Date"].iloc[0].strftime("%d %b %Y"),
        image=make_plot(test_dates, actual, predicted, ticker),
    )


# ---------------- AUTH HELPERS ----------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return jsonify(error="Please log in first."), 401
        return fn(*a, **kw)
    return wrapper


def credentials():
    body = request.get_json(silent=True) or {}
    return (body.get("username") or "").strip(), body.get("password") or ""


TICKER_RE = re.compile(r"[A-Z0-9.\-^=]{1,15}")


# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html", username=session.get("username"))


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/api/signup")
def signup():
    username, password = credentials()
    if not username or not password:
        return jsonify(error="Enter username and password."), 400
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        with db() as (conn, cur):
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                        (username, pw_hash))
            conn.commit()
    except mysql.connector.IntegrityError:
        return jsonify(error="Username already exists."), 409
    except Exception as e:
        return jsonify(error=f"Database error: {e}"), 500
    return jsonify(message="Signup successful! You can now log in.")


@app.post("/api/login")
def login():
    username, password = credentials()
    if not username or not password:
        return jsonify(error="Enter username and password."), 400
    try:
        with db() as (conn, cur):
            cur.execute("SELECT id, password_hash FROM users WHERE username=%s", (username,))
            user = cur.fetchone()
    except Exception as e:
        return jsonify(error=f"Database error: {e}"), 500
    if not user:
        return jsonify(error="User not found."), 404
    uid, stored = user
    stored = stored if isinstance(stored, bytes) else stored.encode()
    if not bcrypt.checkpw(password.encode(), stored):
        return jsonify(error="Wrong password."), 401
    session["user_id"], session["username"] = uid, username
    return jsonify(username=username)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/predict")
@login_required
def predict():
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "AAPL").strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        return jsonify(error="Invalid ticker symbol."), 400
    try:
        result = analyze(ticker)
    except Exception as e:
        return jsonify(error=str(e)), 400
    try:                                   # saving history must never break a prediction
        with db() as (conn, cur):
            cur.execute("INSERT INTO stock_searches (user_id, ticker, predicted_price, "
                        "signal_type, pct_change) VALUES (%s, %s, %s, %s, %s)",
                        (session["user_id"], ticker, result["price"],
                         result["signal"], result["pct"]))
            conn.commit()
    except Exception as e:
        print("WARNING: could not save history:", e)
    return jsonify(result)


@app.post("/api/cluster")
@login_required
def cluster():
    body = request.get_json(silent=True) or {}
    raw = body.get("tickers") or ""
    tickers = list(dict.fromkeys(t.strip().upper() for t in raw.split(",") if t.strip()))
    if not 3 <= len(tickers) <= 12 or not all(TICKER_RE.fullmatch(t) for t in tickers):
        return jsonify(error="Enter 3 to 12 valid tickers separated by commas."), 400
    rows, skipped = [], []
    for t in tickers:
        try:
            df = fetch_stock_data(t)
            r = df["Close"].pct_change().dropna()
            if len(r) < 60:
                raise ValueError("short history")
            dd = (df["Close"] / df["Close"].cummax() - 1).min()
            rows.append(dict(ticker=t, ann_return=round(float(r.mean() * 252 * 100), 1),
                             ann_vol=round(float(r.std() * np.sqrt(252) * 100), 1),
                             max_dd=round(float(dd * 100), 1)))
        except Exception:
            skipped.append(t)
    if len(rows) < 3:
        return jsonify(error="Could not load enough tickers (need at least 3)."), 400
    return jsonify(rows=cluster_tickers(rows), skipped=skipped)


@app.get("/api/history")
@login_required
def history():
    with db() as (conn, cur):
        cur.execute("SELECT ticker, predicted_price, signal_type, pct_change, created_at "
                    "FROM stock_searches WHERE user_id=%s ORDER BY id DESC LIMIT 50",
                    (session["user_id"],))
        rows = cur.fetchall()
    return jsonify(history=[dict(ticker=t, price=round(float(p), 2), signal=s,
                                 pct=None if c is None else round(float(c), 2),
                                 when=str(w)[:16] if w else "")
                            for t, p, s, c, w in rows])


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1",
            port=int(os.environ.get("PORT", "5000")))

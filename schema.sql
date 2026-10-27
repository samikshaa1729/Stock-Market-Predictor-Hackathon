-- Optional: the app creates / upgrades these tables automatically on startup.
-- Run this manually only if you prefer to set the database up yourself.
CREATE DATABASE IF NOT EXISTS STOCK_MARKET;
USE STOCK_MARKET;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_searches (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    predicted_price DOUBLE NOT NULL,
    signal_type VARCHAR(10) NULL,                      -- v2: BUY / HOLD / SELL
    pct_change DOUBLE NULL,                            -- v2: predicted % change
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,    -- v2: when it was predicted
    FOREIGN KEY (user_id) REFERENCES users(id)
);

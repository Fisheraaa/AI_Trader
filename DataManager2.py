import sqlite3
import pandas as pd
import requests
import time
import datetime
import os
import json
import random
import akshare as ak
from collections import deque

DB_PATH = "data/db/trading.db"
LIST_FILE = "etf_list.txt"
FAIL_REPORT_FILE = "data/daily_fetch_failures.json"
DAILY_STATE_FILE = "data/daily_sync_state.json"

# 批处理参数（可用 .env 覆盖）
BATCH_SIZE = int(os.getenv("DM_BATCH_SIZE", "20"))
BATCH_SLEEP_SEC = float(os.getenv("DM_BATCH_SLEEP_SEC", "25"))
MAX_ROUNDS = int(os.getenv("DM_MAX_ROUNDS", "8"))  # 最多轮数，防止死循环
HTTP_TIMEOUT = int(os.getenv("DM_HTTP_TIMEOUT", "15"))

INDEX_MAP = {
    "sh000001": "000001",
    "sz399001": "399001",
    "sz399006": "399006",
    "sh000300": "000300",
    "sh000905": "000905",
}


def normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return s
    if s.startswith("sh") or s.startswith("sz"):
        return s
    if len(s) == 6 and s.isdigit():
        if s.startswith("6"):
            return "sh" + s
        if s.startswith(("0", "1", "3")):
            return "sz" + s
        if s.startswith("5"):
            return "sh" + s
        return "sz" + s
    return s


def load_etf_pool():
    if not os.path.exists(LIST_FILE):
        default_list = ["sh510300", "sz159915"]
        with open(LIST_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(default_list))
        return default_list

    out, seen = [], set()
    with open(LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            s = normalize_symbol(line)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


ETF_POOL = load_etf_pool()


class DataManagerV5:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        os.makedirs("data", exist_ok=True)
        self._ensure_market_daily_table()

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": "https://finance.sina.com.cn/",
        })

        self.daily_state = self._load_daily_state()
        self.failures = {
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market_daily_failed": [],
            "hourly_failed": [],
            "daily_failed": [],
            "notes": []
        }

    # ---------- state ----------
    def _today_str(self):
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _load_daily_state(self):
        if not os.path.exists(DAILY_STATE_FILE):
            return {}
        try:
            with open(DAILY_STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_daily_state(self):
        with open(DAILY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.daily_state, f, ensure_ascii=False, indent=2)

    def is_daily_synced_today(self, symbol: str) -> bool:
        return self.daily_state.get(symbol) == self._today_str()

    def mark_daily_synced_today(self, symbol: str):
        self.daily_state[symbol] = self._today_str()

    def _save_fail_report(self):
        with open(FAIL_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(self.failures, f, ensure_ascii=False, indent=2)

    # ---------- DB ----------
    def _ensure_market_daily_table(self):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_daily (
                date TEXT,
                index_code TEXT,
                close REAL,
                volume REAL,
                ma20 REAL,
                pct_chg REAL,
                hv20 REAL,
                ad_line REAL
            )
            """
        )
        conn.commit()
        conn.close()

    def get_last_date(self, table_name):
        try:
            conn = sqlite3.connect(DB_PATH)
            res = pd.read_sql(f"SELECT date FROM {table_name} ORDER BY date DESC LIMIT 1", conn)
            conn.close()
            return res.iloc[0]["date"]
        except Exception:
            return None

    # ---------- HTTP ----------
    def _http_get_with_retry(self, url: str, max_retry=3, base_sleep=0.8):
        last_err = None
        for i in range(max_retry):
            try:
                r = self.session.get(url, timeout=HTTP_TIMEOUT)
                if r.status_code == 200:
                    return r
                if r.status_code in (429, 456, 403):
                    time.sleep(base_sleep * (2 ** i) + random.uniform(0.1, 0.5))
                    continue
                time.sleep(base_sleep * (i + 1))
            except Exception as e:
                last_err = e
                time.sleep(base_sleep * (2 ** i) + random.uniform(0.1, 0.5))
        if last_err:
            raise last_err
        return None

    # ---------- fetch ----------
    def fetch_hourly_data(self, symbol, count=480):
        url = (
            "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=60&datalen={count}"
        )
        try:
            resp = self._http_get_with_retry(url)
            if resp is None:
                return None
            txt = resp.text.strip().lower()
            if txt.startswith("<!doctype html") or "<html" in txt[:200]:
                return None
            data = resp.json()
            df = pd.DataFrame(data)
            if df.empty or "day" not in df.columns:
                return None
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["day"])
            df = df[["date", "open", "close", "high", "low", "volume"]].dropna()
            return df.sort_values("date")
        except Exception:
            return None

    def _fetch_sina_daily(self, symbol: str, count=520):
        url = (
            "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&datalen={count}"
        )
        try:
            resp = self._http_get_with_retry(url)
            if resp is None:
                return None
            txt = resp.text.strip().lower()
            if not txt or txt.startswith("<!doctype html") or "<html" in txt[:200]:
                return None
            data = resp.json()
            df = pd.DataFrame(data)
            if df.empty or "day" not in df.columns:
                return None
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["day"])
            df = df[["date", "open", "close", "high", "low", "volume"]].dropna()
            return df.sort_values("date").tail(count)
        except Exception:
            return None

    def fetch_daily_data(self, symbol, count=520):
        code = symbol.replace("sh", "").replace("sz", "")
        try:
            df = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date="20000101",
                end_date=datetime.datetime.now().strftime("%Y%m%d"),
                adjust=""
            )
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume"
                })
                cols = ["date", "open", "close", "high", "low", "volume"]
                if all(c in df.columns for c in cols):
                    df = df[cols].copy()
                    df["date"] = pd.to_datetime(df["date"])
                    for c in ["open", "high", "low", "close", "volume"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    df = df.dropna().sort_values("date").tail(count)
                    if not df.empty:
                        return df
        except Exception:
            pass
        return self._fetch_sina_daily(symbol, count)

    def daily_from_hourly_fallback(self, symbol: str, count_days=520):
        table = f"tech_{symbol}"
        try:
            conn = sqlite3.connect(DB_PATH)
            h = pd.read_sql(
                f"SELECT date, open, high, low, close, volume FROM {table} ORDER BY date DESC LIMIT 30000",
                conn
            )
            conn.close()
            if h.empty:
                return None
            h["date"] = pd.to_datetime(h["date"])
            h = h.sort_values("date").set_index("date")
            d = pd.DataFrame()
            d["open"] = h["open"].resample("D").first()
            d["high"] = h["high"].resample("D").max()
            d["low"] = h["low"].resample("D").min()
            d["close"] = h["close"].resample("D").last()
            d["volume"] = h["volume"].resample("D").sum()
            d = d.dropna().reset_index()
            return d[["date", "open", "close", "high", "low", "volume"]].tail(count_days)
        except Exception:
            return None

    # ---------- indicator ----------
    @staticmethod
    def _safe_div(a, b):
        return a / b.replace(0, pd.NA)

    def calculate_full_indicators(self, df):
        df = df.sort_values("date").reset_index(drop=True)
        df["ma20"] = df["close"].rolling(20).mean()
        df["ma60"] = df["close"].rolling(60).mean()
        df["ma480"] = df["close"].rolling(480).mean()
        std20 = df["close"].rolling(20).std()
        df["boll_up"] = df["ma20"] + 2 * std20
        df["boll_low"] = df["ma20"] - 2 * std20

        exp1 = df["close"].ewm(span=12, adjust=False).mean()
        exp2 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd_dif"] = exp1 - exp2
        df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2

        low_9 = df["low"].rolling(9).min()
        high_9 = df["high"].rolling(9).max()
        rsv = self._safe_div(df["close"] - low_9, high_9 - low_9) * 100
        df["kdj_k"] = rsv.ewm(com=2).mean()
        df["kdj_d"] = df["kdj_k"].ewm(com=2).mean()
        df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = self._safe_div(gain, loss)
        df["rsi"] = 100 - (100 / (1 + rs))

        df["vol_ratio"] = self._safe_div(df["volume"], df["volume"].rolling(5).mean())

        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()

        direction = (df["close"].diff() > 0).astype(int) - (df["close"].diff() < 0).astype(int)
        df["obv"] = (direction * df["volume"]).fillna(0).cumsum()
        df["obv_ma20"] = df["obv"].rolling(20).mean()

        df["bias20"] = self._safe_div(df["close"] - df["ma20"], df["ma20"]) * 100
        df["bias60"] = self._safe_div(df["close"] - df["ma60"], df["ma60"]) * 100
        df["bias480"] = self._safe_div(df["close"] - df["ma480"], df["ma480"]) * 100
        return df

    def make_weekly_from_daily(self, df_daily):
        d = df_daily.copy().sort_values("date").set_index("date")
        w = pd.DataFrame()
        w["open"] = d["open"].resample("W-FRI").first()
        w["high"] = d["high"].resample("W-FRI").max()
        w["low"] = d["low"].resample("W-FRI").min()
        w["close"] = d["close"].resample("W-FRI").last()
        w["volume"] = d["volume"].resample("W-FRI").sum()
        w = w.dropna().reset_index()
        return self.calculate_full_indicators(w)

    # ---------- sync single ----------
    def sync_hourly(self, symbol):
        table = f"tech_{symbol}"
        last_date_str = self.get_last_date(table)

        if last_date_str is None:
            df = self.fetch_hourly_data(symbol, count=480)
        else:
            last_date = pd.to_datetime(last_date_str)
            hours_diff = int((datetime.datetime.now() - last_date).total_seconds() / 3600)
            if hours_diff < 1:
                return True
            fetch_count = min(hours_diff + 20, 480)
            df_new = self.fetch_hourly_data(symbol, count=fetch_count)
            if df_new is None:
                return False
            conn = sqlite3.connect(DB_PATH)
            try:
                df_old = pd.read_sql(f"SELECT * FROM {table} ORDER BY date DESC LIMIT 480", conn)
                df_old["date"] = pd.to_datetime(df_old["date"])
            except Exception:
                df_old = pd.DataFrame()
            conn.close()
            df = df_new if df_old.empty else pd.concat([df_old, df_new]).drop_duplicates(subset=["date"]).sort_values("date")

        if df is None or df.empty:
            return False

        df = self.calculate_full_indicators(df)
        conn = sqlite3.connect(DB_PATH)
        df.to_sql(table, conn, if_exists="replace", index=False)
        conn.close()
        return True

    def sync_daily_weekly(self, symbol):
        d = self.fetch_daily_data(symbol, count=520)
        source = "ak_or_sina"
        if d is None or d.empty:
            d = self.daily_from_hourly_fallback(symbol, 520)
            source = "hourly_fallback"
        if d is None or d.empty:
            return False

        d = self.calculate_full_indicators(d)
        w = self.make_weekly_from_daily(d)

        conn = sqlite3.connect(DB_PATH)
        d.to_sql(f"tech_daily_{symbol}", conn, if_exists="replace", index=False)
        w.to_sql(f"tech_weekly_{symbol}", conn, if_exists="replace", index=False)
        conn.close()
        return True

    # ---------- sync market ----------
    def sync_market_daily(self):
        all_rows = []
        for idx, ak_code in INDEX_MAP.items():
            df_ok = None
            try:
                df = ak.index_zh_a_hist(
                    symbol=ak_code,
                    period="daily",
                    start_date="20000101",
                    end_date=datetime.datetime.now().strftime("%Y%m%d")
                )
                if df is not None and not df.empty:
                    df = df.rename(columns={"日期": "date", "收盘": "close", "成交量": "volume", "涨跌幅": "pct_chg"})
                    need = ["date", "close", "volume", "pct_chg"]
                    if all(c in df.columns for c in need):
                        df_ok = df[need].copy()
                        df_ok["date"] = pd.to_datetime(df_ok["date"])
                        for c in ["close", "volume", "pct_chg"]:
                            df_ok[c] = pd.to_numeric(df_ok[c], errors="coerce")
                        df_ok = df_ok.dropna().sort_values("date")
            except Exception:
                pass

            if df_ok is None or df_ok.empty:
                fb = self._fetch_sina_daily(idx, 520)
                if fb is not None and not fb.empty:
                    fb["pct_chg"] = fb["close"].pct_change() * 100
                    df_ok = fb[["date", "close", "volume", "pct_chg"]].copy()

            if df_ok is None or df_ok.empty:
                self.failures["market_daily_failed"].append(idx)
                continue

            df_ok["ma20"] = df_ok["close"].rolling(20).mean()
            ret = df_ok["close"].pct_change()
            df_ok["hv20"] = ret.rolling(20).std() * (252 ** 0.5) * 100
            df_ok["ad_line"] = pd.NA
            df_ok["index_code"] = idx
            all_rows.append(df_ok[["date", "index_code", "close", "volume", "ma20", "pct_chg", "hv20", "ad_line"]])

        if not all_rows:
            return False
        out = pd.concat(all_rows).sort_values(["date", "index_code"])
        conn = sqlite3.connect(DB_PATH)
        out.to_sql("market_daily", conn, if_exists="replace", index=False)
        conn.close()
        return True

    # ---------- batch engine ----------
    def _run_batch_rounds(self, symbols, task_func, fail_bucket_name, title):
        """
        symbols: list[str]
        task_func: func(symbol)->bool
        """
        pending = deque(symbols)
        succeeded = set()
        round_no = 0

        while pending and round_no < MAX_ROUNDS:
            round_no += 1
            current = list(pending)
            pending.clear()

            print(f"🔁 {title} Round {round_no}/{MAX_ROUNDS}, pending={len(current)}")

            # 分批
            for i in range(0, len(current), BATCH_SIZE):
                batch = current[i:i + BATCH_SIZE]
                print(f"📦 {title} batch {i//BATCH_SIZE + 1}, size={len(batch)}")
                batch_fail = 0

                for sym in batch:
                    t0 = time.time()
                    ok = False
                    try:
                        ok = task_func(sym)
                    except Exception:
                        ok = False

                    cost = round(time.time() - t0, 2)
                    if ok:
                        succeeded.add(sym)
                        print(f"✅ {title} {sym} ({cost}s)")
                    else:
                        pending.append(sym)
                        batch_fail += 1
                        print(f"⚠️ {title} {sym} FAIL ({cost}s)")

                # 批间停顿：如果这一批失败多，停更久
                sleep_sec = BATCH_SLEEP_SEC + (8 if batch_fail >= max(3, len(batch)//3) else 0)
                if i + BATCH_SIZE < len(current):
                    print(f"😴 batch cooldown {sleep_sec}s ...")
                    time.sleep(sleep_sec)

            if pending:
                # 轮间停顿
                round_sleep = max(12, BATCH_SLEEP_SEC // 2)
                print(f"🛌 round cooldown {round_sleep}s, still pending={len(pending)}")
                time.sleep(round_sleep)

        # 最终失败
        final_fail = [s for s in symbols if s not in succeeded]
        self.failures[fail_bucket_name].extend(final_fail)
        return len(final_fail) == 0

    # ---------- run ----------
    def run(self, mode="full"):
        """
        mode:
          - full: market_daily + hourly + daily/weekly
          - hourly_only: only hourly
        """
        mode = (mode or "full").strip().lower()
        start_all = time.time()
        print(f"🚀 DataManagerV5 started mode={mode}")
        print(f"⚙️ BATCH_SIZE={BATCH_SIZE}, BATCH_SLEEP_SEC={BATCH_SLEEP_SEC}, MAX_ROUNDS={MAX_ROUNDS}")

        if mode == "full":
            ok_mkt = self.sync_market_daily()
            if ok_mkt:
                print("✅ market_daily done")
            else:
                print("⚠️ market_daily partial/failed")

        # 1) 小时线：对全池子做“直到成功或到上限轮数”
        self._run_batch_rounds(
            symbols=ETF_POOL,
            task_func=self.sync_hourly,
            fail_bucket_name="hourly_failed",
            title="hourly"
        )

        # 2) 日/周线：只对今天未同步的做，同样分批多轮
        if mode == "full":
            targets = [s for s in ETF_POOL if not self.is_daily_synced_today(s)]
            if targets:
                def _daily_task(sym):
                    ok = self.sync_daily_weekly(sym)
                    if ok:
                        self.mark_daily_synced_today(sym)
                        self._save_daily_state()
                    return ok

                self._run_batch_rounds(
                    symbols=targets,
                    task_func=_daily_task,
                    fail_bucket_name="daily_failed",
                    title="daily_weekly"
                )
            else:
                print("⏭️ 所有symbol今日已完成日/周线同步。")

        if self.failures["market_daily_failed"] or self.failures["hourly_failed"] or self.failures["daily_failed"]:
            self.failures["notes"].append("存在失败项；已采用分批+多轮重试。可下轮任务继续补齐。")

        self._save_daily_state()
        self._save_fail_report()
        print(f"🧾 fail report -> {FAIL_REPORT_FILE}")
        print(f"✅ finished in {round(time.time()-start_all,2)}s")


if __name__ == "__main__":
    import sys
    mode = "full"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    DataManagerV5().run(mode=mode)
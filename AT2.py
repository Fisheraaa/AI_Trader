import sqlite3
import pandas as pd
import requests
import time
import os
import json
import re
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Set, Tuple
from dotenv import load_dotenv

load_dotenv()

# ================= 配置区 =================
ONE_API_URL = os.getenv("ONE_API_URL", "http://127.0.0.1:3000/v1/chat/completions")
ONE_API_TOKEN = os.getenv("ONE_API_TOKEN", "REPLACE_WITH_YOUR_TOKEN")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "REPLACE_WITH_YOUR_WEBHOOK")

if not ONE_API_TOKEN:
    raise RuntimeError("ONE_API_TOKEN 未加载到环境变量，请检查 .env 是否被读取")

DB_PATH = "data/db/trading.db"
POS_FILE = "my_positions.json"  # 升级为 JSON
POS_LEGACY_FILE = "my_positions.txt"  # 兼容旧格式
KNOWLEDGE_FILE = "knowledge_base.md"
DECISION_LOG_FILE = "data/ai_decision_log.json"

# 动态 RR 档位（定版）
VOL_LOW = 15.0
VOL_HIGH = 25.0
RR_LOW_VOL = 1.8
RR_MID_VOL = 2.0
RR_HIGH_VOL = 2.5

CRASH_THRESHOLD = -3.0
KB_MAX_RECORDS = 300
MAX_CANDIDATES = 12
# ==========================================


class AutoTraderV3:
    def __init__(self):
        self.headers = {"Authorization": f"Bearer {ONE_API_TOKEN}", "Content-Type": "application/json"}
        os.environ["no_proxy"] = "*"

        self.model_tiers: List[List[str]] = [
            ["deepseek-ai/deepseek-v3.2", "meta/llama-3.1-405b-instruct"],
            ["meta/llama-3.3-70b-instruct", "mistralai/mistral-large-2-instruct"],
            ["nvidia/llama-3.1-nemotron-70b-instruct", "google/gemma-2-27b-it"],
        ]

        self.request_timeout_s = int(os.getenv("LLM_REQUEST_TIMEOUT_S", "300"))
        self.tier_timeout_s = int(os.getenv("LLM_TIER_TIMEOUT_S", "360"))
        self.primary_model_attempts = int(os.getenv("LLM_PRIMARY_ATTEMPTS", "3"))
        self.primary_backoff_s = int(os.getenv("LLM_PRIMARY_BACKOFF_S", "8"))
        self.model_blacklist: Set[str] = set()
        self.verbose = True

        os.makedirs("data", exist_ok=True)
        self.ensure_knowledge_base()
        self.ensure_decision_log()

    def log(self, msg: str):
        if self.verbose:
            print(msg)

    @staticmethod
    def beijing_now() -> datetime:
        return datetime.now(timezone(timedelta(hours=8)))

    @staticmethod
    def beijing_now_str(fmt="%Y-%m-%d %H:%M:%S"):
        return AutoTraderV3.beijing_now().strftime(fmt)

    def _summarize_errors(self, errors: List[Tuple[str, str]]) -> str:
        if not errors:
            return "No errors recorded."
        return "\n".join([f"{i}. [{w}] {m}" for i, (w, m) in enumerate(errors, 1)])

    # ----------------- Files -----------------
    def ensure_knowledge_base(self):
        if not os.path.exists(KNOWLEDGE_FILE):
            tpl = (
                "# Knowledge Base - Trading System\n\n"
                "## Core Principles\n"
                "- 不使用未来数据做当日决策（防止数据泄漏）\n"
                "- 复盘标签需可解释，避免错误学习\n"
                "- 避免只记忆个别样本，防止过拟合\n\n"
                "## Winning Patterns\n"
                "- （可由系统逐步补充）\n\n"
                "## Bad Calls\n"
                "- （可由系统逐步补充）\n\n"
                "## Reflection Records (FIFO, max 300)\n"
            )
            with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
                f.write(tpl)

    def ensure_decision_log(self):
        if not os.path.exists(DECISION_LOG_FILE):
            with open(DECISION_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    def load_knowledge_text(self) -> str:
        try:
            with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return "Knowledge base unavailable."

    def load_decision_log(self) -> List[Dict[str, Any]]:
        try:
            with open(DECISION_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_decision_log(self, data: List[Dict[str, Any]]):
        with open(DECISION_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ----------------- Positions (JSON + legacy migrate) -----------------
    def _migrate_legacy_positions_if_needed(self):
        if os.path.exists(POS_FILE):
            return
        if not os.path.exists(POS_LEGACY_FILE):
            return

        positions: Dict[str, Dict[str, Any]] = {}
        with open(POS_LEGACY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [x.strip() for x in line.split(",")]
                try:
                    symbol = parts[0]
                    buy_price = float(parts[1])
                    role = parts[2].lower() if len(parts) >= 3 and parts[2] else "attack"
                    qty = float(parts[3]) if len(parts) >= 4 and parts[3] else 0.0
                    if role not in ("core", "attack"):
                        role = "attack"

                    positions[symbol] = {
                        "buy_price": buy_price,
                        "role": role,
                        "qty": qty,
                        "entry_time": self.beijing_now_str(),
                        "initial_stop": None,
                        "tp1": None,
                        "tp1_done_ratio": 0.0,
                        "tp2_done_ratio": 0.0,
                        "trailing_stop": None,
                        "last_action_ts": self.beijing_now_str(),
                    }
                except Exception:
                    continue

        self.save_positions(positions)
        self.log(f"✅ 已从 {POS_LEGACY_FILE} 迁移到 {POS_FILE}")

    def load_my_positions(self) -> Dict[str, Dict[str, Any]]:
        self._migrate_legacy_positions_if_needed()
        if not os.path.exists(POS_FILE):
            return {}
        try:
            with open(POS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def save_positions(self, positions: Dict[str, Dict[str, Any]]):
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)

    # ----------------- Market Context -----------------
    def get_all_tables(self) -> List[str]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tech_%'")
        tables = [t[0] for t in cursor.fetchall()]
        conn.close()
        # 只保留小时线主表，排除 daily/weekly
        return [t for t in tables if not t.startswith("tech_daily_") and not t.startswith("tech_weekly_")]

    def get_market_context(self, table_name: str) -> Optional[Dict[str, Any]]:
        symbol = table_name.replace("tech_", "")
        try:
            conn = sqlite3.connect(DB_PATH)
            df = pd.read_sql(f"SELECT * FROM {table_name} ORDER BY date DESC LIMIT 80", conn)
            conn.close()
            if df.empty:
                return None

            curr = df.iloc[0]
            prev = df.iloc[1] if len(df) > 1 else curr

            vol_ratio = float(curr.get("vol_ratio", 1) or 1)
            vol_status = "放量" if vol_ratio > 1.5 else "缩量" if vol_ratio < 0.7 else "平量"
            close_price = float(curr.get("close", 0) or 0)
            boll_up = float(curr.get("boll_up", 1e18) or 1e18)
            boll_low = float(curr.get("boll_low", -1e18) or -1e18)

            boll_pos = (
                "触碰上轨(压力)"
                if close_price >= boll_up
                else "触碰下轨(支撑)"
                if close_price <= boll_low
                else "通道内运行"
            )
            kdj_j = float(curr.get("kdj_j", 50) or 50)
            k_status = "超买" if kdj_j > 90 else "超跌" if kdj_j < 10 else "中性"
            trend = "多头向上" if float(curr.get("macd_hist", 0) or 0) > 0 else "空头向下"

            ma480 = float(curr.get("ma480", 0) or 0)
            ma480_pos = "上方(中长线健康)" if close_price >= ma480 else "下方(仅短反看待)"
            obv = float(curr.get("obv", 0) or 0)
            obv_ma20 = float(curr.get("obv_ma20", 0) or 0)
            obv_trend = "OBV上行" if obv >= obv_ma20 else "OBV下行(警惕诱多)"

            bias20 = round(float(curr.get("bias20", 0) or 0), 2)
            bias60 = round(float(curr.get("bias60", 0) or 0), 2)
            bias480 = round(float(curr.get("bias480", 0) or 0), 2)
            bias_desc = f"BIAS20:{bias20}% BIAS60:{bias60}% BIAS480:{bias480}%"

            atr14 = float(curr.get("atr14", 0) or 0)
            atr_stop = close_price - 2 * atr14
            day_change = 0.0
            try:
                prev_close = float(prev["close"])
                if prev_close != 0:
                    day_change = round((close_price - prev_close) / prev_close * 100, 2)
            except Exception:
                pass

            desc = (
                f"代码:{symbol} | 现价:{round(close_price, 4)} | MA480:{ma480_pos} | "
                f"{boll_pos} | KDJ:{k_status} | 量能:{vol_status}(比率:{round(vol_ratio, 2)}) | "
                f"MACD:{trend} | {obv_trend} | ATR14:{round(atr14, 4)} | "
                f"动态止损(2ATR):{round(atr_stop, 4)} | {bias_desc} | 近一根涨跌:{day_change}%"
            )

            return {
                "symbol": symbol,
                "desc": desc,
                "price": close_price,
                "atr14": atr14,
                "atr_stop": float(atr_stop),
                "ma480": ma480,
                "obv": obv,
                "obv_ma20": obv_ma20,
                "day_change": day_change,
                "vol_ratio": vol_ratio,
                "macd_hist": float(curr.get("macd_hist", 0) or 0),
                "kdj_j": kdj_j,
                "boll_up": boll_up,
                "boll_low": boll_low,
            }
        except Exception:
            return None

    def get_market_environment(self) -> Dict[str, Any]:
        """
        读取 market_daily + tech_daily_% 计算市场环境锚点
        """
        env = {
            "summary": "市场环境数据不足，按中波动默认阈值处理。",
            "volatility_level": "mid",
            "volatility_value": 20.0,
            "rr_new_threshold": RR_MID_VOL,
            "rr_add_threshold": round(RR_MID_VOL * 0.75, 2),
            "raw": {},
        }

        conn = sqlite3.connect(DB_PATH)
        try:
            md = pd.read_sql(
                "SELECT * FROM market_daily ORDER BY date DESC LIMIT 400",
                conn
            )
            if md.empty:
                return env

            md["date"] = pd.to_datetime(md["date"])
            latest_date = md["date"].max()
            latest = md[md["date"] == latest_date].copy()

            # 关键指数描述
            focus_codes = ["sh000001", "sz399001", "sz399006", "sh000300", "sh000905"]
            focus = latest[latest["index_code"].isin(focus_codes)].copy()

            idx_lines = []
            vols = []
            for _, row in focus.iterrows():
                code = row["index_code"]
                close = float(row["close"])
                ma20 = float(row["ma20"]) if pd.notna(row["ma20"]) else close
                pct = float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0.0
                hv20 = float(row["hv20"]) if pd.notna(row["hv20"]) else None
                if hv20 is not None:
                    vols.append(hv20)
                ma_flag = "站上MA20" if close >= ma20 else "跌破MA20"
                idx_lines.append(f"- {code}: {round(pct,2)}%，{ma_flag}")

            # 宽度：tech_daily_% close > ma20 比例
            tables = pd.read_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tech_daily_%'",
                conn
            )["name"].tolist()

            above_cnt, total_cnt = 0, 0
            new_high_20, new_low_20 = 0, 0
            for t in tables:
                try:
                    d = pd.read_sql(f"SELECT date, close, ma20 FROM {t} ORDER BY date DESC LIMIT 21", conn)
                    if d.empty:
                        continue
                    d["date"] = pd.to_datetime(d["date"])
                    d = d.sort_values("date")
                    last = d.iloc[-1]
                    total_cnt += 1
                    if pd.notna(last["ma20"]) and float(last["close"]) > float(last["ma20"]):
                        above_cnt += 1

                    if len(d) >= 20:
                        last20 = d.tail(20)
                        c = float(last["close"])
                        if c >= float(last20["close"].max()):
                            new_high_20 += 1
                        if c <= float(last20["close"].min()):
                            new_low_20 += 1
                except Exception:
                    continue

            breadth = (above_cnt / total_cnt * 100) if total_cnt > 0 else 50.0
            if breadth >= 60:
                breadth_state = "强势"
            elif breadth <= 40:
                breadth_state = "弱势"
            else:
                breadth_state = "中性"

            hv = float(sum(vols) / len(vols)) if vols else 20.0
            if hv < VOL_LOW:
                vol_level = "low"
                rr_new = RR_LOW_VOL
            elif hv > VOL_HIGH:
                vol_level = "high"
                rr_new = RR_HIGH_VOL
            else:
                vol_level = "mid"
                rr_new = RR_MID_VOL
            rr_add = round(rr_new * 0.75, 2)

            summary = "【当前市场环境】\n"
            if idx_lines:
                summary += "\n".join(idx_lines) + "\n"
            summary += f"- 市场宽度: 收盘在MA20上方占比 {round(breadth,2)}%，{breadth_state}\n"
            summary += f"- 新高/新低(20期): {new_high_20}/{new_low_20}\n"
            summary += f"- 波动率(hv20): {round(hv,2)}%，RR门槛={rr_new}, RR_add门槛={rr_add}"

            env = {
                "summary": summary,
                "volatility_level": vol_level,
                "volatility_value": round(hv, 2),
                "rr_new_threshold": rr_new,
                "rr_add_threshold": rr_add,
                "raw": {
                    "breadth": breadth,
                    "new_high_20": new_high_20,
                    "new_low_20": new_low_20,
                    "index_lines": idx_lines,
                },
            }
            return env
        except Exception as e:
            self.log(f"[WARN] get_market_environment failed: {e}")
            return env
        finally:
            conn.close()

    def get_multi_timeframe_summary(self, symbol: str) -> Dict[str, Any]:
        """
        小时(tech_) + 日线(tech_daily_) + 周线(tech_weekly_)
        """
        out = {
            "symbol": symbol,
            "hourly": "数据不足",
            "daily": "数据不足",
            "weekly": "数据不足",
            "resonance": "共振不足",
        }

        conn = sqlite3.connect(DB_PATH)
        try:
            # hourly
            try:
                h = pd.read_sql(f"SELECT * FROM tech_{symbol} ORDER BY date DESC LIMIT 30", conn)
                if not h.empty:
                    c = h.iloc[0]
                    macd = "多头" if float(c.get("macd_hist", 0) or 0) > 0 else "空头"
                    kdj = float(c.get("kdj_j", 50) or 50)
                    kdj_s = "超买" if kdj > 90 else "超跌" if kdj < 10 else "中性"
                    out["hourly"] = f"MACD:{macd}, KDJ:{kdj_s}, vol_ratio:{round(float(c.get('vol_ratio',1) or 1),2)}"
            except Exception:
                pass

            # daily
            try:
                d = pd.read_sql(f"SELECT * FROM tech_daily_{symbol} ORDER BY date DESC LIMIT 40", conn)
                if not d.empty:
                    c = d.iloc[0]
                    price = float(c.get("close", 0) or 0)
                    ma20 = float(c.get("ma20", price) or price)
                    ma60 = float(c.get("ma60", ma20) or ma20)
                    macd = "多头" if float(c.get("macd_hist", 0) or 0) > 0 else "空头"
                    ma_rel = "站上MA20" if price >= ma20 else "跌破MA20"
                    trend = "多头结构" if price >= ma20 >= ma60 else "震荡/弱势"
                    out["daily"] = f"{ma_rel}, MACD:{macd}, 结构:{trend}"
            except Exception:
                pass

            # weekly
            try:
                w = pd.read_sql(f"SELECT * FROM tech_weekly_{symbol} ORDER BY date DESC LIMIT 40", conn)
                if not w.empty:
                    c = w.iloc[0]
                    p = float(c.get("close", 0) or 0)
                    ma20w = float(c.get("ma20", p) or p)
                    macd = "多头" if float(c.get("macd_hist", 0) or 0) > 0 else "空头"
                    wk = "上升趋势" if p >= ma20w else "下行/震荡"
                    out["weekly"] = f"{wk}, MA20周线:{'上方' if p>=ma20w else '下方'}, MACD:{macd}"
            except Exception:
                pass

            # resonance
            bull_votes = 0
            for key in ("hourly", "daily", "weekly"):
                txt = out[key]
                if "多头" in txt or "上升趋势" in txt or "站上MA20" in txt or "上方" in txt:
                    bull_votes += 1
            if bull_votes >= 3:
                out["resonance"] = "多周期共振良好"
            elif bull_votes == 2:
                out["resonance"] = "中等共振"
            else:
                out["resonance"] = "共振不足"

            return out
        finally:
            conn.close()

    def get_structured_features(self, symbol: str, market_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        ctx = market_map.get(symbol, {})
        mtf = self.get_multi_timeframe_summary(symbol)
        return {
            "symbol": symbol,
            "price": ctx.get("price"),
            "atr14": ctx.get("atr14"),
            "atr_stop": ctx.get("atr_stop"),
            "trend": {
                "ma480_relation": "above" if (ctx.get("price", 0) >= ctx.get("ma480", 1e18)) else "below",
                "macd_hist": ctx.get("macd_hist"),
                "kdj_j": ctx.get("kdj_j"),
            },
            "volume": {
                "vol_ratio": ctx.get("vol_ratio"),
                "obv": ctx.get("obv"),
                "obv_ma20": ctx.get("obv_ma20"),
                "obv_state": "up" if (ctx.get("obv", 0) >= ctx.get("obv_ma20", 0)) else "down",
            },
            "multi_timeframe": mtf,
            "day_change": ctx.get("day_change"),
        }

    # ----------------- LLM Calling -----------------
    def call_model_once(self, model: str, prompt: str, temperature: float = 0.2) -> Dict[str, str]:
        if model in self.model_blacklist:
            raise Exception(f"Model blacklisted: {model}")

        self.log(f"  [Requesting] -> {model} ...")
        data = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature}

        try:
            r = requests.post(ONE_API_URL, headers=self.headers, json=data, timeout=self.request_timeout_s)
        except requests.exceptions.ReadTimeout as e:
            raise Exception(f"Read timed out ({self.request_timeout_s}s): {e}")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Request exception: {e}")

        if r.status_code == 200:
            try:
                content = r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                raise Exception(f"Bad JSON response: {e}; raw={r.text[:400]}")
            return {"model": model, "content": content}

        if r.status_code == 404:
            self.model_blacklist.add(model)

        raise Exception(f"HTTP {r.status_code}: {r.text[:500]}")

    def race_call_models_by_tiers(self, prompt: str, temperature=0.2, skip_models: Optional[Set[str]] = None) -> Dict[str, str]:
        skip_models = set(skip_models or set())
        errors: List[Tuple[str, str]] = []
        last_error_msg = None

        for tier_i, tier in enumerate(self.model_tiers, start=1):
            active_models = [m for m in tier if m not in skip_models and m not in self.model_blacklist]
            if not active_models:
                self.log(f"\n🚀 Tier {tier_i} skipped.")
                continue

            self.log(f"\n🚀 Tier {tier_i} Racing Started: {active_models}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_models)) as ex:
                future_to_model = {ex.submit(self.call_model_once, m, prompt, temperature): m for m in active_models}
                try:
                    for future in concurrent.futures.as_completed(future_to_model, timeout=self.tier_timeout_s):
                        model = future_to_model[future]
                        try:
                            res = future.result()
                            self.log(f"✅ WINNER FOUND (Tier {tier_i}): {res['model']}")
                            return res
                        except Exception as e:
                            msg = str(e)
                            errors.append((f"tier{tier_i}:{model}", msg))
                            last_error_msg = msg
                            self.log(f"❌ {model} failed: {msg}")
                except concurrent.futures.TimeoutError as e:
                    msg = f"Tier {tier_i} timed out ({self.tier_timeout_s}s): {e}"
                    errors.append((f"tier{tier_i}", msg))
                    last_error_msg = msg
                    self.log(msg)

        raise Exception(
            "CRITICAL ERROR: All model tiers failed.\n"
            f"Last error: {last_error_msg}\n"
            f"Error summary:\n{self._summarize_errors(errors)}"
        )

    def call_with_quality_priority(self, prompt: str, temperature: float = 0.2) -> Dict[str, str]:
        primary_model = self.model_tiers[0][0]
        primary_errors: List[Tuple[str, str]] = []

        self.log(f"\n🏆 Quality-Priority Mode: primary={primary_model}")
        for attempt in range(1, self.primary_model_attempts + 1):
            self.log(f"🔥 Primary attempt {attempt}/{self.primary_model_attempts}")
            try:
                res = self.call_model_once(primary_model, prompt, temperature)
                self.log(f"✅ Primary success: {res['model']}")
                return res
            except Exception as e:
                primary_errors.append((f"primary:{attempt}", str(e)))
                self.log(f"❌ Primary failed: {e}")
                if attempt < self.primary_model_attempts:
                    time.sleep(self.primary_backoff_s)

        self.log("⬇️ Entering fallback racing...")
        try:
            return self.race_call_models_by_tiers(prompt, temperature, skip_models={primary_model})
        except Exception as e:
            raise Exception(
                "Primary failed and fallback failed.\n"
                f"Primary errors:\n{self._summarize_errors(primary_errors)}\n\nFallback:\n{e}"
            )

    # ----------------- Risk Control -----------------
    def parse_instruction_lines(self, text: str) -> List[Dict[str, Any]]:
        """
        解析格式（定稿）：
        [代码] | [胜率%] | [动作] | [防守价] | [目标价] | [持仓天数预估]
        """
        rows = []
        for line in text.splitlines():
            if "|" not in line:
                continue
            parts = [x.strip() for x in line.split("|")]
            if len(parts) < 6:
                continue

            symbol = parts[0].strip("[] ")
            action = parts[2].strip("[] ").lower()
            try:
                win_rate = float(re.sub(r"[^0-9.\-]", "", parts[1]))
                stop = float(re.sub(r"[^0-9.\-]", "", parts[3]))
                target = float(re.sub(r"[^0-9.\-]", "", parts[4]))
                days = int(float(re.sub(r"[^0-9.\-]", "", parts[5])))
            except Exception:
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "win_rate": win_rate,
                    "action": action,
                    "stop": stop,
                    "target": target,
                    "days": days,
                    "raw": line,
                }
            )
        return rows

    def apply_risk_gate(
        self,
        rows: List[Dict[str, Any]],
        market_map: Dict[str, Dict[str, Any]],
        positions: Dict[str, Dict[str, Any]],
        rr_new_threshold: float,
        rr_add_threshold: float,
    ) -> List[Dict[str, Any]]:
        validated = []
        for r in rows:
            symbol = r["symbol"]
            if symbol not in market_map:
                r["status"] = "INVALID_SIGNAL"
                r["reason"] = "无市场数据"
                validated.append(r)
                continue

            price = market_map[symbol]["price"]
            action = r["action"]

            # 周期硬约束
            if symbol in positions:
                role = positions[symbol].get("role", "attack")
                if role == "core" and not (15 <= r["days"] <= 35):
                    r["status"] = "INVALID_SIGNAL"
                    r["reason"] = "底仓周期违反约束(15-35天)"
                    validated.append(r)
                    continue
                if role == "attack" and not (3 <= r["days"] <= 5):
                    r["status"] = "INVALID_SIGNAL"
                    r["reason"] = "攻击仓周期违反约束(3-5天)"
                    validated.append(r)
                    continue

            # 新开仓
            if symbol not in positions and action in ("买入", "开仓", "加仓", "持股", "持有"):
                denom = (price - r["stop"])
                rr = (r["target"] - price) / denom if denom > 0 else -999
                r["rr"] = round(rr, 4)

                if rr < rr_new_threshold:
                    r["status"] = "INVALID_SIGNAL"
                    r["reason"] = f"RR不足(新开仓), rr={round(rr,4)} < {rr_new_threshold}"
                    r["action"] = "观望"
                else:
                    r["status"] = "VALID"

            # 加仓
            elif symbol in positions and action in ("加仓",):
                denom = (price - r["stop"])
                rr_add = (r["target"] - price) / denom if denom > 0 else -999
                r["rr_add"] = round(rr_add, 4)

                if rr_add < rr_add_threshold:
                    r["status"] = "INVALID_SIGNAL"
                    r["reason"] = f"RR_add不足, rr_add={round(rr_add,4)} < {rr_add_threshold}"
                    r["action"] = "观望"
                else:
                    r["status"] = "VALID"
            else:
                r["status"] = "VALID"

            validated.append(r)
        return validated

    def check_trailing_stops(self, positions: Dict[str, Dict[str, Any]], market_map: Dict[str, Dict[str, Any]]) -> List[str]:
        """
        阶梯止盈与移动止损:
        - TP1: 1.5R 到达 -> 减仓50%，止损抬到成本
        - TP2: 2.5R 到达 -> 再减仓30%，剩余仓位跟踪
        - trailing: max(MA10, close-2ATR) 近似用 (close-2ATR) 与已有trailing_stop抬升
        """
        actions = []
        changed = False

        for symbol, p in positions.items():
            if symbol not in market_map:
                continue
            price = float(market_map[symbol]["price"])
            atr14 = float(market_map[symbol].get("atr14", 0) or 0)
            buy = float(p.get("buy_price", 0) or 0)
            if buy <= 0:
                continue

            initial_stop = p.get("initial_stop")
            if initial_stop is None:
                # 默认初始止损：2ATR
                initial_stop = max(0.0001, buy - 2 * atr14) if atr14 > 0 else buy * 0.97
                p["initial_stop"] = round(initial_stop, 6)
                changed = True

            risk_r = buy - float(initial_stop)
            if risk_r <= 0:
                continue

            tp1 = buy + 1.5 * risk_r
            tp2 = buy + 2.5 * risk_r
            p["tp1"] = round(tp1, 6)

            tp1_done = float(p.get("tp1_done_ratio", 0) or 0)
            tp2_done = float(p.get("tp2_done_ratio", 0) or 0)
            qty = float(p.get("qty", 0) or 0)

            # TP1
            if price >= tp1 and tp1_done < 0.5 and qty > 0:
                reduce_qty = round(qty * 0.5, 6)
                p["qty"] = round(max(0.0, qty - reduce_qty), 6)
                p["tp1_done_ratio"] = 0.5
                # 移动止损到成本
                p["trailing_stop"] = round(max(float(p.get("trailing_stop") or 0), buy), 6)
                p["last_action_ts"] = self.beijing_now_str()
                actions.append(f"{symbol}: 触发TP1，减仓50%({reduce_qty})，止损上移至成本价{round(buy,4)}")
                changed = True

            # TP2
            qty = float(p.get("qty", 0) or 0)
            if price >= tp2 and tp2_done < 0.3 and qty > 0:
                reduce_qty = round(qty * 0.3, 6)
                p["qty"] = round(max(0.0, qty - reduce_qty), 6)
                p["tp2_done_ratio"] = 0.3
                p["last_action_ts"] = self.beijing_now_str()
                actions.append(f"{symbol}: 触发TP2，再减仓30%({reduce_qty})，剩余仓位进入强化跟踪止损")
                changed = True

            # trailing stop 动态抬升
            trail_candidate = price - 2 * atr14 if atr14 > 0 else price * 0.98
            old_trail = float(p.get("trailing_stop") or 0)
            new_trail = max(old_trail, trail_candidate, buy if tp1_done >= 0.5 else 0)
            p["trailing_stop"] = round(new_trail, 6)

            if price <= new_trail and qty > 0:
                actions.append(f"{symbol}: 触发移动止损，建议清仓。现价{round(price,4)} <= trailing_stop {round(new_trail,4)}")

        if changed:
            self.save_positions(positions)
        return actions

    # ----------------- Reflection -----------------
    def run_reflection(self, market_map: Dict[str, Dict[str, Any]], market_env: Dict[str, Any]):
        logs = self.load_decision_log()
        if len(logs) < 2:
            return "今日复盘：样本不足，暂不触发。"

        yesterday = logs[-2]
        bad_records = []
        total = 0
        correct = 0
        x = 0.8  # 观望阈值

        for rec in yesterday.get("signals", []):
            symbol = rec.get("symbol")
            action = rec.get("action", "")
            if symbol not in market_map:
                continue
            move = float(market_map[symbol].get("day_change", 0) or 0)

            total += 1
            ok = False
            if action in ("持股", "持有", "加仓", "买入", "开仓"):
                ok = move > 0
                if move <= CRASH_THRESHOLD:
                    bad_records.append(
                        f"{symbol}: 昨建议[{action}]，当前近一根涨跌{move}% <= {CRASH_THRESHOLD}%（暴跌阈值）"
                    )
            elif action in ("减仓", "卖出", "清仓"):
                ok = move < 0
            elif action in ("观望",):
                ok = abs(move) < x

            if ok:
                correct += 1
            else:
                env_short = f"波动率{market_env.get('volatility_value','?')}%，宽度信息:{market_env.get('raw',{}).get('breadth','?')}"
                bad_records.append(
                    f"{symbol}: 昨建议[{action}]，当前近一根涨跌{move}%；环境={env_short}，需复核是否逆势交易。"
                )

        if total == 0:
            return "今日复盘：可评估样本为0。"

        acc = round(correct / total * 100, 2)

        block_time = self.beijing_now_str("%Y-%m-%d %H:%M:%S")
        new_block = (
            f"\n### Reflection @{block_time}\n"
            f"- 标签定义：暴跌阈值={CRASH_THRESHOLD}%，观望阈值={x}%\n"
            "- 风险提示：避免错误学习、避免数据泄漏、避免个例过拟合\n"
            f"- 胜率回溯：{correct}/{total} = {acc}%\n"
            "- 错误/风险事件：\n"
            + ("\n".join([f"  - {x_}" for x_ in bad_records]) if bad_records else "  - 无")
            + "\n- 教训：弱势宽度(<40%) + 上轨附近追高，应降权处理；高波动环境优先等待RR更优的回撤入场。\n"
        )

        self.append_kb_reflection_block(new_block)
        if bad_records:
            return f"今日复盘：胜率{acc}%，触发{len(bad_records)}条风险反思，已写入知识库。"
        return f"今日复盘：胜率{acc}%，未发现显著风险事件。"

    def append_kb_reflection_block(self, block: str):
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            text = f.read()

        reflections = re.findall(r"(### Reflection @[\s\S]*?)(?=\n### Reflection @|\Z)", text)
        reflections.append(block.strip() + "\n")

        if len(reflections) > KB_MAX_RECORDS:
            reflections = reflections[-KB_MAX_RECORDS:]

        base = re.sub(r"\n### Reflection @[\s\S]*$", "", text).rstrip()
        final_text = base + "\n\n" + "\n".join(reflections) + "\n"

        with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            f.write(final_text)

    # ----------------- Output -----------------
    def build_prompt(
        self,
        market_env_summary: str,
        position_features: List[Dict[str, Any]],
        candidate_features: List[Dict[str, Any]],
        knowledge_text: str,
    ) -> str:
        pos_json = json.dumps(position_features, ensure_ascii=False, indent=2)
        can_json = json.dumps(candidate_features[:MAX_CANDIDATES], ensure_ascii=False, indent=2)

        return (
            "你是一位顶级量化主理人。禁止使用“可能/也许/大概”等模糊词。\n"
            "必须先执行红蓝对抗，再给最终裁决。输出必须可执行。\n\n"
            "【硬约束】\n"
            "1) 仓位周期约束：core=15~35天；attack=3~5天。\n"
            "2) 新开仓必须满足 RR=(Target-Price)/(Price-Stop) >= 动态门槛。\n"
            "3) 加仓必须满足 RR_add=(NewTarget-Price)/(Price-TrailingStop) >= 动态门槛。\n"
            "4) 防守价优先采用 ATR 止损逻辑（多头：当前价-2*ATR）。\n"
            "5) 输出必须包含明确动作，不得模糊。\n\n"
            "【知识库（最高优先级）】\n"
            f"{knowledge_text}\n\n"
            "步骤1：环境判断\n"
            f"{market_env_summary}\n\n"
            "步骤2：个股分析（结构化特征）\n"
            "【持仓特征JSON】\n"
            f"{pos_json}\n\n"
            "【候选特征JSON】\n"
            f"{can_json}\n\n"
            "步骤3：对抗性辩论（精简输出）\n"
            "- 每个重点标的：给出多头理由与空头证伪点，最终只保留“辩论结论”。\n\n"
            "步骤4：最终裁决\n"
            "- 若空头证伪成立，放弃交易并给出观望。\n"
            "- 若不成立，给出交易计划（动作、防守价、目标价、持仓天数预估、RR逻辑）。\n\n"
            "步骤5：输出四部分：\n"
            "A. 【波段防御区】\n"
            "B. 【短线进攻区】\n"
            "C. 【实战指令】（每行严格格式）\n"
            "   [代码] | [胜率%] | [动作] | [防守价] | [目标价] | [持仓天数预估]\n"
            "D. 【今日避雷笔记】\n"
            "注意：你可以精简辩论文本，但必须明确写出“辩论结论”。\n"
        )

    def send_to_feishu(self, model: str, advice: str):
        bj_time = self.beijing_now_str("%H:%M")
        title = f"📈 AI 深度决策报告 | 模型:{model} | 北京时间:{bj_time}"
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [[{"tag": "text", "text": advice}]],
                    }
                }
            },
        }
        requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)

    def save_today_log(self, model: str, raw_advice: str, checked_rows: List[Dict[str, Any]], market_env: Dict[str, Any]):
        logs = self.load_decision_log()
        logs.append(
            {
                "ts_bj": self.beijing_now_str(),
                "model": model,
                "market_env": market_env,
                "raw_advice": raw_advice,
                "signals": checked_rows,
            }
        )
        if len(logs) > 1000:
            logs = logs[-1000:]
        self.save_decision_log(logs)

    def run_engine(self):
        positions = self.load_my_positions()
        tables = self.get_all_tables()

        pos_diag, new_opps = [], []
        market_map: Dict[str, Dict[str, Any]] = {}

        for t in tables:
            ctx = self.get_market_context(t)
            if not ctx:
                continue
            symbol = ctx["symbol"]
            market_map[symbol] = ctx

            if symbol in positions:
                buy_p = float(positions[symbol].get("buy_price", 0) or 0)
                role = positions[symbol].get("role", "attack")
                qty = float(positions[symbol].get("qty", 0) or 0)
                profit = round((ctx["price"] - buy_p) / buy_p * 100, 2) if buy_p > 0 else 0.0
                pos_diag.append(
                    f"{ctx['desc']} | 买入价:{buy_p} | 角色:{role} | 数量:{qty} | 盈亏:{profit}%"
                )
            else:
                new_opps.append(ctx["desc"])

        market_env = self.get_market_environment()
        reflection_summary = self.run_reflection(market_map, market_env)

        # 结构化特征
        pos_symbols = [s for s in market_map.keys() if s in positions]
        cand_symbols = [s for s in market_map.keys() if s not in positions][:MAX_CANDIDATES]

        position_features = [self.get_structured_features(s, market_map) for s in pos_symbols]
        candidate_features = [self.get_structured_features(s, market_map) for s in cand_symbols]

        knowledge_text = self.load_knowledge_text()
        prompt = self.build_prompt(
            market_env_summary=market_env["summary"],
            position_features=position_features,
            candidate_features=candidate_features,
            knowledge_text=knowledge_text,
        )

        res = self.call_with_quality_priority(prompt, temperature=0.2)
        raw_advice = res["content"]

        parsed_rows = self.parse_instruction_lines(raw_advice)
        checked_rows = self.apply_risk_gate(
            parsed_rows,
            market_map,
            positions,
            rr_new_threshold=market_env["rr_new_threshold"],
            rr_add_threshold=market_env["rr_add_threshold"],
        )

        trailing_actions = self.check_trailing_stops(positions, market_map)

        gate_lines = ["\n【风控闸门结果】"]
        if not checked_rows:
            gate_lines.append("- 未解析到标准化实战指令，建议检查模型输出格式。")
        else:
            for r in checked_rows:
                rr_txt = ""
                if "rr" in r:
                    rr_txt = f" | RR:{r['rr']}"
                if "rr_add" in r:
                    rr_txt = f" | RR_add:{r['rr_add']}"
                reason = f" | 原因:{r.get('reason','-')}" if r.get("status") != "VALID" else ""
                gate_lines.append(
                    f"- {r['symbol']} | 动作:{r['action']} | 状态:{r.get('status','UNKNOWN')}{rr_txt}{reason}"
                )

        trail_lines = ["\n【阶梯止盈/移动止损】"]
        if trailing_actions:
            trail_lines.extend([f"- {x}" for x in trailing_actions])
        else:
            trail_lines.append("- 今日未触发。")

        final_msg = (
            f"{raw_advice}\n\n"
            f"{reflection_summary}\n"
            + "\n".join(gate_lines)
            + "\n"
            + "\n".join(trail_lines)
        )

        self.send_to_feishu(res["model"], final_msg)
        self.save_today_log(res["model"], raw_advice, checked_rows, market_env)


if __name__ == "__main__":
    AutoTraderV3().run_engine()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
把 my_positions.txt 导入/合并到 my_positions.json
- 支持代码写法：sh510300 / sz159915 / 510300(自动推断前缀)
- txt格式（每行）：
  代码,买入价[,role][,qty]
  例如：
  510300,3.85,core,1000
  sh518880,4.12,attack,500
  sz159915,2.01
"""

import os
import json
import argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Tuple, List


DEFAULT_TXT = "my_positions.txt"
DEFAULT_JSON = "my_positions.json"


def beijing_now_str(fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.now(timezone(timedelta(hours=8))).strftime(fmt)


def normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return s
    if s.startswith("sh") or s.startswith("sz"):
        return s

    # 只给6位数字做自动推断
    if len(s) == 6 and s.isdigit():
        if s.startswith("6"):
            return "sh" + s
        if s.startswith(("0", "3", "1")):
            return "sz" + s
        if s.startswith("5"):
            return "sh" + s
        return "sz" + s

    return s


def parse_line(line: str) -> Tuple[bool, Dict[str, Any], str]:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return False, {}, "skip"

    parts = [x.strip() for x in raw.split(",")]
    if len(parts) < 2:
        return False, {}, f"字段不足: {raw}"

    try:
        symbol = normalize_symbol(parts[0])
        buy_price = float(parts[1])

        role = "attack"
        qty = 0.0
        if len(parts) >= 3 and parts[2]:
            role = parts[2].lower()
        if role not in ("core", "attack"):
            role = "attack"

        if len(parts) >= 4 and parts[3]:
            qty = float(parts[3])

        item = {
            "symbol": symbol,
            "buy_price": buy_price,
            "role": role,
            "qty": qty,
        }
        return True, item, ""
    except Exception as e:
        return False, {}, f"解析失败: {raw} | {e}"


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_json(path: str, data: Dict[str, Any]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_record(item: Dict[str, Any], old: Dict[str, Any] = None) -> Dict[str, Any]:
    now = beijing_now_str()
    old = old or {}

    # 保留旧的运行态字段，避免覆盖掉策略状态
    rec = {
        "buy_price": item["buy_price"],
        "role": item["role"],
        "qty": item["qty"],
        "entry_time": old.get("entry_time", now),
        "initial_stop": old.get("initial_stop", None),
        "tp1": old.get("tp1", None),
        "tp1_done_ratio": old.get("tp1_done_ratio", 0.0),
        "tp2_done_ratio": old.get("tp2_done_ratio", 0.0),
        "trailing_stop": old.get("trailing_stop", None),
        "last_action_ts": now,
    }
    return rec


def import_positions(txt_path: str, json_path: str, mode: str = "merge"):
    """
    mode:
      - merge: 合并/覆盖同symbol，不删除json中其他symbol
      - replace: 用txt完全替换json
    """
    if not os.path.exists(txt_path):
        raise FileNotFoundError(f"未找到文件: {txt_path}")

    base = {} if mode == "replace" else load_json(json_path)

    ok_cnt, fail_cnt = 0, 0
    warnings: List[str] = []
    parsed_symbols = set()

    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            ok, item, msg = parse_line(line)
            if not ok:
                if msg != "skip":
                    fail_cnt += 1
                    warnings.append(msg)
                continue

            sym = item["symbol"]
            parsed_symbols.add(sym)
            old = base.get(sym, {})
            base[sym] = build_record(item, old=old)
            ok_cnt += 1

    # replace模式下，只保留txt里出现的symbol
    if mode == "replace":
        base = {k: v for k, v in base.items() if k in parsed_symbols}

    save_json(json_path, base)

    print("✅ 导入完成")
    print(f"- TXT文件: {txt_path}")
    print(f"- JSON文件: {json_path}")
    print(f"- 模式: {mode}")
    print(f"- 成功: {ok_cnt}")
    print(f"- 失败: {fail_cnt}")
    if warnings:
        print("\n⚠️ 失败明细（前20条）:")
        for w in warnings[:20]:
            print(f"  - {w}")


def main():
    parser = argparse.ArgumentParser(description="Import positions from txt to json")
    parser.add_argument("--txt", default=DEFAULT_TXT, help="txt file path (default: my_positions.txt)")
    parser.add_argument("--json", default=DEFAULT_JSON, help="json file path (default: my_positions.json)")
    parser.add_argument("--mode", choices=["merge", "replace"], default="merge", help="merge or replace")
    args = parser.parse_args()

    import_positions(args.txt, args.json, args.mode)


if __name__ == "__main__":
    main()
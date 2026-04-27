import sqlite3
import os

db_path = "data/db/trading.db"
os.makedirs(os.path.dirname(db_path), exist_ok=True)

def init():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 核心表：存决策指令，为以后 OpenClaw 全自动做准备
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,        -- 股票/ETF代码 [cite: 6]
            action TEXT,        -- BUY/SELL/HOLD
            reason TEXT,        -- AI的逻辑理由 [cite: 22]
            instruction_json TEXT, -- 结构化指令，预留给 OpenClaw
            is_synced BOOLEAN DEFAULT 0 -- 状态标志位：是否已在APP/模拟盘执行 [cite: 44, 56]
        )
    ''')
    
    # 存行情数据 [cite: 30, 31]
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            symbol TEXT,
            price REAL,
            volume REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print(f"✅ 数据库已持久化至 D 盘: {db_path}")

if __name__ == "__main__":
    init()
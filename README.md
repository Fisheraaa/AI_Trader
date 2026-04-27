# AI Trader

<div style="text-align: right; margin-bottom: 20px;">
  <a href="#english-version" style="padding: 8px 16px; background-color: #f0f0f0; border-radius: 4px; text-decoration: none; color: #333; margin-right: 10px;">English</a>
  <a href="#chinese-version" style="padding: 8px 16px; background-color: #0366d6; border-radius: 4px; text-decoration: none; color: white;">中文</a>
</div>

<a name="chinese-version"></a>

## 项目简介

AI Trader是一个集成了硬性量化指标(Hard Quant)、宏观环境风控(Market Context)与AI多模型辩论反思(AI Multi-agent Debate)的闭环自动化交易系统。

- **双赛道虚拟盘**：国信A股赛道(沪深A股范围)和中金ETF赛道(ETF范围)，启动资金各100万元
- **时间周期**：3.17-5.8（共35个交易日）
- **技术架构**：WSL2运行环境、D盘数据持久化、多模型冗余、飞书半自动执行

## 核心功能

### 1. AI多模型辩论与决策重塑
- 角色对抗：系统内部模拟多个决策席位（如激进派、保守派、技术派）
- 逻辑纠偏：当主模型给出买入信号时，辩论模块会强制寻找该标的的风险点
- 共识机制：只有通过多轮对话，且量化分与AI信心值双双达标后，系统才会判定为"高把握"信号
- 少样本学习：结合knowledge_base.md，AI在决策前会复习历史的"好球"与"坏球"

### 2. 多周期技术共振策略
- 周线(Weekly)：定牛熊。价格是否在MA20(20周均线)之上，决定了大的操作方向
- 日线(Daily)：定结构。寻找均线多头排列、MACD金叉以及站稳长周期均线(MA480)
- 小时线(Hourly)：定切入。在细分周期寻找缩量回调或放量突破的精确买点

### 3. 市场环境与宽度风控
- 环境评估：计算主要指数（如沪深300）的波动率(HV)和市场宽度（站上MA20的股票比例）
- 风险模式(Risk-Off)：若市场宽度< 40%或波动率骤增，系统自动进入防御状态，大幅收缩入场门槛

### 4. 动态量化评分加权
- 量化因子(65%)：包含MA(移动平均线)趋势、MACD动能、OBV(能量潮)成交量、ATR(平均真实波幅)波动风险
- AI信心分(35%)：提取技术形态背后的"势"与"意图"

## 技术架构

### 系统组成
- **DataManager2.py**：数据管理员，负责增量更新、技术指标计算、数据持久化
- **AT3.py**：策略大脑，负责形态翻译、多维初筛、AI精选
- **scheduler.py**：任务调度器，监控进程状态，自动重刷数据并重启任务
- **飞书机器人**：动态预警中心，发送行情提示、决策预警、重连简报和硬核风险预警

### 多模型投委会
- **第一梯队(主力)**：DeepSeek-R1 / Llama-3.3-70B（核心策略官）
- **第二梯队(候补)**：Mistral-Large-2 / Nemotron-340B（速度极快）
- **第三梯队(兜底)**：Gemma-2-27B / Phi-3-Medium（极致鲁棒）
- **特定任务型**：Mixtral-8x22B / Llama-3-70B（新闻情报官）

## 快速开始

### 环境要求
- Docker
- WSL2（Windows用户）
- Python 3.10+

### 安装步骤
1. 克隆仓库
   ```bash
   git clone <repository-url>
   cd AI_Trader
   ```

2. 配置环境变量
   ```bash
   cp .env.example .env
   # 编辑.env文件，填写相关配置
   ```

3. 启动服务
   ```bash
   docker compose up -d --build
   ```

4. 查看日志
   ```bash
   docker logs -f ai_trader_app
   ```

## 配置说明

### 核心配置文件
- **.env**：环境变量配置，包含API密钥、Webhook地址等
- **etf_list.txt**：存储要监控的ETF代码
- **my_positions.json**：当前持仓配置

### 环境变量
- **ONE_API_URL**：OneAPI服务地址
- **ONE_API_TOKEN**：OneAPI访问令牌
- **FEISHU_WEBHOOK**：飞书机器人Webhook地址
- **QUANT_WEIGHT**：量化因子权重
- **AI_WEIGHT**：AI信心分权重

## 目录结构

```
AI_Trader/
├── data/              # 数据目录
│   ├── db/            # 数据库文件
│   ├── logs/          # 日志文件
│   └── ai_decision_log.json  # AI决策日志
├── resource/          # 资源文件
├── .venv/             # 虚拟环境
├── analyze_evolution.py  # 进化分析脚本
├── DataManager2.py    # 数据管理模块
├── AT3.py             # 策略主模块
├── scheduler.py       # 任务调度器
├── positions_import_from_txt.py  # 持仓导入脚本
├── docker-compose.yml # Docker配置
├── Dockerfile         # Docker构建文件
├── requirements.txt   # Python依赖
└── README.md          # 项目说明
```

## 运行状态检查

### 查看运行日志
```bash
docker logs -f ai_trader_app
```

### 检查数据收集状态
```bash
docker exec -it ai_trader_app bash -lc "python3 - <<'PY'
import sqlite3, pandas as pd
conn=sqlite3.connect('/app/data/db/trading.db')
m=pd.read_sql('select max(date) as d from market_daily', conn)
print('market_daily max date:', m.iloc[0]['d'])
d=pd.read_sql("select name from sqlite_master where type='table' and name like 'tech_daily_%' limit 1", conn)
if not d.empty:
    t=d.iloc[0]['name']
    x=pd.read_sql(f'select max(date) as d from {t}', conn)
    print(t, 'max date:', x.iloc[0]['d'])
conn.close()
PY"
```

### 查看进程状态
```bash
docker exec -it ai_trader_app bash -lc "ps -ef | grep AT3.py | grep -v grep"
```

## 贡献指南

1. Fork本仓库
2. 创建特性分支
3. 提交更改
4. 推送到分支
5. 创建Pull Request

## 许可证

本项目采用MIT许可证。

---

<div style="text-align: right; margin-bottom: 20px;">
  <a href="#english-version" style="padding: 8px 16px; background-color: #0366d6; border-radius: 4px; text-decoration: none; color: white; margin-right: 10px;">English</a>
  <a href="#chinese-version" style="padding: 8px 16px; background-color: #f0f0f0; border-radius: 4px; text-decoration: none; color: #333;">中文</a>
</div>

<a name="english-version"></a>

# AI Trader

## Project Introduction

AI Trader is a closed-loop automated trading system that integrates Hard Quant indicators, Market Context risk control, and AI Multi-agent Debate reflection.

- **Dual-track virtual trading**：Guosen A-share track (Shanghai and Shenzhen A-share range) and CICC ETF track (ETF range), with 1 million yuan startup capital for each
- **Time period**：3.17-5.8 (35 trading days in total)
- **Technical architecture**：WSL2 runtime environment, D-drive data persistence, multi-model redundancy, Feishu semi-automatic execution

## Core Features

### 1. AI Multi-model Debate and Decision Reshaping
- Role confrontation：The system internally simulates multiple decision seats (such as radical, conservative, technical)
- Logic correction：When the main model gives a buy signal, the debate module will forcefully find risk points for the target
- Consensus mechanism：Only after multiple rounds of dialogue, and when both the quantitative score and AI confidence value meet the standards, the system will determine it as a "high-confidence" signal
- Few-shot learning：Combined with knowledge_base.md, AI will review historical "good calls" and "bad calls" before making decisions

### 2. Multi-timeframe Technical Resonance Strategy
- Weekly：Determine bull/bear. Whether the price is above MA20 (20-week moving average) determines the overall operation direction
- Daily：Determine structure. Look for long-term moving average alignment, MACD golden cross, and standing above long-term moving average (MA480)
- Hourly：Determine entry point. Find precise buying points with shrinking retracement or volume breakout in the细分周期

### 3. Market Environment and Breadth Risk Control
- Environment assessment：Calculate volatility (HV) and market breadth (percentage of stocks above MA20) for major indices (such as CSI 300)
- Risk-Off mode：If market breadth < 40% or volatility surges, the system automatically enters defensive state and significantly tightens entry thresholds

### 4. Dynamic Quantitative Scoring Weighting
- Quantitative factors (65%)：Include MA (moving average) trend, MACD momentum, OBV (On-Balance Volume) trading volume, ATR (Average True Range) volatility risk
- AI confidence score (35%)：Extract the "momentum" and "intent" behind technical patterns

## Technical Architecture

### System Components
- **DataManager2.py**：Data manager, responsible for incremental updates, technical indicator calculation, data persistence
- **AT3.py**：Strategy brain, responsible for pattern translation, multi-dimensional initial screening, AI selection
- **scheduler.py**：Task scheduler, monitors process status, automatically refreshes data and restarts tasks
- **Feishu robot**：Dynamic early warning center, sending market tips, decision warnings, reconnection briefings, and hard risk warnings

### Multi-model Committee
- **First tier (main force)**：DeepSeek-R1 / Llama-3.3-70B (core strategy officer)
- **Second tier (backup)**：Mistral-Large-2 / Nemotron-340B (extremely fast)
- **Third tier (bottom support)**：Gemma-2-27B / Phi-3-Medium (extremely robust)
- **Specific task type**：Mixtral-8x22B / Llama-3-70B (news intelligence officer)

## Quick Start

### Environment Requirements
- Docker
- WSL2 (Windows users)
- Python 3.10+

### Installation Steps
1. Clone the repository
   ```bash
   git clone <repository-url>
   cd AI_Trader
   ```

2. Configure environment variables
   ```bash
   cp .env.example .env
   # Edit .env file and fill in relevant configuration
   ```

3. Start the service
   ```bash
   docker compose up -d --build
   ```

4. View logs
   ```bash
   docker logs -f ai_trader_app
   ```

## Configuration Instructions

### Core Configuration Files
- **.env**：Environment variable configuration, including API keys, Webhook addresses, etc.
- **etf_list.txt**：Store ETF codes to be monitored
- **my_positions.json**：Current position configuration

### Environment Variables
- **ONE_API_URL**：OneAPI service address
- **ONE_API_TOKEN**：OneAPI access token
- **FEISHU_WEBHOOK**：Feishu robot Webhook address
- **QUANT_WEIGHT**：Quantitative factor weight
- **AI_WEIGHT**：AI confidence score weight

## Directory Structure

```
AI_Trader/
├── data/              # Data directory
│   ├── db/            # Database files
│   ├── logs/          # Log files
│   └── ai_decision_log.json  # AI decision log
├── resource/          # Resource files
├── .venv/             # Virtual environment
├── analyze_evolution.py  # Evolution analysis script
├── DataManager2.py    # Data management module
├── AT3.py             # Main strategy module
├── scheduler.py       # Task scheduler
├── positions_import_from_txt.py  # Position import script
├── docker-compose.yml # Docker configuration
├── Dockerfile         # Docker build file
├── requirements.txt   # Python dependencies
└── README.md          # Project description
```

## Running Status Check

### View running logs
```bash
docker logs -f ai_trader_app
```

### Check data collection status
```bash
docker exec -it ai_trader_app bash -lc "python3 - <<'PY'
import sqlite3, pandas as pd
conn=sqlite3.connect('/app/data/db/trading.db')
m=pd.read_sql('select max(date) as d from market_daily', conn)
print('market_daily max date:', m.iloc[0]['d'])
d=pd.read_sql("select name from sqlite_master where type='table' and name like 'tech_daily_%' limit 1", conn)
if not d.empty:
    t=d.iloc[0]['name']
    x=pd.read_sql(f'select max(date) as d from {t}', conn)
    print(t, 'max date:', x.iloc[0]['d'])
conn.close()
PY"
```

### View process status
```bash
docker exec -it ai_trader_app bash -lc "ps -ef | grep AT3.py | grep -v grep"
```

## Contribution Guide

1. Fork this repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License.

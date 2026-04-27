import re
import json
import os
from datetime import datetime

# Configuration paths (consistent with your project structure)
KNOWLEDGE_FILE = "knowledge_base.md"
DECISION_LOG_FILE = "data/ai_decision_log.json"

def analyze_ai_evolution():
    print(f"统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50)

    # 1. Parse "reflection rules" from knowledge base
    if os.path.exists(KNOWLEDGE_FILE):
        print("【1. 知识库进化规则提取】")
        with open(KNOWLEDGE_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Use regex to match "lessons" or "avoid pitfalls" entries in the knowledge base
        # Assuming your md file uses ### or - to record lessons
        lessons = re.findall(r"(?:教训|避坑|反思|Bad Case).*?\n(.*?)(?=\n\n|###|$)", content, re.S)
        
        if not lessons:
            print("  - 暂未发现明确的进化规则，AI 还在积累经验中。")
        for i, lesson in enumerate(lessons):
            clean_lesson = lesson.strip().replace('\n', ' ')
            print(f"  规则 {i+1}: {clean_lesson}")
    else:
        print(f"  错误: 未找到 {KNOWLEDGE_FILE}")

    print("\n" + "="*50)

    # 2. Parse "self-correction" from decision logs
    if os.path.exists(DECISION_LOG_FILE):
        print("【2. 最近决策中的“自我纠偏”记录】")
        try:
            with open(DECISION_LOG_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)
            
            # Filter out cases where AI (Critic) rejected high-scoring targets
            # This demonstrates AI evolution: no longer blindly following indicators, but identifying traps
            rejections = [log for log in logs if log.get('quant_score', 0) > 70 and log.get('action') == 'WAIT']
            
            if not rejections:
                print("  - 近期暂无“高分否决”案例。")
            else:
                for entry in rejections[-5:]: # Show only the last 5
                    symbol = entry.get('symbol', 'Unknown')
                    q_score = entry.get('quant_score')
                    reason = entry.get('ai_reason', '无具体原因')
                    print(f"  标的: {symbol} | 量化分: {q_score} -> [AI 进化干预: 否决]")
                    print(f"  干预逻辑: {reason[:100]}...") # Truncate to show main logic
                    print("-" * 30)
        except Exception as e:
            print(f"  解析日志出错: {e}")
    else:
        print(f"  提示: 尚未生成决策日志 {DECISION_LOG_FILE}")

if __name__ == "__main__":
    analyze_ai_evolution()
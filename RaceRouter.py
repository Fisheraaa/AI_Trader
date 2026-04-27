import requests
import concurrent.futures
import time
import json

class RaceRouter:
    def __init__(self):
        # Local One API address
        self.api_url = "http://127.0.0.1:3000/v1/chat/completions"
        # REPLACE with your actual One API Token from the dashboard
        self.api_token = "sk-NiIPGGLqzqxQTH1DE35244F2Bb884fCdA916Ce1a79AaB801" 

    def call_model(self, model, prompt):
        print(f"  [Requesting] -> {model}...") 
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3 # Lower temperature for more stable financial analysis
        }
        
        # 30s timeout to keep the race moving
        response = requests.post(self.api_url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            res_json = response.json()
            return {
                "model": model, 
                "content": res_json['choices'][0]['message']['content']
            }
        else:
            raise Exception(f"Status Code {response.status_code}: {response.text}")

    def fast_analyze(self, market_info):
        # Professional English Prompt for better AI reasoning
        prompt = (
            f"Act as a senior quantitative trader. Analyze the following market data "
            f"and provide a concise 'Actionable Advice' (Buy/Hold/Sell/Watch) with 3 bullet points: {market_info}"
        )
        
        # Tiered Racing Strategy
        tiers = [
            ["meta/llama-3.1-405b-instruct", "deepseek-ai/deepseek-v3.2"],
            ["meta/llama-3.3-70b-instruct", "mistralai/mistral-large-2-instruct"],
            ["nvidia/llama-3.1-nemotron-70b-instruct", "google/gemma-2-27b-it"]
        ]

        for i, tier in enumerate(tiers):
            print(f"\n🚀 Tier {i+1} Racing Started: {tier}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                # Submit both models in the current tier
                future_to_model = {executor.submit(self.call_model, m, prompt): m for m in tier}
                
                try:
                    # as_completed returns futures as they finish
                    for future in concurrent.futures.as_completed(future_to_model, timeout=35):
                        try:
                            result = future.result()
                            print(f"✅ WINNER FOUND: {result['model']}")
                            return result['content']
                        except Exception as e:
                            print(f"❌ {future_to_model[future]} failed: {e}")
                            continue
                except concurrent.futures.TimeoutError:
                    print(f"⏳ Tier {i+1} timed out, moving to next tier...")

        return "CRITICAL ERROR: All models failed to respond."

if __name__ == "__main__":
    router = RaceRouter()
    print("=== AI Trader Racing System Activated ===")
    
    start_time = time.time()
    # Example market data for testing
    test_data = "Stock: 600519.SH, Price: 1600, Change: -3%, Volume: Decreasing"
    
    advice = router.fast_analyze(test_data)
    
    print("\n" + "="*40)
    print(f"FINAL AI ADVICE:\n{advice}")
    print("="*40)
    print(f"Total Latency: {time.time() - start_time:.2f} seconds")
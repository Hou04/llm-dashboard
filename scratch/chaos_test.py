
import asyncio
import httpx
import time
import random
import uuid

# CONFIGURATION
# We hit the /log endpoint because it allows us to simulate high traffic 
# without actually spending money on real AI calls.
GATEWAY_URL = "http://127.0.0.1:8000/v1/gateway/log"

# STEP 1: Create a tenant in your dashboard (e.g. "Grok_Tenant")
# STEP 2: Create a Virtual Key for that tenant and paste it here:
# (Note: The script uses the key for Authentication to the dashboard API)
VIRTUAL_KEY = "llm_vk_test_3d9ecaf76a65fc1d83c6edf51cc27c612ec86bd58ac20a2e" 

async def send_log(client, mode="normal"):
    """Sends a single log entry to the gateway."""
    
    request_id = str(uuid.uuid4())
    
    if mode == "normal":
        model = "llama3-8b-8192"
        input_tokens = random.randint(50, 150)
        output_tokens = random.randint(100, 300)
        cost = (input_tokens + output_tokens) * 0.00001
        status = "success"
        error = None
    elif mode == "spam":
        # Simulate a massive cost spike
        model = "gpt-4-turbo"
        input_tokens = random.randint(10000, 20000)
        output_tokens = random.randint(2000, 4000)
        cost = (input_tokens + output_tokens) * 0.0001 # Higher price
        status = "success"
        error = None
    elif mode == "fail":
        # Simulate a provider failure
        model = "claude-3-opus"
        input_tokens = 50
        output_tokens = 0
        cost = 0
        status = "error"
        error = "429: Rate limit exceeded"
    else:
        model = "llama3-8b"
        input_tokens = 100
        output_tokens = 100
        cost = 0.002
        status = "success"
        error = None

    headers = {
        "X-Virtual-Key": VIRTUAL_KEY,
        "Content-Type": "application/json"
    }
    
    # Round cost to 8 decimal places to avoid Pydantic validation errors
    cost = round(cost, 8)
    
    # Matching LogCallRequest schema
    payload = {
        "tenant_id": "groq tenant", # Make sure this tenant exists in your DB!
        "request_id": request_id,
        "provider": "groq" if mode != "fail" else "anthropic",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost,
        "duration_ms": random.randint(200, 2500),
        "status": status,
        "error_message": error,
        "prompt_text": "Simulated prompt content for demo purposes.",
        "completion_text": "Simulated completion content for demo purposes." if status == "success" else ""
    }

    try:
        response = await client.post(GATEWAY_URL, json=payload, headers=headers, timeout=60.0)
        if response.status_code == 201:
            print(f"[{mode.upper()}] Status: {response.status_code} | Result: {response.json().get('decision', 'N/A')}")
        else:
            print(f"[{mode.upper()}] Status: {response.status_code} | Error: {response.text}")
    except Exception as e:
        print(f"Network Error: {type(e).__name__}")
    
    # Tiny sleep to let the DB breathe
    await asyncio.sleep(0.1)

async def run_wave(count=10, mode="normal"):
    print(f"\n🚀 SIMULATING {mode.upper()} TRAFFIC: {count} logs...")
    async with httpx.AsyncClient() as client:
        # Limit concurrency to 5 to avoid overloading local DB
        sem = asyncio.Semaphore(5)
        
        async def send_with_sem(m):
            async with sem:
                await send_log(client, m)
                
        tasks = [send_with_sem(mode) for _ in range(count)]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    print("--- OBSIDIAN AI INFRASTRUCTURE DEMO GENERATOR ---")
    
    # WAVE 1: Normal operations
    #asyncio.run(run_wave(20, "normal"))
    
    # WAVE 2: Anomalous Cost (Uncomment to show spike)
    asyncio.run(run_wave(50, "spam"))
    
    # WAVE 3: Provider Failures (Uncomment to show red logs)
    #asyncio.run(run_wave(30, "fail"))
    
    print("\n✅ Simulation complete. Refresh your Dashboard!")

import requests

API_URL = "http://localhost:8000/v1"

print("1. Logging in...")
res = requests.post(f"{API_URL}/auth/login", json={"username": "admin", "password": "Admin@1234"})
print("Login status:", res.status_code)
if res.status_code != 200:
    print(res.text)
    exit(1)

tokens = res.json()
access = tokens['access_token']
refresh = tokens['refresh_token']
print("Got access token")

print("\n2. Fetching /auth/me")
res = requests.get(f"{API_URL}/auth/me", headers={"Authorization": f"Bearer {access}"})
print("Me status:", res.status_code)
print(res.text[:200])

print("\n3. Fetching /dashboard/executive")
res = requests.get(f"{API_URL}/dashboard/executive?period_days=30", headers={"Authorization": f"Bearer {access}"})
print("Executive status:", res.status_code)
if res.status_code != 200:
    print(res.text[:200])
else:
    print("Success")

print("\n4. Testing refresh token")
res = requests.post(f"{API_URL}/auth/refresh", json={"refresh_token": refresh})
print("Refresh status:", res.status_code)
if res.status_code != 200:
    print(res.text[:200])
else:
    print("Refresh Success")

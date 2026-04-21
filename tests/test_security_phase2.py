"""Quick smoke test for security modules."""
from core.sanitize import sanitize_text, sanitize_identifier

# Test XSS tag stripping (tags removed, text content preserved)
result = sanitize_text('<script>alert(1)</script>Hello')
assert '<script>' not in result
assert 'Hello' in result

result2 = sanitize_text('<iframe src="evil.com"></iframe>OK')
assert '<iframe' not in result2
assert 'OK' in result2

# Test event handler removal
result3 = sanitize_text('text with onclick="steal()" more')
assert 'onclick' not in result3

# Test None passthrough
assert sanitize_text(None) is None

# Test identifier sanitization
assert sanitize_identifier('enterprise_corp') == 'enterprise_corp'
assert sanitize_identifier(None) is None

# Test truncation
long_text = 'x' * 20000
assert len(sanitize_text(long_text, max_length=500)) == 500

print("All sanitization tests PASSED")

from core.rate_limit import RateLimitMiddleware, DEFAULT_RATE, AUTH_RATE, BURST_RATE
assert DEFAULT_RATE == 200
assert AUTH_RATE == 10
assert BURST_RATE == 60
print("Rate limit config OK")

from core.security_headers import SecurityHeadersMiddleware
print("Security headers middleware OK")

print("\n=== All Phase 2 security modules verified ===")

import re, urllib.request

base = "http://127.0.0.1:3002"
html = urllib.request.urlopen(base + "/login", timeout=10).read().decode("utf-8", "ignore")
srcs = set(re.findall(r'src="(/_next/[^"]+)"', html))
found_8001 = False
found_8000 = False
for s in srcs:
    try:
        js = urllib.request.urlopen(base + s, timeout=10).read().decode("utf-8", "ignore")
    except Exception:
        continue
    if "localhost:8001" in js:
        found_8001 = True
    if "localhost:8000" in js:
        found_8000 = True
print("chunks_scanned", len(srcs))
print("references_8001", found_8001)
print("references_8000", found_8000)

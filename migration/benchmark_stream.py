#!/usr/bin/env python3
import requests
import time
import sys

url = "http://localhost:8080/defects/query/stream"
payload = {"query": "slow search timeout", "n_results": 3}
headers = {"Content-Type": "application/json"}

start = time.time()
response = requests.post(url, json=payload, headers=headers, stream=True)

if response.status_code != 200:
    print(f"status,{response.status_code},0.0")
    sys.exit(1)

total_bytes = 0
tokens = 0
first_result_time = None
for line in response.iter_lines():
    if line:
        tokens += 1
        total_bytes += len(line)
        if b'event: results' in line and not first_result_time:
            first_result_time = time.time() - start
end = time.time()

total_time = end - start
print(f"stream,200,{total_time:.3f},{tokens},{total_bytes}")
if first_result_time:
    print(f"first_results_time,{first_result_time:.3f}")

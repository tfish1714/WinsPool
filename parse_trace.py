import io, re
with open('import_trace.txt', 'rb') as f:
    raw = f.read()

text = raw.decode('utf-16le', errors='ignore')

lines = text.splitlines()
results = []

for i, line in enumerate(lines):
    if line.startswith('import time:'):
        parts = line.split('|')
        if len(parts) >= 2:
            try:
                self_time = int(line[12:line.find('|')].strip())
                cum_time = int(parts[1].strip())
                mod_name = lines[i-1].strip()
                results.append((cum_time, self_time, mod_name))
            except Exception as e:
                pass

results.sort(reverse=True, key=lambda x: x[0])
print("---- SLOWEST CUMULATIVE IMPORTS ----")
for cum, s_time, name in results[:20]:
    if cum > 1000000:  # > 1s
        print(f"{cum/1000000:.3f}s (self: {s_time/1000000:.3f}s) : {name}")

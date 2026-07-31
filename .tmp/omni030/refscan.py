import io, subprocess, os

pairs = [l.strip().split('|') for l in io.open('.tmp/omni030/map.txt', encoding='utf-8') if '|' in l]
terms = set()
for old, new in pairs:
    base = os.path.basename(old)
    stem = os.path.splitext(base)[0]
    terms.add(base)
    terms.add(stem)
terms = sorted(terms, key=len, reverse=True)
excl = ['.git', 'node_modules', 'REGISTRY.md', 'scan_state.json', '.tmp_debt.json',
        '_tmp_debt.json', 'reconcile-runs', '.tmp/omni030']
hits = {}
for t in terms:
    r = subprocess.run(['git', 'grep', '-l', '-F', '--', t], capture_output=True, text=True)
    files = [f for f in r.stdout.splitlines() if not any(e in f for e in excl)]
    r2 = subprocess.run(['rg', '-l', '-F', '--no-ignore', '-g', '!.git', '-g', '!node_modules',
                         '-g', '!.tmp/omni030', '--', t], capture_output=True, text=True)
    for f in r2.stdout.splitlines():
        f = f.replace(os.sep, '/')
        if any(e in f for e in excl):
            continue
        if f not in files:
            files.append(f)
    if files:
        hits[t] = sorted(set(files))
out = io.open('.tmp/omni030/refhits.txt', 'w', encoding='utf-8')
for t in terms:
    if t in hits:
        out.write('## %s\n' % t)
        for f in hits[t]:
            out.write('   %s\n' % f)
out.close()
print('terms with hits:', sum(1 for t in terms if t in hits))

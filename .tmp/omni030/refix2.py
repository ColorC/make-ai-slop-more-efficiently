import io, os, subprocess

pairs = [l.strip().split('|') for l in io.open('.tmp/omni030/map.txt', encoding='utf-8') if '|' in l]
terms = {}
for old, new in pairs:
    so = os.path.splitext(os.path.basename(old))[0]
    sn = os.path.splitext(os.path.basename(new))[0]
    terms[so] = sn
ordered = sorted(terms.items(), key=lambda kv: len(kv[0]))

EXCLUDE_SUBSTR = [
    '/.git/', 'node_modules/', 'docs/tech_debt/', 'var/tech_debt/',
    '_tmp_debt.json', '.tmp/omni030/', 'docs/ARCH-CHANGES.jsonl',
    'docs/plans/_archive/', 'docs/plans/dashboard/_archive/',
    'packages/services/_core/guardian/rules/naming.py',
    '.omni/protection_baseline.json', '.omni/cron/', '__pycache__/',
    'runtime_snapshots/',
]

def excluded(relp):
    p = '/' + relp.replace('\\', '/')
    return any(x in p for x in EXCLUDE_SUBSTR)

# 一次 rg 调用匹配全部字面模式（-F -f），按目录分片
pat_file = '.tmp/omni030/patterns.txt'
io.open(pat_file, 'w', encoding='utf-8').write('\n'.join(terms.keys()) + '\n')

SCOPES = ['src', 'tests', 'scripts', 'docs',
          'data/domains/demogame_ux', 'data/domains/demogame', 'data/domains/test_team']
files = set()
r = subprocess.run(['git', '-c', 'core.quotepath=false', 'grep', '-l', '-F', '-f', pat_file, '--', '.'],
                   capture_output=True)
files.update(f for f in r.stdout.decode('utf-8').splitlines() if f)
for scope in SCOPES:
    r2 = subprocess.run(['rg', '-l', '-F', '-f', pat_file, '--no-ignore',
                         '-g', '!.git', '-g', '!node_modules', '-g', '!__pycache__',
                         '-g', '!runtime_snapshots', '--', scope],
                        capture_output=True)
    files.update(f.replace('\\', '/') for f in r2.stdout.decode('utf-8').splitlines() if f)

candidates = []
for f in sorted(files):
    f = f.strip('"')
    if not excluded(f):
        candidates.append(f)
print('candidate files:', len(candidates))

changed = {}
for f in candidates:
    try:
        data = io.open(f, 'rb').read()
    except OSError:
        continue
    if b'\x00' in data[:8192]:
        continue
    lines = data.split(b'\n')
    hit_terms = []
    for i, ln in enumerate(lines):
        if b'material_id=' in ln:
            continue
        for old_t, new_t in ordered:
            ob = old_t.encode('utf-8')
            if ob in ln:
                ln = ln.replace(ob, new_t.encode('utf-8'))
                hit_terms.append(old_t)
        lines[i] = ln
    if hit_terms:
        io.open(f, 'wb').write(b'\n'.join(lines))
        changed[f] = sorted(set(hit_terms))

out = io.open('.tmp/omni030/refixed.txt', 'w', encoding='utf-8')
for f, ts in sorted(changed.items()):
    out.write('%s :: %s\n' % (f, ','.join(ts)))
out.close()
print('files changed:', len(changed))

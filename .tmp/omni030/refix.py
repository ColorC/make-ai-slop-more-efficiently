import io, os, subprocess, sys

REPO = os.getcwd()
pairs = [l.strip().split('|') for l in io.open('.tmp/omni030/map.txt', encoding='utf-8') if '|' in l]

# term map: old stem -> new stem, ascending length (shortest first) so that
# uniformly-derived longer names (test_X, run_X) are rewritten via the short term.
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
    '.omni/protection_baseline.json',
]

def excluded(relp):
    p = '/' + relp.replace('\\', '/')
    return any(x in p for x in EXCLUDE_SUBSTR)

def find_files(term):
    files = set()
    r = subprocess.run(['git', '-c', 'core.quotepath=false', 'grep', '-l', '-F', '--', term],
                       capture_output=True)
    files.update(f for f in r.stdout.decode('utf-8').splitlines() if f)
    # rg 在本仓根目录静默搜不到东西（数据目录太大/遍历问题），按目录分片搜
    for scope in ['src', 'tests', 'scripts', 'docs', '.omni',
                  'data/domains/demogame_ux', 'data/domains/demogame', 'data/domains/test_team']:
        r2 = subprocess.run(['rg', '-l', '-F', '--no-ignore', '-g', '!.git', '-g', '!node_modules',
                             '-g', '!__pycache__', '-g', '!runtime_snapshots', '--', term, scope],
                            capture_output=True)
        files.update(f.replace('\\', '/') for f in r2.stdout.decode('utf-8').splitlines() if f)
    out = []
    for f in sorted(files):
        f = f.strip('"')
        if not excluded(f):
            out.append(f)
    return out

changed = {}
for old_t, new_t in ordered:
    ob = old_t.encode('utf-8')
    nb = new_t.encode('utf-8')
    for f in find_files(old_t):
        try:
            data = io.open(f, 'rb').read()
        except OSError:
            continue
        if b'\x00' in data[:8192]:
            continue
        if ob not in data:
            continue
        # keep material_id lines stable (opaque registry ids, not filenames)
        lines = data.split(b'\n')
        hit = False
        for i, ln in enumerate(lines):
            if b'material_id=' in ln:
                continue
            if ob in ln:
                lines[i] = ln.replace(ob, nb)
                hit = True
        if hit:
            io.open(f, 'wb').write(b'\n'.join(lines))
            changed.setdefault(f, []).append(old_t)

out = io.open('.tmp/omni030/refixed.txt', 'w', encoding='utf-8')
for f, ts in sorted(changed.items()):
    out.write('%s :: %s\n' % (f, ','.join(ts)))
out.close()
print('files changed:', len(changed))

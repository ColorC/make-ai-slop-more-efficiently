import io, os, re

pairs = [l.strip().split('|') for l in io.open('.tmp/omni030/map.txt', encoding='utf-8') if '|' in l]
# 反向: new stem -> old stem
rev = {}
for old, new in pairs:
    so = os.path.splitext(os.path.basename(old))[0]
    sn = os.path.splitext(os.path.basename(new))[0]
    rev[sn] = so
# 单次 bytes 正则交替，最长优先，匹配区域不重扫 → 对包含关系安全
brev = {k.encode('utf-8'): v.encode('utf-8') for k, v in rev.items()}
bpat = re.compile(b'|'.join(re.escape(k) for k in sorted(brev, key=len, reverse=True)))

files = [l.strip() for l in io.open('.tmp/omni030/revert_list.txt', encoding='utf-8') if l.strip()]
n = 0
for f in files:
    data = io.open(f, 'rb').read()
    out = bpat.sub(lambda m: brev[m.group(0)], data)
    if out != data:
        io.open(f, 'wb').write(out)
        n += 1
print('reverted files:', n, 'of', len(files))

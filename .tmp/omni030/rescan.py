"""直接用 OMNI-030 判定函数复扫全仓（git ls-files -co --exclude-standard 口径）。"""
import io, os, subprocess, sys

sys.path.insert(0, 'src')
from omnicompany.packages.services._core.guardian.rules._base import FileContext
from omnicompany.packages.services._core.guardian.rules.naming import _check_versioned_filename

r = subprocess.run(['git', '-c', 'core.quotepath=false', 'ls-files', '-co', '--exclude-standard'],
                   capture_output=True)
paths = [p for p in r.stdout.decode('utf-8').splitlines() if p]
root = os.getcwd()
hits = []
for p in paths:
    ctx = FileContext(path=p, abs_path=os.path.join(root, p.replace('/', os.sep)),
                      change_type='M', content=None)
    try:
        if _check_versioned_filename(ctx):
            hits.append(p)
    except Exception as e:
        print('ERR', p, e)
out = io.open('.tmp/omni030/rescan.txt', 'w', encoding='utf-8')
out.write('\n'.join(hits) + ('\n' if hits else ''))
out.close()
print('OMNI-030 current hits:', len(hits))
for h in hits:
    print(' ', h)

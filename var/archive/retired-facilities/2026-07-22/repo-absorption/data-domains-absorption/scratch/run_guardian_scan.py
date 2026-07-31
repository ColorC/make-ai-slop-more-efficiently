"""Guardian 确定性规则扫描（全局核心包）"""
import sys
from pathlib import Path
sys.path.insert(0, 'e:/WindowsWorkspace/omnicompany/src')

from omnicompany.packages.services.guardian.rules import RULES
from omnicompany.packages.services.guardian.rules._base import FileContext

ROOT = Path('e:/WindowsWorkspace/omnicompany/src/omnicompany')
SCAN_DIRS = [
    ROOT / 'packages',
    ROOT / 'runtime',
    ROOT / 'protocol',
    ROOT / 'core',
]

py_files = []
for d in SCAN_DIRS:
    if d.exists():
        py_files.extend(d.rglob('*.py'))

print(f'扫描 {len(py_files)} 个文件...')

violations_by_sev: dict[str, list] = {}
for f in py_files:
    try:
        content = f.read_text(encoding='utf-8', errors='replace')
        rel = str(f.relative_to(ROOT)).replace('\\', '/')
        ctx = FileContext(path=rel, abs_path=str(f), change_type='M', content=content)
        for rule in RULES:
            if rule.certainty == 'absolute':
                try:
                    if rule.check(ctx):
                        sev = rule.severity
                        violations_by_sev.setdefault(sev, []).append(
                            (rule.id, rule.name, rel)
                        )
                except Exception:
                    pass
    except Exception as e:
        print(f'  ERROR {f.name}: {e}')

total = sum(len(v) for v in violations_by_sev.values())
print(f'\n确定性违规总计: {total} 条')
for sev in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']:
    items = violations_by_sev.get(sev, [])
    if items:
        print(f'\n[{sev}] {len(items)} 条:')
        for vid, vname, vpath in items:
            print(f'  {vid} {vname}')
            print(f'    {vpath}')

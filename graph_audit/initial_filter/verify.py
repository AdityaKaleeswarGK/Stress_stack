"""Read-only mechanical audit of saved initial-filter reports against Git."""
import json
import subprocess
import sys
from pathlib import Path


def verify(report):
    def git(*args):
        return subprocess.check_output(['git', '-C', report['clone'], *args])
    rows = report['candidates'] + report['rejected']
    window = git('rev-list', '--first-parent', report['ref'], '-n', str(report['summary']['commits_visited'])).decode().splitlines()
    assert len({r['sha'] for r in rows}) == len(rows)
    assert set(window) == {r['sha'] for r in rows}
    for row in rows:
        parents = git('show', '-s', '--format=%P', row['sha']).decode().split()
        assert parents[0] == row['base_sha']
        git('cat-file', '-e', row['base_sha'] + '^{commit}')
        raw = git('diff', '--numstat', '-z', '--no-ext-diff', '--no-textconv', row['base_sha'], row['sha'], '--').split(b'\0')
        files = {}
        i = 0
        while i < len(raw) and raw[i]:
            added, deleted, path = raw[i].split(b'\t', 2)
            i += 1
            if not path:
                path = raw[i + 1]
                i += 2
            files[path.decode()] = (0 if added == b'-' else int(added), 0 if deleted == b'-' else int(deleted))
        stats = row if 'files' in row else row['patch']
        saved = {f['path']: (f['added'], f.get('deleted', f.get('removed'))) for f in stats['files']}
        assert files == saved, row['sha']
        assert sum(a + d for a, d in files.values()) == stats['loc_changed']
    return len(rows)


if __name__ == '__main__':
    for filename in sys.argv[1:]:
        report = json.loads(Path(filename).read_text())
        print(filename, verify(report), 'changes verified')

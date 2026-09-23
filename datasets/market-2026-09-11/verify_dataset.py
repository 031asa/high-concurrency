"""Offline validation of the bundled one-day market sample; no service startup."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
import tarfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from ydcore.archive_analysis import quotes,day_of
from timer_pdf.code.analysis import analyze
from timer_pdf.code.rules import Rules

def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=ROOT/'result/sample-market-2026-09-11')
    args=p.parse_args()
    expected,name=(HERE/'SHA256SUMS').read_text().strip().split(None,1)
    archive=HERE/name.strip()
    if sha(archive)!=expected:raise ValueError('Compressed dataset checksum mismatch')
    target=args.output_root.resolve();target.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'r:gz') as bundle:
        for member in bundle.getmembers():
            destination=(target/member.name).resolve()
            if target not in destination.parents or member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe archive entry: '+member.name)
            if destination.exists() and member.isfile():
                with bundle.extractfile(member) as stream:
                    if hashlib.sha256(stream.read()).hexdigest()!=sha(destination):
                        raise ValueError('Existing different file, refusing overwrite: '+member.name)
        bundle.extractall(target)
    folder=target/'market-2026-09-11'
    manifest=json.loads((folder/'manifest.json').read_text())
    for name,digest in manifest['files'].items():
        path=(folder/name).resolve()
        if folder not in path.parents or sha(path)!=digest:raise ValueError('File checksum mismatch: '+name)
    counts=Counter()
    for path in sorted((folder/'aeron-mvp').glob('*/archive/*.rec')):
        for quote in quotes(path,include_digest=True):
            if day_of(quote['received'])!=manifest['date']:raise ValueError('Record outside selected day')
            counts[quote['source']]+=1
    if dict(counts)!=manifest['sources']:raise ValueError('Record count mismatch')
    expected=json.loads((HERE/'expected-analysis.json').read_text())
    actual=analyze(folder/'aeron-mvp',folder/'time-probes',manifest['date'],Rules(ROOT/'timer_pdf/code/rules.json'))
    if actual['status']!=expected['parse_status']:raise ValueError('Parse status mismatch')
    for source,result in expected['sources'].items():
        found=actual['sources'][source]
        if found['count']!=result['count'] or dict(found['categories'])!=result['categories']:raise ValueError('Classification mismatch: '+source)
        for key,value in result['clean'].items():
            other=found['clean'][key]
            if value is None:
                if other is not None:raise ValueError('Expected unavailable metric: '+key)
            elif other is None or not math.isclose(value,other,rel_tol=1e-10,abs_tol=1e-8):raise ValueError('Statistic mismatch: '+source+'/'+key)
    print(json.dumps({'result':'PASS','date':manifest['date'],'sources':dict(counts),'unpacked':str(folder)},ensure_ascii=False))

if __name__=='__main__':main()

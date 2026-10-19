"""Append immutable non-Git ledgers to the existing evidence ZIP, preserving history."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main(bundle, results, commit):
    bundle, results = Path(bundle), Path(results)
    with zipfile.ZipFile(bundle) as z:
        if z.testzip() is not None:
            raise ValueError('Existing evidence ZIP has CRC error')
        old = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(old.pop('evidence_manifest.json'))
    for name, info in manifest['payload_files'].items():
        if sha(old[name]) != info['sha256']:
            raise ValueError('Existing payload changed: ' + name)
    base = 'https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/' + commit + '/'
    section = '\n\n## 2026-10-20 곡선 예측 구성요소 실제 평가\n\n'
    section += '공개 논문 기반10개 고정 조합을 실제 계산했으나 모두 비용 차감 후 손실로 운영 교체 미승인입니다.\n'
    section += '2026년1~9월273일은 모든 조합의 체결 경로가 완전했습니다. 이 기간 기본 비용 -20.43%~-44.70%, 비용 두 배 -48.77%~-64.32%의 가상 결과입니다.\n'
    section += '원문 전체 재현이나 LBank 실제 체결 성과가 아닙니다. 2022~2026 자료는 모두 이전 조사에서 사용했습니다.\n'
    section += '긴 전체 기간의 조합별 미확정3~5일은0수익으로 넣지 않았습니다. 예측14565행과 종료 모델별 거래14526행은 겹치는10모델의 행이며 독립 실전 거래 수가 아닙니다.\n\n'
    section += '[상세 결과](' + base + 'docs/lbank_functional_curve_validation_2026-10-20.md)\n'
    section += '[실행 코드](' + base + 'research/continuation/functional_curve.py)\n'
    section += '[전체 계산 결과](' + base + 'research/continuation/results/functional_curve_20261020_report.json)\n'
    section += '[원본 복원 catalog](' + base + 'research/continuation/results/functional_curve_20261020_input_sources.json.gz)\n\n'
    section += '새 코드·문서·보고서는 위 연구 저장소에 보존하며 이 묶음에는 새 원시 평가 장부만 추가했습니다. ledgers/functional_curve/의 forecasts는 실제 가격 평가 전에 저장한 예측·시각이고, forecast_curves.npz는288점 예측 곡선, daily_and_trades는 비용·손익·미상 결과 장부입니다.\n'
    old['README.md'] = old['README.md'].rstrip() + section.encode() + b'\n'
    for name in ('forecasts.csv.gz', 'forecast_curves.npz', 'forecast_checkpoint.json', 'daily_and_trades.csv.gz'):
        key = 'ledgers/functional_curve/' + name
        if key in old:
            raise ValueError('Refuse to overwrite existing functional-curve evidence')
        old[key] = (results / name).read_bytes()
    new_manifest = dict(revision=manifest['revision'] + 1, latest_research_commit=commit,
                        payload_files={n: dict(sha256=sha(b), bytes=len(b)) for n, b in sorted(old.items())},
                        production_replaced=False, eligible_replacement_candidates=[])
    staged = bundle.with_suffix('.staged.zip')
    with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(old.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
        z.writestr('evidence_manifest.json', json.dumps(new_manifest, indent=2))
    with zipfile.ZipFile(staged) as z:
        assert z.testzip() is None
        for name, info in new_manifest['payload_files'].items():
            assert sha(z.read(name)) == info['sha256']
    backup = bundle.with_name(bundle.stem + '_before_functional_curve.zip')
    if not backup.exists():
        shutil.copy2(bundle, backup)
    staged.replace(bundle)
    print(json.dumps(dict(path=str(bundle), bytes=bundle.stat().st_size, sha256=sha(bundle.read_bytes()), revision=new_manifest['revision'],
                          payload_files=len(new_manifest['payload_files']), prior_contents_preserved=True)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--results', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    main(args.bundle, args.results, args.commit)

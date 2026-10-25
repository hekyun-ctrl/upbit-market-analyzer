"""Render all seven P0 model cards; never select only favorable outcomes."""
import hashlib
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'research/zerobase/trade_evidence_p0_20261025'


def kst(value):
    return datetime.fromtimestamp(value,timezone(timedelta(hours=9))).isoformat() if value is not None else '미기록'


def display(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=9))).isoformat() if value else '미기록'


def main():
    cards=json.loads((DATA/'trade_cards.json').read_text())
    lines=['# 거래별 근거·결과 카드 — P0 진단', '',
        '아래 7개는 0/15초 이용 지연 가정의 **정책 재생 카드**다. 실제 발송 7건이나 계정 거래 7건이 아니다. 동일 원자료의 두 시나리오를 독립 거래 표본으로 합산하지 않는다.', '',
        '확인 시각은 정책 재생에서 조건이 통과된 보존 관측 시각이다. 실제 구조 수신·발송 요청·Telegram 수락 시각은 없으며 채워 넣지 않았다. 목표와 비용·손익은 모형이다. 방향·목표의 정확도 개선은 아직 미입증이다.', '']
    for i,c in enumerate(sorted(cards,key=lambda c:(c['nominal_delay_seconds'],c['provenance']['confirm_at'],c['policy'])),1):
        p,pr=c['plan'],c['provenance'];bar=pr['nearest_barrier'];out=c['outcome'];ctx=p.get('sampled_context') or {}
        lines += [f"## 카드 {i}: {c['policy']} / 지연 가정 {c['nominal_delay_seconds']}초 / {'롱' if p['direction']==1 else '숏'}", '',
            f"- plan_id: `{c['plan_id']}`",f"- 공통 사건 ID: `{c['event_id']}`",f"- 원자료 창: `{c['window']}`", '',
            '| 항목 | 당시 정책 재생에서 사용한 내용 |','|---|---|',
            f"| 최초 구조 사용 가능 시각 | 실제 수신 근거 없음; anchor 원문은 JSON에 보존 |",
            f"| 후보 / 확인 | {kst(pr['candidate_at'])} / {kst(pr['confirm_at'])} |",
            '| 발송 요청 / Telegram 수락 | 미실행 / 미실행 |',
            f"| 관측 진입 / 고정 앵커 | {p['price']:,.4f} / {p.get('structure_anchor') if p.get('structure_anchor') is not None else p.get('level')} |",
            f"| 방향 근거 | 전략 `{p['strategy']}`; 완료 5분 RSI {p.get('rsi5')}; 문맥 5분 변화 {ctx.get('trend5_pct')}%, 15분 변화 {ctx.get('trend15_pct')}% |",
            f"| 손절 / T1 / T2 | {p['stop']:,.4f} / {p['target1']:,.4f} / {p['target2']:,.4f} |",
            '| 목표 근거 | 비용 후 요구 RR를 역산한 수치; 장벽에서 유도한 구조 목표가 아님 |',
            f"| 첫 관측 장벽 | {format(bar['price'],',.4f') if bar['price'] is not None else '미확인 — 장벽 없음과 다름'} |",
            f"| 장벽 확정 시각 | 실제 수신 시각 {kst(bar.get('known_at'))}; 명목 시나리오 {kst(bar.get('nominal_known_at'))} |",
            f"| 장벽/목표 예외 | `{json.dumps((p.get('entry_decision_priority') or {}).get('overrides',[]),ensure_ascii=False)}` |",
            f"| 비용 후 계획 손실 / T1 순익 | {p['net_loss']:.6f} / {p['net_gain1']:.6f} USDT; 계획 RR {p['rr1']:.4f} |",
            f"| TP1-before-SL | {c['first_contact']['status']}; {c['first_contact'].get('reason','')}; 시각 {display(c['first_contact'].get('at'))}; TP1 선행 {c['first_contact'].get('tp1_before_sl')} — 5분봉 해상도 |",
            f"| 최대 6시간 고정 청산 결과 | 상태 {out['status']}; {out.get('reason','')}; 순손익 {out.get('net_usdt_low','미확정')} USDT; 종료/누락 시각 {display(out.get('latest_exit_bar_end') or out.get('at'))} |", '',
            '### 5/15/30/60분 관측 가격 평가', '',
            '청산 규칙을 적용한 실현 손익이 아니라 해당 시점까지 보유했다고 가정한 가격·비용 모형이다. 관측이 없으면 미확정이다.', '',
            '| 기간 | 관측 / 상태 | 비용 후 가격 평가 |','|---|---|---|']
        for minutes,value in c['horizons'].items():
            lines.append(f"| {minutes}분 | {display(value['observed_at']) if value.get('observed_at') else value.get('reason')} | {value.get('net_pnl_model','미확정')} |")
        lines += ['', '원본 수신·실제 호가 체결·개인 수수료·펀딩은 미검증이다. 진입 봉 안의 순서·누락은 미확정으로 보존한다. MFE/MAE와 실제 단말 수신 지연은 이 자료로 확정하지 않았다.', '']
    (DATA/'TRADE_CARDS.md').write_text('\n'.join(lines)+'\n')
    manifest=dict(renderer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256((DATA/'trade_cards.json').read_bytes()).hexdigest(),
        output_sha256=hashlib.sha256((DATA/'TRADE_CARDS.md').read_bytes()).hexdigest())
    (DATA/'render_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':main()

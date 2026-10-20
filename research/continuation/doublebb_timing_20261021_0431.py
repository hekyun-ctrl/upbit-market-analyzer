"""Frozen paired timing intervention; development transfer, not original trader replica."""
import inspect,json,sys
from pathlib import Path
import lbank_public_techniques as pt
import baseline_replay as replay
Original=pt.TechniqueStudy
src=inspect.getsource(Original.step)
assert src.count("if not confirmation(state,e,now)")==1
src=src.replace("if not confirmation(state,e,now)","if e['strategy'] not in ('double_bb_5m','double_bb_1h') and not confirmation(state,e,now)")
needle="                if row is None:\n"
assert src.count(needle)==1
insert="""                if row is None and e['strategy'] in ('double_bb_5m','double_bb_1h'):
                    detail=dict(event=e,baseline_reason=baseline_reason,timing_transfer=True)
                    with self.book.db:self.book.db.execute('INSERT INTO technique_candidates VALUES(?,?,?,?,?)',(e['id'],now-60,price,'watch',json.dumps(detail)))
                    row=self.book.db.execute('SELECT * FROM technique_candidates WHERE id=?',(e['id'],)).fetchone()
"""
src=src.replace(needle,insert+needle)
import textwrap
ns=dict(vars(pt));exec(textwrap.dedent(src),ns)
class Timing(Original):pass
Timing.step=ns['step']
original_args=sys.argv[:]
reports={}
for name,cls in [('baseline',Original),('bb_available_same_gates',Timing)]:
 replay.TechniqueStudy=cls
 sys.argv=[original_args[0],'--data','data','--output',name+'.json']
 replay.main()
 reports[name]=json.loads(Path(name+'.json').read_text())
assert len(reports['baseline']['unique_raw_events'])==108
assert reports['baseline']['summaries']['box_retest_5m']['levels']['opened']==2
for name,r in reports.items():
 assert r['summaries']['grouped_decline_5m']==reports['baseline']['summaries']['grouped_decline_5m']
 assert r['summaries']['box_retest_5m']==reports['baseline']['summaries']['box_retest_5m']
out=dict(protocol='doubleBB_availability_timing_only_v1',original_trader_replica=False,development_only=True,promotion=False,operations_changed=False,
 intervention='For doubleBB only bypass extra closed1m reaction and watch delay. All other original gates, source closed-bar setups, overlap, stops, targets, management and costs unchanged.',
 source_video='https://www.youtube.com/watch?v=eGaaM0Pz2Lk',
 source_limit='Current page title only; earlier review settings attestation. New transcript unavailable: provider insufficient credits. Exact original stop/exit unknown.',
 uncertainty='Few existing BB episodes; no valid native urgency/manual fill evidence; no independent OOS or positive lower bound asserted.',
 reports=reports)
Path('paired_result.json').write_text(json.dumps(out,separators=(',',':'))+'\n')
print('PAIRED_RESULT_BEGIN');print(Path('paired_result.json').read_text());print('PAIRED_RESULT_END')

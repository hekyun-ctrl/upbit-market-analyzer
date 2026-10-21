import json,pathlib,hashlib,collections
p=pathlib.Path(__file__).parent;r=json.loads((p/'raw.json').read_text());events={};reasons=collections.Counter();kinds=collections.Counter()
for row in r['notification']['deploy']:
 m=row['message'];kind=m.split('kinds=')[1].split()[0];kinds[kind]+=1
for row in r['study']['deploy']:
 m=row['message']
 if 'STUDY {' not in m:continue
 d=json.loads(m[m.index('{'):])
 for a in d.get('decisions',[]):
  e=events.setdefault(a['id'],{'first':row['timestamp'],'last':row['timestamp'],'reasons':collections.Counter(),'outcomes':[]});e['last']=row['timestamp'];e['reasons'][a['reason']]+=1;reasons[a['reason']]+=1
 for a in d.get('outcomes',[]):raise AssertionError('New outcome requires explicit reconciliation')
report={'version':'bounded_native_episode_reconciliation_v1','deployment':'4bbda6c7-29e8-4811-8322-5f8d0574c1be','source':'25388b431c9c9d38df6a434670586c65f382d604','raw_SHA256':hashlib.sha256((p/'raw.json').read_bytes()).hexdigest(),'notification_window_UTC':['2026-10-20T23:10:46Z','2026-10-21T04:49:00Z'],'notification_rows':len(r['notification']['deploy']),'notification_kind_counts':dict(kinds),'study_window_UTC':['2026-10-21T04:30:00Z','2026-10-21T04:49:00Z'],'study_rows':len(r['study']['deploy']),'distinct_decision_candidates':len(events),'events':events,'decision_occurrences_not_trades':dict(reasons),'new_open_or_closed_observed':0,'all_period_coverage_proven':False,'native_orderbook_verified':False,'net_expectancy':None,'double_cost':None,'TP1_first':None,'five_minute_adverse':None,'promotion':False,'limits':['Literal filters and bounded windows only; missing logs are not proof of no activity.','23progress API acknowledgments are zero trades, device receipt unknown.','severity error wraps INFO logger on stderr, not evidence of23application failures.','No entry/exit prices for new BB decision candidate: cannot synthesize returns from later prices.','hourly_ready=false in inspected public study rows; ready=true only other readiness.']}
(p/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

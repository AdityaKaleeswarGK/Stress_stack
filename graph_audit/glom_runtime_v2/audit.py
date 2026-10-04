"""Post-run evidence inventory. Requires the entire batch to have finished."""
import json
from pathlib import Path
P=Path(__file__).resolve().parent

def main():
 summary=json.loads((P/'summary.json').read_text())
 catalog=json.loads((P.parent/'initial_filter/corrected.json').read_text())
 assert summary['candidate_count']==len(catalog['candidates'])==21
 by_sha={c['sha']:c for c in catalog['candidates']};rows=[]
 for review in summary['results']:
  sha=review['candidate'];case=by_sha[sha];experiments=[]
  for file in sorted((P/'experiments'/sha).glob('*/summary.json')):
   data=json.loads(file.read_text());request=json.loads((file.parent/'request/request.json').read_text())
   states={s:{'status':r['status'],'exit_code':r.get('exit_code'),'counts':{v:sum(t['status']==v for t in r.get('tests',{}).values()) for v in ('pass','fail','error','skip')}} for s,r in data['runs'].items()}
   failure_messages={s:{k:v['message'][:700] for k,v in r.get('tests',{}).items() if v['status'] in ['fail','error']} for s,r in data['runs'].items()}
   experiments.append({'id':data['evidence_id'],'purpose':data['purpose'],'repeat_stable':data['repeat_stable'],'comparison':data['comparison'],'states':states,'existing_tests':request['existing_tests'],'probe_path':str(file.parent/'request/request.json'),'nonpassing_messages':failure_messages})
  rows.append({'sha':sha,'pr_number':case.get('pr_number'),'subject':case['subject'],'review_status':review['status'],'agent_decision':(review.get('assessment') or {}).get('decision'),'agent_problem_statement':(review.get('assessment') or {}).get('problem_statement'),'experiments':experiments,'reason':review.get('reason')})
 (P/'audit_measurements.json').write_text(json.dumps(rows,indent=2)+'\n')
 for row in rows:
  print(row['sha'][:8],row['review_status'],row['agent_decision'],[(x['id'],len(x['comparison']['fail_to_pass']),len(x['comparison']['pass_to_fail']),x['repeat_stable'],{s:r['status'] for s,r in x['states'].items()}) for x in row['experiments']])
if __name__=='__main__':main()

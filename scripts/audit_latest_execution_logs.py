import asyncio, io, json, zipfile
from collections import Counter
from sqlalchemy import select
from infra.db.session import AsyncSessionLocal
from infra.db.models.execution import TestRun, TestResult
from infra.object_storage.s3_client import S3Client

async def main():
    from types import SimpleNamespace
    storage = S3Client()
    grouped = {}; latest = {}
    for page in storage._client.get_paginator('list_objects_v2').paginate(Bucket=storage.bucket, Prefix='executions/18e86b82-b95a-43db-8760-7a404021106e/'):
        for item in page.get('Contents', []):
            parts = item['Key'].split('/')
            run_id, result_id, channel = parts[2:5]
            grouped.setdefault(run_id, {}).setdefault(result_id, {})[channel] = item['Key']
            latest[run_id] = max(latest.get(run_id, item['LastModified']), item['LastModified'])
    if not latest:
        print('No saved execution evidence found'); return
    latest_id = max(latest, key=latest.get)
    run = SimpleNamespace(id=latest_id, status='not checked: database unavailable')
    rows = [SimpleNamespace(id=result_id, evidence=evidence, spec_path='', status='unknown') for result_id, evidence in grouped[latest_id].items()]
    print('LATEST_SAVED_RUN', latest_id, str(latest[latest_id]))
    storage=S3Client(); totals=Counter(); issues=[]; samples=[]
    for row in rows:
        try:
            e=row.evidence or {}
            if not all(e.get(k) for k in ('trace','console_log','network_log')):
                totals['missing_evidence']+=1; continue
            trace,console,network=await asyncio.gather(*(storage.get(e[k]) for k in ('trace','console_log','network_log')))
            c,n=json.loads(console),json.loads(network)
            expected_c=[];expected_n=[]
            with zipfile.ZipFile(io.BytesIO(trace)) as archive:
                for name in archive.namelist():
                    if not name.endswith(('.trace','.network')): continue
                    for line in archive.read(name).splitlines():
                        try: event=json.loads(line)
                        except ValueError: continue
                        if not isinstance(event,dict): continue
                        method=str(event.get('method') or event.get('type') or '').lower()
                        params=event.get('params') if isinstance(event.get('params'),dict) else {}
                        if 'console' in method: expected_c.append(params or event)
                        if method in {'request','response','requestfinished','requestfailed','resource-snapshot'} or 'network' in method: expected_n.append(params or event.get('snapshot') or event)
            totals['checked']+=1;totals['console_entries']+=len(c);totals['network_entries']+=len(n)
            totals['empty_console']+=not bool(c); totals['empty_network']+=not bool(n)
            if c!=expected_c or n!=expected_n:
                issues.append({'result':str(row.id),'spec':row.spec_path,'saved_console':len(c),'trace_console':len(expected_c),'saved_network':len(n),'trace_network':len(expected_n)})
            if len(samples)<3:
                samples.append({'result':str(row.id),'spec':row.spec_path,'status':row.status,'console_count':len(c),'network_count':len(n),'console_fields':list(c[0]) if c else [],'request_sample': [{'method':x.get('request',{}).get('method'),'url':x.get('request',{}).get('url'),'status':x.get('response',{}).get('status')} for x in n[:2]]})
        except Exception as exc:
            issues.append({'result':str(row.id),'error':type(exc).__name__+': '+str(exc)[:180]})
    print('AUDIT',json.dumps({'run_id':str(run.id),'status':run.status,'results':len(rows),'totals':dict(totals),'issues':issues,'samples':samples}))
asyncio.run(main())

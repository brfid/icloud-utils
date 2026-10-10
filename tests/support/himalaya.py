#!/usr/bin/env python3
import json, os, pathlib, re, subprocess, sys, time
scenario=json.loads(pathlib.Path(os.environ['MAIL_TEST_SCENARIO']).read_text())
version=scenario.get('version','2.2.1')
args=sys.argv[1:]
if args==['--version']:
    print('himalaya v'+version)
    sys.exit(0)
with open(os.environ['MAIL_TEST_CALLS'],'a') as f:
    f.write(json.dumps(args)+'\n')
prefix=['--json','--log-level','off','--config',os.environ['MAIL_TEST_CONFIG'],
        '--account',os.environ.get('MAIL_TEST_ACCOUNT','icloud'),'--backend','imap']
assert args[:len(prefix)]==prefix, 'unexpected global arguments'
args=args[len(prefix):]
kind=tuple(args[:2])
uid=lambda value: re.fullmatch(r'[1-9][0-9]*',value) and int(value)<=4294967295
mailbox=lambda value: value.startswith('--mailbox=') and len(value)>10
if kind==('imap','status'):
    assert len(args)==4 and args[2]=='--' and args[3], 'unexpected status arguments'
elif kind in (('account','check'),('mailbox','list')):
    assert len(args)==2, 'unexpected account/mailbox arguments'
elif kind in (('envelope','list'),('envelope','search')):
    assert len(args)==(7 if kind[1]=='list' else 9) and mailbox(args[2]), 'unexpected envelope arguments'
    assert args[3]=='--page' and int(args[4])>0 and args[5]=='--page-size' and 1<=int(args[6])<=100
    if kind[1]=='search': assert args[7]=='--', 'query must follow the delimiter'
elif kind==('message','read'):
    assert len(args)==6 and mailbox(args[2]) and args[3:5]==['--raw','--'] and uid(args[5]), 'unexpected read arguments'
elif kind==('attachment','list'):
    assert len(args)==5 and mailbox(args[2]) and args[3]=='--' and uid(args[4]), 'unexpected attachment arguments'
elif kind==('attachment','download'):
    assert len(args)>=8 and mailbox(args[2]) and args[3]=='--dir' and args[5]=='--'
    assert all(uid(value) for value in args[6:]), 'unexpected download arguments'
else:
    raise AssertionError('unexpected command')
if kind==('imap','status'):
    countfile=pathlib.Path(os.environ['MAIL_TEST_CALLS']+'.status')
    count=int(countfile.read_text())+1 if countfile.exists() else 1
    countfile.write_text(str(count))
    epoch=scenario.get('epoch',10)
    if scenario.get('change_epoch') and count%2==0: epoch+=1
    print(json.dumps({'uid_validity':epoch,'messages':1,'unseen':1}))
elif kind==('account','check'):
    ok=scenario.get('auth_ok',True)
    print(json.dumps({'account':'icloud','backends':[{'backend':'imap','ok':ok,'error':None if ok else 'private-auth-error'}]}))
elif kind==('mailbox','list'):
    row={'id':'INBOX','name':'INBOX'}
    if version=='2.2.1': row['role']='inbox'
    print(json.dumps({'mailboxes':[row]}))
elif args[0]=='envelope':
    result={'envelopes':[{'id':'7','subject':'Synthetic fixture','flags':[]}]}
    if version=='2.2.1': result['queued']=0
    print(json.dumps(result))
elif kind==('message','read'):
    if scenario.get('fail'):
        print('private-body-and-credential-sentinel')
        print('private-stderr-sentinel',file=sys.stderr)
        sys.exit(7)
    if scenario.get('block'):
        pathlib.Path(os.environ['MAIL_TEST_STARTED']).write_text('started')
        marker=os.environ['MAIL_TEST_ORPHAN']
        code='import time,pathlib; time.sleep(1.5); pathlib.Path('+repr(marker)+').write_text("orphan")'
        subprocess.Popen([sys.executable,'-c',code])
        time.sleep(20)
    if scenario.get('invalid_json'): print('private-not-json')
    else: print(json.dumps({'message':scenario.get('message','Subject: Synthetic\r\n\r\nFixture body.\r\n')}))
elif kind==('attachment','list'):
    print(json.dumps({'attachments':[{'id':'3','size':5,'inline':False,'filename':'part.txt'}]}))
elif kind==('attachment','download'):
    target=pathlib.Path(args[args.index('--dir')+1])
    values=args[args.index('--')+1:]
    rows=[]
    for part in values[1:]:
        path=target/('part-'+part+'.txt')
        path.write_bytes(('part'+part).encode())
        rows.append({'id':part,'size':len(path.read_bytes()),'inline':False,'path':str(path)})
    if scenario.get('partial_download'): rows=[]
    print(json.dumps({'attachments':rows}))
else:
    print('unexpected command',file=sys.stderr)
    sys.exit(3)

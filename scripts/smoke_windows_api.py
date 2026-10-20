"""Exercise installed Windows DPAPI, saved API transport and hardware guards.

The only credential is generated inside this test process. The HTTP provider is
an explicitly local protocol fixture; it does not claim real-provider quality.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import textwrap

CODE = r'''
import json, secrets, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from paperbridge.api_profiles import ProfileManager
from paperbridge.providers import APIClient
from paperbridge.local_runtime import assess_local, GIB

key = secrets.token_hex(24)
counts = {'requests': 0, 'authenticated': 0}
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, value, mime='application/json'):
        body=value.encode('utf-8')
        self.send_response(200); self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def authenticate(self):
        counts['requests'] += 1
        assert self.headers.get('Authorization') == 'Bearer ' + key
        counts['authenticated'] += 1
    def do_GET(self):
        self.authenticate(); assert self.path == '/v1/models'
        self.reply(json.dumps({'data':[{'id':'qa-model'}]}))
    def do_POST(self):
        self.authenticate(); assert self.path == '/v1/chat/completions'
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['model'] == 'qa-model' and body['stream']
        data={'choices':[{'delta':{'content':'科学论文翻译测试通过'},'finish_reason':'stop'}],
              'usage':{'prompt_tokens':21,'completion_tokens':8}}
        self.reply('data: '+json.dumps(data,ensure_ascii=False)+'\n\ndata: [DONE]\n\n', 'text/event-stream')

root=Path(os.environ['PB_API_QA_ROOT'])
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
manager=ProfileManager(root)
row=None
try:
    row=manager.save({'kind':'custom','name':'Local QA fixture','model':'qa-model',
        'base_url':f'http://127.0.0.1:{server.server_port}/v1','api_key':key,
        'json_mode':False,'stream_usage':True})
    assert key not in manager.path.read_text('utf-8')
    encrypted=list((root/'settings/.api-secrets').glob('*.bin'))
    assert len(encrypted)==1 and key.encode() not in encrypted[0].read_bytes()
    restart=ProfileManager(root)
    config, restored=restart.resolve(row['id'])
    assert restored == key
    client=APIClient(restored,config)
    assert client.test_connection()['connected']
    assert client.generate([{'role':'user','content':'Translate a synthetic test sentence.'}],100)=='科学论文翻译测试通过'
    assert client.stats['prompt_tokens']==21 and client.stats['completion_tokens']==8
    assert counts['requests']==counts['authenticated']==2
    hardware={'ram_bytes':16*GIB,'available_ram_bytes':10*GIB,'nvidia_gpus':[]}
    guard=assess_local('precision',windows=True,info=hardware)
    assert not guard['can_start'] and guard['code']=='insufficient_memory'
    assert guard['can_use_4b'] and 'API' in guard['message']
    enough={**hardware,'ram_bytes':32*GIB,'available_ram_bytes':24*GIB}
    assert assess_local('precision',windows=True,info=enough)['status']=='confirm_cpu'
    result={'platform':'Windows x64','native_dpapi_roundtrip':True,
        'key_not_in_metadata':True,'ciphertext_not_plaintext':True,'reopened_profile_passed':True,
        'localhost_api_connection_passed':True,'localhost_stream_generation_passed':True,
        'usage_accounting_passed':True,'insufficient_14b_memory_blocked':True,
        'cpu_14b_requires_confirmation':True,'real_cloud_provider_tested':False,
        'user_credential_used':False,'passed':True}
finally:
    if row: manager.delete(row['id'])
    server.shutdown();server.server_close()
(root/'WINDOWS-API-HARDWARE-REPORT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True)
    args=p.parse_args()
    args.data.mkdir(parents=True,exist_ok=True)
    script=args.data/'api-qa.py'
    script.write_text('def main():\n'+textwrap.indent(CODE.strip(),'    ')+'\n\nif __name__=="__main__":\n    main()\n',encoding='utf-8')
    code='import sys; sys.path.insert(0,sys.argv.pop(1)); import runpy; runpy.run_path(sys.argv[1],run_name="__main__")'
    subprocess.run([str(args.bundle/'Runtime/Python/python.exe'),'-X','utf8','-c',code,
                    str(args.bundle/'Engine'),str(script)],
                   env={**os.environ,'PB_API_QA_ROOT':str(args.data),'PAPERBRIDGE_HOME':str(args.data)},
                   cwd=args.bundle/'Engine',check=True,timeout=120)


if __name__=='__main__':
    main()

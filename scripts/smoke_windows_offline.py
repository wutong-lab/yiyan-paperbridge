"""Run a real PDF through an installed complete edition, with downloads forbidden."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("standard", "precision"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PAPERBRIDGE_HOME=str(args.data),
                       PAPERBRIDGE_WINDOWS_BUNDLE=str(args.bundle),
                       PAPERBRIDGE_LLAMA_DIR=str(args.bundle / "Runtime/llama"),
                       PAPERBRIDGE_CPU_ONLY="1", PB_TEST_PROFILE=args.profile, PYTHONUTF8="1")
    code = r'''
from pathlib import Path
import hashlib, json, os, shutil, time, urllib.request
from paperbridge.paths import CODE_ROOT, ROOT
from paperbridge.gguf_inference import block_external_network
block_external_network()
def forbidden_download(*a, **k): raise AssertionError('Offline edition attempted a download')
urllib.request.urlopen = forbidden_download
shutil.copytree(CODE_ROOT / '.cache/babeldoc', ROOT / '.cache/babeldoc', dirs_exist_ok=True)
from paperbridge.windows_models import installed, model_directory, default_profile, manifest
profile=os.environ['PB_TEST_PROFILE']; model=model_directory(profile); entry=manifest()[profile]
assert installed(model) and model.parent.name=='Models', str(model)
assert default_profile()==profile
h=hashlib.sha256()
with (model/entry['file']).open('rb') as f:
    while chunk:=f.read(4*1024**2): h.update(chunk)
assert h.hexdigest()==entry['sha256']
import pymupdf as fitz
from paperbridge.app import add_document, get_job, save_job, status
initial=status()
assert initial['layout_installed'] and profile in initial['bundled_models'],initial
assert initial['default_profile']==profile
doc=fitz.open(); page=doc.new_page(width=595,height=842)
page.insert_text((50,60),'Scientific reading example',fontsize=15)
page.insert_textbox(fitz.Rect(50,95,540,220),'The supernova light curve provides information about the progenitor. We estimate a posterior distribution using Bayesian inference.',fontsize=12)
page.draw_rect(fitz.Rect(50,280,240,370),color=(0.2,0.5,0.3))
page.insert_text((65,315),'Synthetic figure',fontsize=10)
page.insert_text((50,450),'References\nSmith, A. 2020. Reference-only method.\nJournal of Scientific Research, 10.\nJones, B. 2021. Further measurements.',fontsize=9)
created=add_document(doc.tobytes(),'Windows-offline-test.pdf'); doc.close()
job=get_job(created['id']); job['settings'].update(engine='full',profile=profile,domain='astronomy',glossary='progenitor = 前身星',polish=False,pages='',auto_cleanup=False)
save_job(job)
from paperbridge.full_worker import run
started=time.perf_counter(); run(job['id'])
folder=ROOT/'data/jobs'/job['id']; result=json.loads((folder/'worker-state.json').read_text(encoding='utf-8'))
assert result['status']=='complete',result['message']
with fitz.open(folder/'translated.pdf') as pdf:
    text=''.join(page.get_text() for page in pdf)
    assert len(pdf)==1
    assert any('\u3400' <= c <= '\u9fff' for c in text),text
assert (folder/'bilingual.pdf').is_file()
assert result['export_report']['structural_checks_passed']
from paperbridge.reference_restore import _same_pixels, _same_reference_text_and_positions
reference_regions=result['source_sections']['reference_regions']
assert reference_regions
with fitz.open(folder/'original.pdf') as source, fitz.open(folder/'translated.pdf') as translated:
    for item in reference_regions:
        a,b=source[item['page']-1],translated[item['page']-1];region=fitz.Rect(item['bbox'])
        assert _same_reference_text_and_positions(a,b,region)
        assert _same_pixels(a,b,region)
reference_segments=[s for s in result['segments'] if s.get('reason')=='参考文献保留原文']
assert reference_segments and all(not s.get('translation') and s['status']=='preserved' for s in reference_segments)
assert not list((ROOT/'models').rglob('*.gguf')),'Model was unnecessarily duplicated'
report={'platform':'Windows x64','edition':profile,'engine':'llama.cpp CPU','model':entry['file'],'model_sha256':h.hexdigest(),'model_in_install_directory':True,'model_copied_to_user_directory':False,'downloads_forbidden':True,'elapsed_seconds':round(time.perf_counter()-started,1),'status':result['status'],'has_chinese_pdf':True,'structure_passed':True,'reference_text_and_pixels_preserved':True,'reference_segments_excluded_from_translation':True,'export_report':result['export_report']}
(ROOT/'WINDOWS-OFFLINE-SMOKE-REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
shutil.copy2(folder/'translated.pdf',ROOT/'Windows-offline-translated.pdf')
shutil.copy2(folder/'bilingual.pdf',ROOT/'Windows-offline-bilingual.pdf')
'''.replace("'\\u3400'", "'\u3400'").replace("'\\u9fff'", "'\u9fff'")
    script = args.data / "run.py"
    script.write_text("def main():\n" + textwrap.indent(code.strip(), "    ") + "\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")
    with (args.data / "smoke.log").open("w", encoding="utf-8") as log:
        result = subprocess.run([str(args.bundle / "Runtime/Python/python.exe"), "-X", "utf8", str(script)],
                                env=environment, cwd=args.bundle / "Engine", stdout=log, stderr=log, timeout=2700)
    if result.returncode:
        print((args.data / "smoke.log").read_text(encoding="utf-8")[-16000:])
        for path in (args.data / "tmp").glob("inference-*.log"):
            print(path.read_text(encoding="utf-8", errors="replace")[-8000:])
        raise SystemExit(result.returncode)
    print((args.data / "WINDOWS-OFFLINE-SMOKE-REPORT.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

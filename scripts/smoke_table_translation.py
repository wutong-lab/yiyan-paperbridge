"""Exercise both table settings with the installed model and PDF export engine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def make_fixture(destination):
    import pymupdf as fitz
    destination.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 65), "Scientific reading example", fontsize=16)
        page.insert_textbox(fitz.Rect(50, 100, 545, 190),
            "The supernova light curve provides information about the progenitor. "
            "We estimate a posterior distribution using Bayesian inference.", fontsize=12)
        with fitz.open() as figure:
            chart = figure.new_page(width=200, height=90)
            chart.draw_rect(chart.rect, fill=(.95, .98, .96), color=None)
            chart.draw_polyline([(15, 70), (70, 25), (130, 35), (180, 65)], width=2, color=(.2, .5, .3))
            chart.insert_text((15, 15), "Synthetic light curve", fontsize=9)
            image = chart.get_pixmap().tobytes("png")
        page.insert_image(fitz.Rect(50, 210, 250, 300), stream=image)
        page.insert_text((50, 335), "Table 1: Summary of the observations.", fontsize=11)
        for x in [50, 270, 535]: page.draw_line((x, 360), (x, 480), width=.6)
        for y in [360, 400, 440, 480]: page.draw_line((50, y), (535, y), width=.6)
        for y, values in [(385, ("Observation group", "Observation count")),
                          (425, ("Training samples", "42")), (465, ("Validation samples", "120"))]:
            for x, text in zip([60, 280], values): page.insert_text((x, y), text, fontsize=11)
        page.insert_text((50, 515), "Note: The counts refer to independent observations.", fontsize=10)
        page.insert_textbox(fitz.Rect(50, 570, 545, 650),
            "The light curve provides information about the physical model. "
            "These observations support the scientific conclusions.", fontsize=12)
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 65), "Scientific conclusions", fontsize=15)
        page.insert_textbox(fitz.Rect(50, 100, 545, 190),
            "We report a posterior distribution for the supernova model. "
            "The observations provide evidence for the scientific interpretation.", fontsize=12)
        page.insert_text((50, 330), "References\nSmith, A. 2020. Reference-only measurements.\n"
                         "Journal of Scientific Research, 10.\nJones, B. 2021. A scientific method.", fontsize=9)
        doc.set_metadata({"title": "Synthetic two-page table validation", "author": "PaperBridge QA"})
        doc.save(destination, deflate=True)


def worker(args):
    from paperbridge.gguf_inference import block_external_network
    block_external_network()
    import pymupdf as fitz
    from paperbridge.paths import ROOT, WINDOWS
    from paperbridge.app import add_document, get_job, save_job, Settings
    from paperbridge.full_worker import run
    from paperbridge.reference_restore import _same_pixels, _same_reference_text_and_positions
    from paperbridge.table_policy import TABLE_PRESERVED
    if os.environ.get('PB_TABLE_DIAGNOSTIC') == '1':
        from paperbridge.translator import LocalTranslator
        record_failure = LocalTranslator._record_failure
        def diagnostic_failure(self, messages, result, reason, max_tokens):
            print(json.dumps({'synthetic_fixture_failure':True, 'reason':reason,
                              'raw_response':result, 'source_message':messages[-1]['content']}, ensure_ascii=True), flush=True)
            return record_failure(self, messages, result, reason, max_tokens)
        LocalTranslator._record_failure = diagnostic_failure
    version = os.environ['PB_TABLE_VERSION']
    created = add_document(args.fixture.read_bytes(), 'Synthetic-table-validation.pdf')
    job = get_job(created['id'])
    native = [{"page": p['number'], "bbox": b} for p in job['pages'] for b in p.get('table_regions', [])]
    assert len(native) == 1, native
    defaults = Settings().model_dump(); assert defaults['translate_tables'] is False
    defaults.update(engine='full', provider='local', profile='standard', domain='astronomy',
                    glossary='progenitor = 前身星', polish=False, auto_cleanup=False,
                    force_cpu=WINDOWS, translate_tables=args.case == 'on')
    job['settings'] = defaults; save_job(job)
    started = time.monotonic(); run(job['id'])
    folder = ROOT / 'data/jobs' / job['id']
    state = json.loads((folder / 'worker-state.json').read_text(encoding='utf-8'))
    output = args.evidence / args.case; output.mkdir(parents=True, exist_ok=True)
    # Retain evidence before assertions so a failed validation can be diagnosed.
    for name in ['original.pdf', 'translated.pdf', 'bilingual.pdf', 'worker-state.json']:
        if (folder/name).exists(): shutil.copy2(folder/name, output/name)
    if (folder/'translation-failures').exists():
        shutil.copytree(folder/'translation-failures',output/'synthetic-failures',dirs_exist_ok=True)
    assert state['status'] == 'complete', state.get('message')
    assert state['export_report']['structural_checks_passed'], state['export_report']
    table_segments = [s for s in state['segments'] if s.get('table_content')]
    assert table_segments and state['export_report']['translate_tables'] == (args.case == 'on')
    with fitz.open(folder/'original.pdf') as original, fitz.open(folder/'translated.pdf') as target:
        assert len(original) == len(target) == 2
        assert all(tuple(a.rect) == tuple(b.rect) for a, b in zip(original, target))
        for page in target:
            assert any('\u3400' <= c <= '\u9fff' for c in page.get_text())
        region = fitz.Rect(native[0]['bbox'])
        if args.case == 'off':
            assert all(s['status'] == 'preserved' and not s.get('translation') for s in table_segments)
            assert any(s['reason'] == TABLE_PRESERVED for s in table_segments)
            assert _same_reference_text_and_positions(original[0], target[0], region)
            assert _same_pixels(original[0], target[0], region), 'Disabled table pixels changed'
        else:
            assert any(s.get('translation') and s.get('exported') for s in table_segments)
            assert any('\u3400' <= c <= '\u9fff' for c in target[0].get_text(clip=region))
            assert {'42', '120'} <= set(target[0].get_text(clip=region).split())
        references = state['source_sections']['reference_regions']; assert references
        for item in references:
            n, box = item['page']-1, fitz.Rect(item['bbox'])
            assert _same_reference_text_and_positions(original[n], target[n], box)
            assert _same_pixels(original[n], target[n], box)
        figure = fitz.Rect(50, 210, 250, 300)
        assert _same_pixels(original[0], target[0], figure)
    caption = [s for s in state['segments'] if 'Summary of the observations' in s['source']]
    notes = [s for s in state['segments'] if 'counts refer to independent observations' in s['source']]
    assert caption and all(s.get('translation') and s.get('exported') for s in caption), caption
    assert notes and all(s.get('translation') and s.get('exported') for s in notes), notes
    with fitz.open(folder/'bilingual.pdf') as bilingual: assert len(bilingual) == 4
    report = {'version': version, 'platform': sys.platform, 'case': args.case,
        'translate_tables': args.case == 'on', 'profile': 'standard', 'real_4b_inference': True,
        'mock_translation': False, 'external_network_blocked': True,
        'source_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        'page_count': 2, 'bilingual_page_count': 4, 'status': state['status'],
        'elapsed_seconds': round(time.monotonic()-started, 1),
        'table_text_and_pixels_preserved': args.case == 'off',
        'table_chinese_exported': args.case == 'on', 'table_numbers_preserved': True,
        'caption_and_notes_translated': True, 'figure_pixels_preserved': True,
        'reference_text_and_pixels_preserved': True, 'structure_passed': True,
        'failed_segments': sum(s['status'] == 'failed' for s in state['segments']),
        'table_paragraphs': len(table_segments), 'passed': True}
    assert report['failed_segments'] == 0, report
    (output/'REPORT.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app', type=Path); p.add_argument('--bundle', type=Path)
    p.add_argument('--engine', type=Path, help='Explicit development-only source override')
    p.add_argument('--diagnostic', action='store_true')
    p.add_argument('--data', type=Path, required=True); p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--fixture', type=Path); p.add_argument('--case', choices=['off','on'])
    p.add_argument('--version', required=True)
    args = p.parse_args(); args.evidence.mkdir(parents=True, exist_ok=True)
    if args.case: return worker(args)
    assert bool(args.app) != bool(args.bundle)
    args.data.mkdir(parents=True, exist_ok=True)
    if args.app:
        resources = args.app.resolve()/'Contents/Resources'
        python = resources/'Python/bin/python3.12'; engine = resources/'Engine'
        assets = resources/'LayoutAssets'
        import plistlib
        native_version = plistlib.loads((args.app/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
    else:
        resources = args.bundle.resolve(); python = resources/'Runtime/Python/python.exe'
        engine = resources/'Engine'; assets = engine/'.cache/babeldoc'
        native_version = json.loads((resources/'BUILD-REPORT.json').read_text(encoding='utf-8-sig'))['version']
    if not args.engine: assert native_version == args.version, (native_version, args.version)
    if args.engine: engine = args.engine.resolve()
    cache = args.data/'.cache/babeldoc'
    if not (cache/'ready.json').is_file(): shutil.copytree(assets, cache, dirs_exist_ok=True)
    fixture = args.evidence.resolve()/'synthetic-tables.pdf'; make_fixture(fixture)
    environment = {**os.environ, 'PAPERBRIDGE_HOME': str(args.data.resolve()),
        'PAPERBRIDGE_APP_VERSION': args.version, 'PB_TABLE_VERSION': args.version,
        'PB_TABLE_SOURCE_OVERRIDE': '1' if args.engine else '0',
        'PB_TABLE_DIAGNOSTIC': '1' if args.diagnostic else '0',
        'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    if args.bundle:
        environment.update(PAPERBRIDGE_WINDOWS_BUNDLE=str(resources),
            PAPERBRIDGE_LLAMA_DIR=str(resources/'Runtime/llama'), PAPERBRIDGE_CPU_ONLY='1')
    bridge = 'import sys;sys.path.insert(0,sys.argv.pop(1));target=sys.argv.pop(1);sys.argv[0]=target;import runpy;runpy.run_path(target,run_name="__main__")'
    failed_cases = []
    for case in ['off','on']:
        command = [str(python), '-X', 'utf8', '-c', bridge, str(engine), str(Path(__file__).resolve()),
                   '--data', str(args.data.resolve()), '--evidence', str(args.evidence.resolve()),
                   '--fixture', str(fixture), '--case', case, '--version', args.version]
        with (args.evidence/f'{case}.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=engine, env=environment, stdout=log, stderr=log, timeout=2700)
        if result.returncode:
            print((args.evidence/f'{case}.log').read_text(encoding='utf-8', errors='replace')[-10000:])
            if not args.diagnostic: raise SystemExit(result.returncode)
            failed_cases.append(case)
    if failed_cases:
        (args.evidence/'DIAGNOSTIC-SUMMARY.json').write_text(json.dumps({'passed':False,'failed_cases':failed_cases}))
        raise SystemExit(1)
    cases = [json.loads((args.evidence/case/'REPORT.json').read_text(encoding='utf-8')) for case in ['off','on']]
    report = {'version': args.version, 'platform': sys.platform, 'default_translate_tables': False,
              'actual_installed_runtime': True, 'real_4b_inference': True, 'cases': cases,
              'native_version': native_version,
              'development_source_override': bool(args.engine),
              'user_credential_used': False, 'passed': all(c['passed'] for c in cases)}
    (args.evidence/'TABLE-TRANSLATION-REPORT.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__': main()

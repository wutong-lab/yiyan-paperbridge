"""Bibliography boundaries must follow layout, not PDF text-block grouping."""
import pytest
import pymupdf as fitz

from paperbridge.document_context import read_source_profile
from paperbridge.pdf_engine import analyze
from paperbridge.sections import detect_sections, is_reference_region


def _text_blocks(page):
    return [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]


def _line_box(page, prefix):
    return next(line["bbox"] for block in _text_blocks(page) for line in block["lines"]
                if "".join(s["text"] for s in line["spans"]).startswith(prefix))


@pytest.mark.parametrize("heading", [
    "References", "REFERENCES:", "8. References", "VIII. Bibliography", "Literature cited",
])
def test_merged_heading_and_entries_protect_both_columns_and_next_page(tmp_path, heading):
    path = tmp_path / "merged-bibliography.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 65), "Acknowledgments\nThis work received scientific support.\n"
                         "We thank colleagues for their help.", fontsize=9)
        page.insert_text((110, 160), heading, fontsize=10)
        page.insert_text((40, 172), "Smith, A.; and Brown, B. 2020.\n"
                         "A study of scientific measurements.\nJournal of Science, 12.", fontsize=9)
        page.insert_text((320, 65), "Jones, B.; and Wu, C.\n2021. A second scientific method.\n"
                         "Journal of Scientific Research, 9.\nLi, X. 2022. Testing scientific results.", fontsize=9)
        assert any(heading in " ".join("".join(s['text'] for s in l['spans'])
                       for l in b['lines']) and len(b['lines']) > 1 for b in _text_blocks(page))
        ack = _line_box(page, "This work")
        left = _line_box(page, "Smith,")
        right = _line_box(page, "Jones,")
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 65), "Research, 7. Continuation of the previous citation.\n"
                         "Garcia, J. 2023. Another scientific study.", fontsize=9)
        continuation = _line_box(page, "Research,")
        doc.save(path)
    sections = detect_sections(path)
    regions = sections['reference_regions']
    assert not is_reference_region(1, ack, regions)
    assert is_reference_region(1, left, regions)
    assert is_reference_region(1, right, regions)
    assert is_reference_region(2, continuation, regions)
    assert sections['body_pages'] == [1]


def test_body_and_bibliography_in_one_block_keep_body_in_context(tmp_path):
    path = tmp_path / "body-and-bibliography.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 80), "Our scientific conclusion remains translatable.\nReferences\n"
                         "Smith, A. 2020. Reference-only terminology.\n"
                         "Jones, B. 2021. Reference-only terminology.\n"
                         "Brown, C. 2022. Reference-only terminology.\n"
                         "Garcia, D. 2023. Reference-only terminology.", fontsize=10)
        assert len(_text_blocks(page)) == 1
        body = _line_box(page, "Our scientific")
        reference = _line_box(page, "Smith,")
        doc.save(path)
    sections = detect_sections(path)
    assert not is_reference_region(1, body, sections['reference_regions'])
    assert is_reference_region(1, reference, sections['reference_regions'])
    assert sections['body_pages'] == [1]
    profile, _ = read_source_profile(path, sections)
    assert any('Our scientific conclusion' in s['source'] for s in profile)
    assert all('Reference-only terminology' not in s['source'] for s in profile)
    segments = analyze(path)['segments']
    assert next(s for s in segments if 'Our scientific conclusion' in s['source'])['status'] == 'pending'
    assert all(s['status'] == 'preserved' for s in segments if 'Reference-only' in s['source'])


def test_right_column_body_before_merged_bibliography_remains_prose(tmp_path):
    path = tmp_path / 'column-continuation.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 65), 'These results establish our method.\n'
                         'We report scientific measurements here.\n'
                         'This conclusion requires translation.', fontsize=9)
        page.insert_text((40, 200), 'References\nSmith, A. 2020. A scientific method.\n'
                         'Journal of Scientific Research, 10.', fontsize=9)
        page.insert_text((320, 65), 'This paragraph still explains the results.\n'
                         'Its scientific conclusions need translation.\n'
                         'Jones, B.; and Wu, C.\n2021. Further scientific measurements.\n'
                         'Journal of Scientific Research, 9.', fontsize=9)
        body, entry = _line_box(page, 'This paragraph'), _line_box(page, 'Jones,')
        doc.save(path)
    sections = detect_sections(path)
    assert len(sections['page_columns'][0]['columns']) == 2
    assert not is_reference_region(1, body, sections['reference_regions'])
    assert is_reference_region(1, entry, sections['reference_regions'])


def test_full_width_reference_heading_preserves_body_above_in_both_columns(tmp_path):
    path = tmp_path / 'spanning-reference-heading.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        for x in (40, 320):
            page.insert_text((x, 65), 'We describe the scientific results.\n'
                             'The method is tested on observations.\n'
                             'These conclusions need translation.', fontsize=9)
            page.insert_text((x, 230), 'Smith, A. 2020. A scientific method.\n'
                             'Journal of Scientific Research, 10.\n'
                             'Jones, B. 2021. Further measurements.', fontsize=9)
        page.insert_text((270, 200), 'References', fontsize=12)
        left, right = [40, 220, 200, 232], [320, 220, 500, 232]
        doc.save(path)
    sections = detect_sections(path)
    assert len(sections['page_columns'][0]['columns']) == 2
    assert all(is_reference_region(1, box, sections['reference_regions']) for box in (left, right))
    assert not is_reference_region(1, [320, 55, 500, 95], sections['reference_regions'])


def test_merged_appendix_heading_ends_reference_protection(tmp_path):
    path = tmp_path / 'merged-appendix.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 70), 'References\nSmith, A. 2020. A scientific method.', fontsize=10)
        page.insert_text((40, 98), 'Appendix A: Further measurements', fontsize=11, fontname='hebo')
        page.insert_text((40, 112), 'These additional scientific results need translation.', fontsize=10)
        assert any(len(b['lines']) >= 3 for b in _text_blocks(page))
        entry, appendix = _line_box(page, 'Smith,'), _line_box(page, 'These additional')
        doc.save(path)
    sections = detect_sections(path)
    assert is_reference_region(1, entry, sections['reference_regions'])
    assert not is_reference_region(1, appendix, sections['reference_regions'])
    assert 1 in sections['body_pages']


def test_spanning_appendix_heading_keeps_references_above_both_columns(tmp_path):
    path = tmp_path / 'spanning-appendix.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 70), 'References', fontsize=11)
        page = doc.new_page(width=600, height=800)
        for x in (40, 320):
            page.insert_text((x, 65), 'Smith, A. 2020. A scientific method.\n'
                             'Journal of Scientific Research, 10.\n'
                             'Jones, B. 2021. Further measurements.', fontsize=9)
            page.insert_text((x, 250), 'These additional results need translation.\n'
                             'We report further measurements here.\n'
                             'The appendix explains the method.', fontsize=9)
        page.insert_text((250, 200), 'Appendix A: Tests', fontsize=12)
        doc.save(path)
    sections = detect_sections(path)
    for box in ([40, 55, 200, 95], [320, 55, 500, 95]):
        assert is_reference_region(2, box, sections['reference_regions'])
    for box in ([40, 240, 200, 275], [320, 240, 500, 275]):
        assert not is_reference_region(2, box, sections['reference_regions'])


@pytest.mark.parametrize('text', [
    'Source\nReferences',
    'The following references\nto earlier studies explain our method.',
    'References\nto earlier scientific studies are discussed here.',
    'Appendix A discusses the results.\nThis scientific paragraph needs translation.',
])
def test_body_mentions_do_not_create_section_boundaries(tmp_path, text):
    path = tmp_path / 'ordinary-prose.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 70), text, fontsize=10)
        page.insert_text((40, 180), 'These scientific results must remain translatable.', fontsize=10)
        doc.save(path)
    assert detect_sections(path)['section_headings'] == []


def test_appendix_in_a_cited_article_title_is_not_a_section(tmp_path):
    path = tmp_path / 'article-title.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 70), 'References\nSmith, A. 2020.\n'
                         'Appendix A: Scientific measurements\n'
                         'Journal of Scientific Research, 10.\n'
                         'Jones, B. 2021. Further measurements.', fontsize=10)
        entry = _line_box(page, 'Jones,')
        doc.save(path)
    sections = detect_sections(path)
    assert [h['kind'] for h in sections['section_headings']] == ['references']
    assert is_reference_region(1, entry, sections['reference_regions'])


def test_caption_and_later_entries_in_one_block_do_not_hide_references(tmp_path):
    path = tmp_path / 'caption-and-bibliography.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 70), 'References', fontsize=11)
        page = doc.new_page()
        page.insert_text((40, 70), 'Figure 2: Scientific measurements of the sample.\n'
                         'The error bars represent measurement uncertainty.\n'
                         'Smith, A. 2020. A scientific method.\n'
                         'Journal of Scientific Research, 10.\n'
                         'Jones, B. 2021. Further measurements.', fontsize=10)
        assert len(_text_blocks(page)) == 1
        caption, entry = _line_box(page, 'The error'), _line_box(page, 'Smith,')
        doc.save(path)
    sections = detect_sections(path)
    assert not is_reference_region(2, caption, sections['reference_regions'])
    assert is_reference_region(2, entry, sections['reference_regions'])


def test_physical_blocks_joining_columns_do_not_mix_body_with_bibliography(tmp_path):
    path = tmp_path / 'joined-columns.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        for index, y in enumerate((65, 77, 89, 101)):
            left = ['Acknowledgments', 'We thank our scientific colleagues.',
                    'References', 'Smith, A. 2020. A scientific method.'][index]
            right = ['Jones, B. 2021. A scientific method.', 'Journal of Scientific Research, 9.',
                     'Brown, C. 2022. Further measurements.', 'Journal of Scientific Research, 10.'][index]
            page.insert_text((40, y), left, fontsize=9)
            page.insert_text((320, y), right, fontsize=9)
        assert any(b['bbox'][0] < 300 < b['bbox'][2] for b in _text_blocks(page))
        ack, entry = _line_box(page, 'We thank'), _line_box(page, 'Jones,')
        doc.save(path)
    sections = detect_sections(path)
    assert not is_reference_region(1, ack, sections['reference_regions'])
    assert is_reference_region(1, entry, sections['reference_regions'])
    profile, _ = read_source_profile(path, sections)
    assert any('We thank' in s['source'] for s in profile)
    assert not any('Scientific Research' in s['source'] for s in profile)


def test_reference_columns_do_not_split_a_centred_footer_number(tmp_path):
    path = tmp_path / 'centred-footer.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 60), 'References', fontsize=11)
        for x in (40, 320):
            page.insert_text((x, 90), 'Smith, A. 2020. A scientific method.\n'
                             'Journal of Scientific Research, 10.\n'
                             'Jones, B. 2021. Further measurements.', fontsize=9)
        page.insert_text((286, 750), '12345', fontsize=10)
        footer, entry = _line_box(page, '12345'), _line_box(page, 'Smith,')
        doc.save(path)
    regions = detect_sections(path)['reference_regions']
    assert is_reference_region(1, entry, regions)
    assert not any(fitz.Rect(r['bbox']).intersects(fitz.Rect(footer)) for r in regions)


def test_references_in_a_table_header_does_not_hide_later_body(tmp_path):
    path = tmp_path / 'reference-table-label.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for x, text in [(40, 'Detector'), (220, 'Data set'), (420, 'References')]:
            page.insert_text((x, 80), text, fontsize=10)
        page.insert_text((420, 92), 'Smith, A. 2020.', fontsize=10)
        page.insert_text((40, 240), 'These scientific body paragraphs must remain translatable.', fontsize=10)
        body = _line_box(page, 'These scientific')
        page.insert_text((40, 400), 'References\nJones, B. 2021. A scientific method.', fontsize=10)
        entry = _line_box(page, 'Jones,')
        doc.save(path)
    sections = detect_sections(path)
    assert len(sections['section_headings']) == 1
    assert sections['section_headings'][0]['y'] > 350
    assert not is_reference_region(1, body, sections['reference_regions'])
    assert is_reference_region(1, entry, sections['reference_regions'])

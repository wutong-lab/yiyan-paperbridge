"""Image-heavy pages must retain the same checks without per-image rendering."""
import shutil

import pymupdf as fitz
import pytest

import paperbridge.quality as quality


def picture_pdf(path, *, count=1, rotation=0, crop=False):
    with fitz.open() as doc:
        page = doc.new_page(width=360, height=480)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
        pix.clear_with(90)
        page.insert_image(fitz.Rect(40.25, 70.25, 100.5, 110.5), pixmap=pix)
        if count > 1:
            image_name = page.get_images()[0][7]
            drawing = ''.join(f'q 0.6 0 0 0.6 {30+(i%250)} {200+(i//250)%120} cm /{image_name} Do Q\n'
                              for i in range(count - 1))
            xref = doc.get_new_xref(); doc.update_object(xref, '<<>>')
            doc.update_stream(xref, drawing.encode('ascii'))
            page.set_contents(xref)
            # This fixture intentionally replaces the first image occurrence.
            count -= 1
        page.insert_text((35, 430), 'Source prose outside all figures')
        if crop:
            page.set_cropbox(fitz.Rect(20, 30, 340, 460))
        page.set_rotation(rotation)
        doc.save(path)
    return count


def test_dense_page_renders_once_per_document_and_checks_every_image(tmp_path, monkeypatch):
    source, target = tmp_path/'dense.pdf', tmp_path/'translated.pdf'
    count = picture_pdf(source, count=27_285)
    shutil.copyfile(source, target)
    render_calls = []
    original = fitz.DisplayList.get_pixmap
    def render(self, *args, **kwargs):
        render_calls.append(kwargs)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(fitz.DisplayList, 'get_pixmap', render)
    events = []
    report = quality.inspect_pdf(source, target, [], progress=events.append)
    assert report['structural_checks_passed']
    assert report['performance']['image_regions_checked'] == count
    assert len(render_calls) == 2
    assert len(report['pages'][0]['image_region_max_pixel_deltas']) == count
    assert events[0]['fraction'] == 0 and events[-1]['fraction'] == 1
    assert [e['fraction'] for e in events] == sorted(e['fraction'] for e in events)
    assert any(e['phase'] == 'images' for e in events)


@pytest.mark.parametrize('rotation,crop', [(0, False), (90, False), (180, True), (270, True)])
def test_rotation_crop_and_changed_prose_preserve_figures(tmp_path, rotation, crop):
    source, target = tmp_path/'source.pdf', tmp_path/'translated.pdf'
    picture_pdf(source, rotation=rotation, crop=crop)
    with fitz.open(source) as doc:
        doc[0].insert_text((170, 250), 'Translation outside the image')
        doc.save(target)
    report = quality.inspect_pdf(source, target, [])
    assert report['structural_checks_passed']
    assert report['pages'][0]['image_region_max_pixel_deltas'] == [0]


def test_overlay_is_detected_even_when_image_bytes_and_bboxes_are_unchanged(tmp_path):
    source, target = tmp_path/'source.pdf', tmp_path/'covered.pdf'
    picture_pdf(source)
    with fitz.open(source) as doc:
        doc[0].draw_rect(fitz.Rect(50, 80, 70, 100), fill=(1, 0, 0), color=None)
        doc.save(target)
    report = quality.inspect_pdf(source, target, [])
    page = report['pages'][0]
    assert page['images_preserved']
    assert not page['image_appearance_preserved']
    assert max(page['image_region_max_pixel_deltas']) > 2
    assert not report['structural_checks_passed']


def test_bounded_tiles_match_full_page_deltas(tmp_path, monkeypatch):
    source, target = tmp_path/'source.pdf', tmp_path/'covered.pdf'
    picture_pdf(source, rotation=90, crop=True)
    with fitz.open(source) as doc:
        doc[0].draw_rect(fitz.Rect(35, 55, 55, 75), fill=(0, 1, 0), color=None)
        doc.save(target)
    first = quality.inspect_pdf(source, target, [])
    monkeypatch.setattr(quality, 'MAX_RENDER_PIXELS', 1)
    monkeypatch.setattr(quality, 'TILE_EDGE', 64)
    second = quality.inspect_pdf(source, target, [])
    assert first['pages'] == second['pages']
    assert second['performance']['page_renders'] == 0
    assert second['performance']['tile_renders'] > 0


def test_cancellation_interrupts_image_inspection_before_publication(tmp_path):
    source, target = tmp_path/'source.pdf', tmp_path/'translated.pdf'
    picture_pdf(source, count=300)
    shutil.copyfile(source, target)
    events = []
    def progress(event):
        events.append(event)
    def cancelled():
        return any(e['phase'] == 'images' for e in events)
    with pytest.raises(InterruptedError, match='已停止 PDF 校验'):
        quality.inspect_pdf(source, target, [], progress=progress, cancelled=cancelled)
    assert not any(e['phase'] == 'page_complete' for e in events)

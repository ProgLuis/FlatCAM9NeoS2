# ##########################################################
# FlatCAM 9 Neo S2
# PDF prepared input helper
# ##########################################################

import os
import tempfile


def prepare_pdf_page(pdf_filename, page_number=1, page_count=1, crop_rect=None):
    """
    Create a temporary single-page PDF for the selected page/crop.

    Returns (prepared_filename, temp_filename). When no preparation is needed,
    temp_filename is None and prepared_filename is the original PDF.
    """

    import fitz

    page_number = int(page_number or 1)
    page_count = int(page_count or 1)
    if page_number < 1 or page_number > page_count:
        raise ValueError('Selected PDF page %s is outside the available range 1-%s.' % (page_number, page_count))

    if crop_rect is None and page_count <= 1:
        return pdf_filename, None

    source_doc = fitz.open(pdf_filename)
    temp_filename = None
    try:
        page_index = page_number - 1
        target_doc = fitz.open()
        try:
            if crop_rect is None:
                target_doc.insert_pdf(source_doc, from_page=page_index, to_page=page_index)
            else:
                source_page = source_doc.load_page(page_index)
                clip = fitz.Rect(*crop_rect)
                target_page = target_doc.new_page(width=clip.width, height=clip.height)
                # Preserve the original PDF vector content for the legacy
                # ParsePDF parser; the page merely exposes the selected clip.
                target_page.show_pdf_page(target_page.rect, source_doc, page_index, clip=clip)

            fd, temp_filename = tempfile.mkstemp(suffix='.pdf', prefix='flatcam_pdf_prepared_')
            os.close(fd)
            target_doc.save(temp_filename)
        finally:
            target_doc.close()
    finally:
        source_doc.close()

    return temp_filename, temp_filename


def remove_prepared_pdf(temp_filename):
    if temp_filename:
        try:
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
                return True
        except Exception:
            return False
    return False

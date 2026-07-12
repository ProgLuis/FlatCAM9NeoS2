# ##########################################################
# FlatCAM 9 Neo S2
# Shared PDF import complexity limits
# ##########################################################

PDF_WARN_VECTOR_OPS = 4000
PDF_MAX_VECTOR_OPS = 6000

PDF_HIGH_COMPLEXITY_MESSAGE = (
    "High complexity PDF selection detected. The import may take longer and use more memory. "
    "Consider reducing the crop area, exporting only the PCB layer, or simplifying the PDF before machining."
)

PDF_TOO_COMPLEX_MESSAGE = (
    "The selected PDF content exceeds the safe processing limit. Import was cancelled to avoid excessive "
    "processing time or memory usage. Reduce the crop area, remove decorative artwork, or export only the PCB layer."
)


def pdf_complexity_status(count):
    if count > PDF_MAX_VECTOR_OPS:
        return 'too_complex'
    if count > PDF_WARN_VECTOR_OPS:
        return 'warning'
    return 'normal'

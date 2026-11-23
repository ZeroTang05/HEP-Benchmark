"""HEP 目标论文 PDF 身份预检验收。"""
from __future__ import annotations

import pymupdf
import pytest

from researchfactory.taskgen.tasks.researchclawbench.hep_paper import ensure_target_paper_identity


def _pdf(path, text: str) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_target_paper_identity_accepts_matching_title(tmp_path) -> None:
    pdf = tmp_path / "target.pdf"
    _pdf(pdf, "Properties of Heavy Cosmic Nuclei from the Alpha Magnetic Spectrometer")

    ensure_target_paper_identity(pdf, {
        "title": "Properties of Heavy Cosmic Nuclei from the Alpha Magnetic Spectrometer",
    })


def test_target_paper_identity_rejects_mismatched_pdf(tmp_path) -> None:
    pdf = tmp_path / "wrong.pdf"
    _pdf(pdf, "Precision Measurement of the Positron Fraction in Primary Cosmic Rays")

    with pytest.raises(ValueError, match="target paper identity mismatch"):
        ensure_target_paper_identity(pdf, {
            "title": "Properties of Heavy Cosmic Nuclei from the Alpha Magnetic Spectrometer",
        })

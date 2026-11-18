"""The document's language and the passages in another one, through the text pass
(`text_pass.annotate`, what `apply` runs on a converted file), one test per kind of document.

The order is: the language chosen at upload, then the text, then the file's own /Lang when
the text cannot decide, else English assumed. Passages are found block by block.
"""

from __future__ import annotations

import pytest

from readablestem.core.types import Element, Kind, Notice, inline_marker
from readablestem.pipeline.language import is_language_tag
from readablestem.pipeline.text_pass import PdfText, annotate

ENGLISH = [
    "The average velocity is the quotient of the displacement and the duration of the trip.",
    "A cart that starts from rest speeds up at a steady rate on a smooth surface.",
    "Kinetic energy is conserved in an elastic collision between two carts.",
    "Find the eigenvalues of the matrix and show that they are all real numbers.",
    "Acceleration is the derivative of the velocity with respect to time.",
    f"A cart that starts with speed {inline_marker('p1-e1')} speeds up at a steady rate.",
]
SPANISH = [
    "La velocidad media es el cociente entre el desplazamiento y la duración del trayecto.",
    "Un carrito que parte del reposo acelera de manera uniforme sobre una superficie lisa.",
    "La energía cinética se conserva en una colisión elástica entre dos carritos.",
    "Calcule los valores propios de la matriz y demuestre que todos son reales.",
    "La aceleración es la derivada de la velocidad con respecto al tiempo.",
    "Los estudiantes deben resolver todos los problemas antes de la próxima clase.",
]
# 82 letters: under the bar for Latin text, but a Chinese letter carries about three.
CHINESE = [
    "平均速度等于位移除以行程所用的时间,单位是米每秒。",
    "从静止开始的小车在光滑的表面上以恒定的加速度运动。",
    "在两辆小车的弹性碰撞中,动能是守恒的。",
    "求这个矩阵的特征值,并证明它们都是实数。",
]
JAPANESE = [
    "平均の速さは、移動した距離を、かかった時間で割った値です。",
    "静止していた台車は、なめらかな面の上で一定の割合で速くなります。",
    "二つの台車の弾性衝突では、運動エネルギーが保存されます。",
    "この行列の固有値を求め、それらがすべて実数であることを示しなさい。",
    "加速度は、速度を時間で微分したものであり、ベクトルです。",
]
ARABIC = [
    "السرعة المتوسطة هي حاصل قسمة الإزاحة على مدة الرحلة ويقاس بالمتر في الثانية",
    "عربة تبدأ من السكون تتسارع بمعدل ثابت على سطح أملس بدون احتكاك يذكر",
    "تتحفظ الطاقة الحركية في التصادم المرن بين عربتين على مسار مستقيم",
    "أوجد القيم الذاتية للمصفوفة وبين أنها جميعا أعداد حقيقية في هذه المسألة",
]
SHORT = ["Speeds are below.", "The speed doubles.", "Check the table."]
SPANISH_PARAGRAPH = (
    "La velocidad media es el cociente entre el desplazamiento y la duración del trayecto, "
    "y se expresa en metros por segundo en el sistema internacional."
)


def run(texts, pdf_lang=None, layout_lang="en", chosen=None):
    elements = [
        Element(
            id=f"p1-e{i}", kind=Kind.PARAGRAPH, page=0, bbox=(72, 98, 572, 112), order=i, text=t
        )
        for i, t in enumerate(texts)
    ]
    result = annotate(elements, PdfText(lang=pdf_lang), layout_lang=layout_lang, chosen_lang=chosen)
    return result, [n.code for n in result.notices if n.code.startswith("lang")]


def test_an_english_document_is_english_and_marks_no_passage():
    result, notices = run(ENGLISH)
    assert (result.lang, result.lang_source, notices) == ("en", "detected", ["lang_detected"])
    assert not any(e.lang for e in result.elements)


def test_a_spanish_document_is_spanish_even_when_the_file_says_english():
    # Word writes en-US into files in other languages: the text outranks it.
    result, notices = run(SPANISH, pdf_lang="en-US")
    assert (result.lang, result.lang_source) == ("es", "detected")
    assert "lang_assumed" not in notices
    result, _ = run(SPANISH, pdf_lang="es-MX")
    assert (result.lang, result.lang_source) == ("es-MX", "pdf")


def test_a_mixed_document_takes_its_largest_language_and_asks():
    # Two languages in paragraphs: the detector is sure of each paragraph, of no one answer.
    result, notices = run(
        [" ".join(SPANISH[:3]), " ".join(ENGLISH[:3]), " ".join(SPANISH[3:]), " ".join(ENGLISH[3:])]
    )
    assert result.lang in ("es", "en")
    assert "lang_mixed" in notices
    assert {n.review for n in result.notices if n.code == "lang_mixed"} == {"required"}


@pytest.mark.parametrize(("texts", "expected"), [(CHINESE, "zh"), (JAPANESE, "ja"), (ARABIC, "ar")])
def test_chinese_japanese_and_arabic_documents_are_detected(texts, expected):
    result, notices = run(texts)
    assert (result.lang, result.lang_source) == (expected, "detected")
    assert "lang_assumed" not in notices


def test_a_short_page_takes_the_files_language_or_is_assumed_and_required():
    result, notices = run(SHORT, pdf_lang="es")
    assert (result.lang, result.lang_source, notices) == ("es", "pdf", [])
    result, notices = run(SHORT)
    assert (result.lang, result.lang_source, notices) == ("en", "default", ["lang_assumed"])
    assert Notice(code="lang_assumed", review="required") in result.notices
    # A file's language that does not fit the text is not used.
    result, _ = run(["السرعة المتوسطة هي حاصل قسمة الإزاحة"], pdf_lang="en")
    assert result.lang_source != "pdf"


def test_a_chosen_language_is_stated_and_decides_over_everything():
    result, notices = run(SPANISH, pdf_lang="en", chosen="fr")
    assert (result.lang, result.lang_source, notices) == ("fr", "user", [])
    result, notices = run(SHORT, chosen="de")
    assert (result.lang, result.lang_source, notices) == ("de", "user", [])


def test_a_passage_in_another_language_is_marked_only_on_a_whole_block():
    result, _ = run([*ENGLISH, SPANISH_PARAGRAPH])
    assert result.lang == "en"
    assert [e.lang for e in result.elements] == [None] * len(ENGLISH) + ["es"]


@pytest.mark.parametrize(
    "tag", ["", "e", "fr_FR", "english language", "en-", "-en", "en--US", "a" * 9, "en-US-x"]
)
def test_malformed_tags(tag):
    assert not is_language_tag(tag)

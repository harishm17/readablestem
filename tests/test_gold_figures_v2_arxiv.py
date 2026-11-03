"""Figure gold v2, arXiv strand: reading plotted data out of pgfplots TeX."""

from __future__ import annotations

from readablestem.gold import figures_v2_arxiv as fa

# ruff: noqa: E501

COORDS = r"""
\begin{figure}
\centering
\begin{tikzpicture}
\begin{axis}[xlabel={Time $t$ (s)}, ylabel={Speed (m/s)}, legend pos=north west]
\addplot coordinates {(0,0) (1,2.5) (2,5) (3,7.5)};
\addlegendentry{Car}
\addplot coordinates {(0,1) (1,1.5) (2,2) (3,2.5)};
\addlegendentry{Bike}
\end{axis}
\end{tikzpicture}
\caption{Speed of a car and a bike.}
\end{figure}
"""


def test_coordinates_become_a_shared_x_table() -> None:
    [fig] = fa.find_plots(COORDS)
    table = fig.table()
    assert table["header"] == ["Time t (s)", "Car", "Bike"]
    assert table["rows"] == [
        ["0", "0", "1"],
        ["1", "2.5", "1.5"],
        ["2", "5", "2"],
        ["3", "7.5", "2.5"],
    ]
    assert table["source"] == "source-data"
    assert fig.caption == "Speed of a car and a bike."


def test_long_description_uses_only_source_numbers() -> None:
    [fig] = fa.find_plots(COORDS)
    text = fig.long_description()
    assert "Time t (s)" in text and "Speed (m/s)" in text
    assert "1: 2.5" in text and "3: 2.5" in text
    assert len(text) >= 120


def test_series_with_different_x_make_long_rows() -> None:
    tex = COORDS.replace("(0,1) (1,1.5) (2,2) (3,2.5)", "(0,1) (1,1.5) (5,2) (6,2.5)")
    [fig] = fa.find_plots(tex)
    assert fig.table()["header"] == ["series", "Time t (s)", "Speed (m/s)"]
    assert len(fig.table()["rows"]) == 8


def test_inline_table_and_bar_kind() -> None:
    tex = r"""
\begin{tikzpicture}
\begin{axis}[ybar, xlabel={Method}, ylabel={Accuracy}, symbolic x coords={A,B,C}]
\addplot table [x=m, y=acc] {
m acc
A 0.5
B 0.7
C 0.9
};
\end{axis}
\end{tikzpicture}
"""
    [fig] = fa.find_plots(tex)
    assert fig.kind == "bar"
    assert fig.table()["rows"] == [["A", "0.5"], ["B", "0.7"], ["C", "0.9"]]


def test_symbolic_x_coordinates() -> None:
    tex = r"""
\begin{tikzpicture}\begin{axis}[ybar,xlabel={Group}]
\addplot coordinates {(red,3) (green,5) (blue,4)};
\end{axis}\end{tikzpicture}
"""
    [fig] = fa.find_plots(tex)
    assert [row[0] for row in fig.table()["rows"]] == ["red", "green", "blue"]


def test_charts_without_inline_data_are_refused() -> None:
    from_file = r"\begin{tikzpicture}\begin{axis}\addplot table {data/results.dat};\end{axis}\end{tikzpicture}"
    formula = r"\begin{tikzpicture}\begin{axis}\addplot {x^2};\end{axis}\end{tikzpicture}"
    assert fa.find_plots(from_file) == []
    assert fa.find_plots(formula) == []


def test_gates_on_size_and_group_plots() -> None:
    too_few = r"\begin{tikzpicture}\begin{axis}\addplot coordinates {(0,0) (1,1)};\end{axis}\end{tikzpicture}"
    many = "".join(f"({i},{i})" for i in range(fa.MAX_ROWS + 1))
    too_many = rf"\begin{{tikzpicture}}\begin{{axis}}\addplot coordinates {{{many}}};\end{{axis}}\end{{tikzpicture}}"
    group = COORDS.replace(
        r"\begin{axis}",
        r"\begin{groupplot}[group style={group size=2 by 1}]\nextgroupplot\begin{axis}",
    )
    assert fa.find_plots(too_few) == []
    assert fa.find_plots(too_many) == []
    assert fa.find_plots(group) == []


def test_non_numeric_y_and_leftover_text_are_refused() -> None:
    tex = r"\begin{tikzpicture}\begin{axis}\addplot coordinates {(0,0) (1,x) (2,2)};\end{axis}\end{tikzpicture}"
    assert fa.find_plots(tex) == []


def test_commented_plots_are_ignored() -> None:
    tex = "\n".join("% " + line for line in COORDS.splitlines())
    assert fa.find_plots(tex) == []


def test_labels_with_leftover_commands_are_dropped_to_empty() -> None:
    assert fa.plain(r"\alpha \cdot x") == ""
    assert fa.plain(r"$\mathrm{Speed}$ (m/s)") == "Speed (m/s)"


def test_standalone_document_contains_the_figure() -> None:
    [fig] = fa.find_plots(COORDS)
    doc = fig.standalone()
    assert doc.startswith("\\documentclass")
    assert "\\begin{axis}" in doc and doc.rstrip().endswith("\\end{document}")

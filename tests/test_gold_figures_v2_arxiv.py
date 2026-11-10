"""Figure gold v2, arXiv strand: reading plotted data out of pgfplots TeX."""

from __future__ import annotations

from readablestem.gold import figures_v2_arxiv as fa

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


def test_long_description_uses_only_source_numbers() -> None:
    [fig] = fa.find_plots(COORDS)
    text = fig.long_description()
    assert "Time t (s)" in text and "Speed (m/s)" in text
    assert "1: 2.5" in text and "3: 2.5" in text
    assert len(text) >= 120

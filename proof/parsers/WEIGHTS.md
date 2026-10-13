# Model weights the parser runs used

The run records in `proof/interim/heldout-2026-10/parsers/*/*.json` list package versions and
say `"offline": false`; they do not name the weights. The weights were fetched on first use
(Docling from Hugging Face, MinerU through `mineru-kit models download --source huggingface`).
This file lists what the cache held on 2026-10-13 after the held-out runs. The files were
written before the runs (the times are the files' own), and nothing was downloaded between, but
the run did not record this itself, so treat the list as evidence, not as a record made by the
run. A re-run on another day should warm the cache once and then run offline, so the weights
cannot change under it.

## Docling 2.138.0 (default settings)

Hugging Face cache, `~/.cache/huggingface/hub`. SHA-256 of the weight files:

| Repository | Revision | File | SHA-256 | Written |
|---|---|---|---|---|
| `docling-project/docling-layout-heron` | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | `model.safetensors` | `00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c` | 2026-10-08 |
| `docling-project/docling-models` | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | `model_artifacts/tableformer/accurate/tableformer_accurate.safetensors` | `2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9` | 2026-10-08 |

RapidOCR's models come with the `rapidocr` package pinned in the arm's lock file; they are not
hashed here.

## MinerU 4.0.12, basic tier

`opendatalab/MinerU-4_models_onnx`, revision `358310b4f64b95f9fefc372ad899356e4111f376`
(from the download metadata), written 2026-10-13 10:37 local time:

| File | SHA-256 |
|---|---|
| `Layout/PP-DocLayoutV2/inference.onnx` | `cd540dc296ff3115fe78efa65b68501e6a8dc74b198acc9834a725ffaa095aac` |
| `MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M.onnx` | `20a32595c2b30282dcd069e6295e9fad96115697beb66acb9b681b814006d193` |
| `OCR/paddleocr/ch_PP-OCRv6_small_rec_infer.onnx` | `5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634` |
| `OCR/paddleocr/ch_PP-OCRv6_tiny_det_infer.onnx` | `193bab7a04fca699a6c82e6abb5b81bdb28177f0abd4062552b04908dafb19f8` |
| `OCR/paddleocr/seal_PP-OCRv4_det_infer.onnx` | `3e190df84542c2756975febe8f73603b587a23c7f7badfdd3fc8a986540d5bc0` |
| `Table/PP-LCNet_x1_0_table_cls.onnx` | `c84bf1d79c1c74d534b5b12adb14dd12151c42f7ae3e4be4f1042b830f80b949` |
| `Table/slanet-plus.onnx` | `d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b` |
| `Table/unet.onnx` | `0ea48d3a17e35ef5c2e498a5e799566073234d39b1079ca21d9f4fafe73c6d20` |

## PyMuPDF4LLM 1.28.2

The layout model is the ONNX file inside the `pymupdf-layout` wheel (version in the arm's lock
file); nothing is downloaded.

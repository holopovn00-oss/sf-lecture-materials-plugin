"""Compile anchored math expressions with Tectonic and verify placed vector math.

The default is offline. A caller explicitly enables downloads to populate the
managed TeX cache. Tectonic discovery is shared with dependency_preflight.py;
the renderer itself never installs a runtime or edits plugin files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from dependency_preflight import default_tectonic_cache_dir, resolve_tectonic
from lecture_content import digest, latex_digest, require, validate_latex

RENDERER_VERSION = "1.0"
PADDING_PT = 2.0
TEX_TO_PT = 72 / 72.27


def file_ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper()}


def checked_file(record):
    require(isinstance(record, dict) and isinstance(record.get("path"), str), "Invalid math file reference")
    path = Path(record["path"])
    require(path.is_absolute() and path.is_file(), f"Missing math file: {path}")
    require(file_ref(path)["sha256"] == record.get("sha256", "").upper(), "Changed math file")
    return path


def tex_source(latex, mode, size_pt):
    validate_latex(latex)
    require(mode in {"inline", "display"}, "Unknown math mode")
    require(type(size_pt) in {int, float} and math.isfinite(size_pt) and 5 <= size_pt <= 36,
            "Invalid math font size")
    style = r"\textstyle" if mode == "inline" else r"\displaystyle"
    # A directly shipped box avoids a standalone/preview package dependency.
    return r"""\RequirePackage{fix-cm}
\documentclass{article}
\usepackage{amsmath,amssymb}
\pagestyle{empty}
\newbox\sfmathbox
\newdimen\sfwidth
\newdimen\sfheight
\begin{document}
\setbox\sfmathbox=\hbox{{\fontsize{%s bp}{%s bp}\selectfont\(%s %s\)}}
\typeout{SFBOX:\the\wd\sfmathbox;\the\ht\sfmathbox;\the\dp\sfmathbox}
\sfwidth=\wd\sfmathbox\advance\sfwidth by 4bp
\sfheight=\ht\sfmathbox\advance\sfheight by \dp\sfmathbox\advance\sfheight by 4bp
\hoffset=-1in\voffset=-1in
\shipout\vbox{\offinterlineskip\special{papersize=\the\sfwidth,\the\sfheight}\kern2bp\hbox{\kern2bp\box\sfmathbox\kern2bp}\kern2bp}
\end{document}
""" % (format(size_pt, ".6g"), format(size_pt * 1.4, ".6g"), style, latex)


def compile_math(latex, mode, size_pt, out, *, tectonic=None, cache_dir=None,
                 allow_downloads=False, timeout=45):
    """Create a PDF and JSON receipt in a new directory; reuse only matching assets."""
    import fitz
    source = tex_source(latex, mode, size_pt)
    out = Path(out).resolve()
    recipe = {"renderer_version": RENDERER_VERSION, "latex": latex, "mode": mode, "size_pt": size_pt}
    receipt_path = out / "math.json"
    if out.exists():
        require(receipt_path.is_file(), f"Incomplete math output; use a new directory: {out}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        require(receipt.get("recipe") == recipe, "Cached math belongs to a different expression or style")
        validate_asset(latex, mode, size_pt, file_ref(receipt_path))
        return receipt
    resolution = resolve_tectonic(tectonic)
    require(resolution is not None,
            "Tectonic is unavailable; run dependency_preflight.py --json, then approve its proposed installation")
    executable = resolution["path"]
    out.mkdir(parents=True, exist_ok=False)
    source_path = out / "math.tex"
    source_path.write_text(source, encoding="utf-8")
    command = [str(executable), "-X", "compile", "--untrusted", "--reruns", "0", "--keep-logs",
               "--outdir", str(out)]
    if not allow_downloads:
        command.append("--only-cached")
    command.append("math.tex")
    env = {**os.environ, "TECTONIC_UNTRUSTED_MODE": "1"}
    if cache_dir:
        env["TECTONIC_CACHE_DIR"] = str(Path(cache_dir).resolve())
    else:
        env["TECTONIC_CACHE_DIR"] = str(default_tectonic_cache_dir().resolve())
    try:
        process = subprocess.run(command, cwd=out, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"LaTeX compilation timed out after {timeout}s; inspect {out}") from exc
    (out / "compile.log").write_text(process.stdout, encoding="utf-8")
    pdf = out / "math.pdf"
    require(process.returncode == 0 and pdf.is_file(),
            f"LaTeX compilation failed (code {process.returncode}); see {out / 'compile.log'}; "
            "run dependency_preflight.py --json to check the TeX cache")
    log = (out / "math.log").read_text(encoding="utf-8", errors="replace")
    require("Missing character:" not in log and "Overfull" not in log, "LaTeX has missing glyphs or overflow")
    metrics = re.search(r"SFBOX:([0-9.]+)pt;([0-9.]+)pt;([0-9.]+)pt", log)
    require(metrics, "LaTeX did not report measured math metrics")
    width, ascent, depth = [float(v) * TEX_TO_PT for v in metrics.groups()]
    with fitz.open(pdf) as doc:
        require(len(doc) == 1 and not doc.needs_pass, "A math expression must produce exactly one PDF page")
        page = doc[0]
        require(abs(page.rect.width - width - 2 * PADDING_PT) < 0.2
                and abs(page.rect.height - ascent - depth - 2 * PADDING_PT) < 0.2,
                "Math PDF differs from TeX box metrics")
        chars = glyphs(page)
        require(chars, "Math PDF contains no extractable glyphs")
        check_ink_margin(page)
        for font in page.get_fonts(full=True):
            require(bool(doc.extract_font(font[0])[3]), "Math font is not embedded")
        dimensions = [page.rect.width, page.rect.height]
    receipt = {"schema_version": "1.0", "recipe": recipe, "recipe_hash": digest(recipe),
               "latex_sha256": latex_digest(latex), "source": file_ref(source_path), "pdf": file_ref(pdf),
               "size_pt": dimensions, "baseline_pt": ascent + PADDING_PT,
               "padding_pt": PADDING_PT, "compile_log": file_ref(out / "compile.log"),
               "tex_log": file_ref(out / "math.log"), "compiler_exit_code": process.returncode,
               "syntax": "COMPILED", "source_correctness": "REQUIRES_SOURCE_REVIEW"}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def glyphs(page, clip=None):
    import fitz
    result = []
    bounds = fitz.Rect(clip) if clip is not None else None
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span["chars"]:
                    # CMEX font boxes can extend above a correctly typeset page.
                    # Select by glyph origins; verify actual ink separately.
                    x, y = char["origin"]
                    # A following punctuation origin can round just inside x1
                    # during PDF serialization. Treat the right/bottom as half-open.
                    inside = bounds is None or (bounds.x0 <= x < bounds.x1 - .001
                                                and bounds.y0 <= y < bounds.y1 - .001)
                    if not char["c"].isspace() and inside:
                        result.append({"c": char["c"], "font": re.sub(r"^[A-Z]{6}\+", "", span["font"]),
                                       "size": span["size"], "bbox": list(char["bbox"]), "origin": list(char["origin"])})
    return result


def check_ink_margin(page):
    import fitz
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY, alpha=False)
    data, w, h = pix.samples, pix.width, pix.height
    edge = list(data[:w]) + list(data[-w:]) + [data[y*w] for y in range(h)] + [data[y*w+w-1] for y in range(h)]
    require(w > 2 and h > 2 and min(edge) >= 250, "Math ink touches the asset boundary")


def validate_asset(latex, mode, size_pt, receipt_ref):
    import fitz
    receipt_path = checked_file(receipt_ref)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    recipe = {"renderer_version": RENDERER_VERSION, "latex": latex, "mode": mode, "size_pt": size_pt}
    require(receipt.get("schema_version") == "1.0" and receipt.get("recipe") == recipe
            and receipt.get("recipe_hash") == digest(recipe) and receipt.get("latex_sha256") == latex_digest(latex),
            "Math receipt differs from the lecture LaTeX or requested style")
    require(checked_file(receipt["source"]).read_text(encoding="utf-8") == tex_source(latex, mode, size_pt),
            "Compiled TeX source differs from canonical LaTeX")
    checked_file(receipt["compile_log"])
    log = checked_file(receipt["tex_log"]).read_text(encoding="utf-8", errors="replace")
    require(receipt.get("compiler_exit_code") == 0 and receipt.get("syntax") == "COMPILED"
            and "Missing character:" not in log and "Overfull" not in log, "Math lacks successful compile evidence")
    metrics = re.search(r"SFBOX:([0-9.]+)pt;([0-9.]+)pt;([0-9.]+)pt", log)
    require(metrics, "Math log lacks TeX box metrics")
    width, ascent, depth = [float(v) * TEX_TO_PT for v in metrics.groups()]
    require(abs(receipt.get("baseline_pt", -1) - ascent - PADDING_PT) < 0.01
            and receipt.get("padding_pt") == PADDING_PT, "Math baseline differs from TeX metrics")
    pdf = checked_file(receipt["pdf"])
    with fitz.open(pdf) as doc:
        require(len(doc) == 1 and not doc.needs_pass, "Invalid math PDF")
        dimensions = [doc[0].rect.width, doc[0].rect.height]
        require(len(receipt["size_pt"]) == 2 and all(abs(a - b) < 0.01 for a, b in zip(receipt["size_pt"], dimensions)),
                "Math dimensions differ from asset")
        require(abs(doc[0].rect.width - width - 2 * PADDING_PT) < 0.2
                and abs(doc[0].rect.height - ascent - depth - 2 * PADDING_PT) < 0.2, "Math metrics differ from asset")
        require(glyphs(doc[0]), "Empty math asset")
        check_ink_margin(doc[0])
        require(all(bool(doc.extract_font(f[0])[3]) for f in doc[0].get_fonts(full=True)), "Math font is not embedded")
    return receipt, pdf


def placed_matches(page, rect, asset_path, tolerance=0.2):
    """Compare actual glyphs and vector rules at 1:1 size, not just receipt hashes."""
    import fitz
    rect = fitz.Rect(rect)
    with fitz.open(asset_path) as asset:
        original = asset[0]
        require(abs(rect.width - original.rect.width) <= tolerance and abs(rect.height - original.rect.height) <= tolerance,
                "Math was scaled or its placement box changed")
        actual, expected = glyphs(page, rect), glyphs(original)
        require(len(actual) == len(expected), "Missing, extra or clipped math glyph")
        # PDF object insertion order is not a mathematical reading order.
        key = lambda c: (c["c"], round(c["origin"][1], 1), round(c["origin"][0], 1))
        for char in actual:
            char["origin"] = [char["origin"][0] - rect.x0, char["origin"][1] - rect.y0]
            char["bbox"] = [char["bbox"][0] - rect.x0, char["bbox"][1] - rect.y0,
                            char["bbox"][2] - rect.x0, char["bbox"][3] - rect.y0]
        for a, b in zip(sorted(actual, key=key), sorted(expected, key=key)):
            require(a["c"] == b["c"] and a["font"] == b["font"] and abs(a["size"] - b["size"]) <= 0.05
                    and max(abs(x - y) for x, y in zip(a["bbox"] + a["origin"], b["bbox"] + b["origin"])) <= tolerance,
                    "Placed math glyph or index differs from the compiled expression")
        # Rendering at the same coordinates detects missing rules, altered strokes,
        # paint-over and nontext parts that text extraction cannot certify.
        probe = fitz.open()
        target = probe.new_page(width=page.rect.width, height=page.rect.height)
        target.show_pdf_page(rect, asset, 0)
        a = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect, colorspace=fitz.csGRAY, alpha=False)
        b = target.get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect, colorspace=fitz.csGRAY, alpha=False)
        require(a.width == b.width and a.height == b.height, "Math raster comparison sizes differ")
        # Antialiasing composites against #F6F6F6 in Golden versus white in the
        # reference. Permit that small luminance difference at the threshold;
        # a missing stroke still differs by hundreds of grayscale levels.
        bad = sum((x < 160) != (y < 160) and abs(x - y) > 12 for x, y in zip(a.samples, b.samples))
        ink = max(1, sum(y < 160 for y in b.samples))
        require(bad <= max(2, ink * 0.003), "Rendered math strokes differ from the compiled expression")
        probe.close()
        return [c["c"] for c in expected]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latex", required=True)
    parser.add_argument("--mode", choices=["inline", "display"], default="display")
    parser.add_argument("--size", type=float, default=9.5)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tectonic")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--allow-downloads", action="store_true")
    args = parser.parse_args()
    try:
        result = compile_math(args.latex, args.mode, args.size, args.out, tectonic=args.tectonic,
                              cache_dir=args.cache_dir, allow_downloads=args.allow_downloads)
        print(json.dumps({"status": "MATH_COMPILED", "receipt": file_ref(args.out / "math.json"),
                          "size_pt": result["size_pt"]}, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

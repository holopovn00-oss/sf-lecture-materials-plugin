"""Lossless JSON content and a bounded LaTeX math vocabulary (no PDF dependencies)."""
from __future__ import annotations

import hashlib
import json
import re

VERSION = "3.0.0"
MATH_ENVIRONMENTS = {"aligned", "alignedat", "gathered", "split", "cases", "matrix",
                     "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "array"}
# Math expressions only: no document commands, files, macros or shell operations.
MATH_COMMANDS = set("""
frac dfrac tfrac cfrac binom dbinom tbinom sqrt left right middle big Big bigg Bigg
bigl bigr Bigl Bigr biggl biggr Biggl Biggr lvert rvert lVert rVert vert Vert
langle rangle lceil rceil lfloor rfloor lbrace rbrace
mathrm mathbf mathit mathsf mathtt mathcal mathbb mathfrak boldsymbol text textrm
operatorname displaystyle textstyle scriptstyle scriptscriptstyle
sum prod coprod int iint iiint oint oiint oiiint lim limsup liminf inf sup min max
sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth log ln exp lg
det dim ker deg gcd Pr mod bmod pmod
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
varkappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi
varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
infty partial nabla ell hbar imath jmath Re Im emptyset varnothing
cdot cdots ldots dots vdots ddots times div pm mp ast star circ bullet
le leq ge geq ne neq approx sim simeq cong equiv propto ll gg lesssim gtrsim
in notin ni subset supset subseteq supseteq cup cap setminus mid nmid parallel
perp to mapsto rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow
longrightarrow longleftarrow longleftrightarrow Longrightarrow Longleftarrow
Longleftrightarrow implies iff forall exists nexists neg land lor wedge vee
underbrace overbrace underline overline bar widehat hat vec tilde widetilde dot
ddot overset underset stackrel substack limits nolimits not
quad qquad thinspace medspace thickspace enspace begin end
""".split())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest().upper()


def latex_digest(latex):
    return hashlib.sha256(latex.encode("utf-8")).hexdigest().upper()


def validate_latex(latex):
    """Check the expression boundary; successful TeX compilation checks syntax."""
    require(isinstance(latex, str) and latex.strip() and len(latex) <= 10000,
            "LaTeX must be a nonempty math expression of at most 10000 characters")
    braces = 0
    environments = []
    i = 0
    while i < len(latex):
        ch = latex[i]
        if ch == "\\":
            require(i + 1 < len(latex), "LaTeX ends with an incomplete command")
            match = re.match(r"\\([A-Za-z]+)", latex[i:])
            if match:
                command = match.group(1)
                require(command in MATH_COMMANDS, f"Unsupported LaTeX command: \\{command}")
                i += len(match.group(0))
                if command in {"begin", "end"}:
                    env = re.match(r"\s*\{([A-Za-z]+)\}", latex[i:])
                    require(env is not None and env.group(1) in MATH_ENVIRONMENTS,
                            "Unsupported LaTeX math environment")
                    name = env.group(1)
                    if command == "begin":
                        environments.append(name)
                    else:
                        require(environments and environments.pop() == name,
                                "Mismatched LaTeX math environment")
                    i += len(env.group(0))
                continue
            require(latex[i + 1] in "\\{},;:! %_#&|", "Unsupported LaTeX control symbol")
            i += 2
            continue
        require(ch not in "$%#" and (ord(ch) >= 32 or ch in "\n\t"),
                "Use a raw LaTeX expression, without dollar delimiters, comments or control characters")
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
            require(braces >= 0, "Unbalanced LaTeX braces")
        i += 1
    require(braces == 0 and not environments, "Unclosed LaTeX group or environment")
    return latex


def formula_items(block):
    for content_index, item in enumerate(block["content"]):
        if item["type"] == "display_math":
            yield content_index, None, item
        else:
            for run_index, run in enumerate(item["runs"]):
                if run["type"] == "math":
                    yield content_index, run_index, run


def validate_content(blocks):
    """Validate typed paragraphs and formula anchors; do not assess meaning."""
    formulas = {}
    for block in blocks:
        require(set(block) == {"text_block_id", "source_block_ids", "content"},
                "A 3.0.0 block contains text_block_id, source_block_ids and content")
        content = block["content"]
        require(isinstance(content, list) and content, "Empty content block")
        for item in content:
            require(isinstance(item, dict), "Invalid content item")
            if item.get("type") == "paragraph":
                require(set(item) == {"type", "runs"} and isinstance(item["runs"], list) and item["runs"],
                        "A paragraph requires nonempty runs")
                visible = False
                for run in item["runs"]:
                    require(isinstance(run, dict), "Invalid paragraph run")
                    if run.get("type") == "text":
                        value = run.get("text")
                        require(set(run) == {"type", "text"} and isinstance(value, str) and value,
                                "Text run must contain text")
                        require("\n" not in value and "\r" not in value,
                                "Paragraph boundaries must be content items, not newlines in text runs")
                        require(not re.search(r"\\[A-Za-z]+|\\[\[(]|\$\$", value),
                                "LaTeX belongs in a math run, not in ordinary text")
                        visible = visible or bool(value.strip())
                    else:
                        require(run.get("type") == "math", "Unknown paragraph run type")
                        visible = True
                require(visible, "A paragraph cannot contain whitespace only")
            else:
                require(item.get("type") == "display_math", "Unknown content item type")
        for _, _, formula in formula_items(block):
            required = {"type", "formula_id", "latex", "source_block_ids"}
            require(required <= formula.keys() and not formula.keys() - (required | {"evidence"}),
                    "Invalid formula fields")
            identifier = formula["formula_id"]
            require(isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier)
                    and identifier not in formulas, "Empty, unsafe or repeated formula ID")
            validate_latex(formula["latex"])
            anchors = formula["source_block_ids"]
            require(isinstance(anchors, list) and anchors and len(set(anchors)) == len(anchors)
                    and set(anchors) <= set(block["source_block_ids"]), "Formula has unknown or repeated source anchors")
            if "evidence" in formula:
                evidence = formula["evidence"]
                require(isinstance(evidence, dict) and set(evidence) == {"path", "sha256", "locator"}
                        and isinstance(evidence["path"], str) and evidence["path"]
                        and isinstance(evidence["sha256"], str) and re.fullmatch(r"[A-Fa-f0-9]{64}", evidence["sha256"])
                        and isinstance(evidence["locator"], str) and evidence["locator"].strip(),
                        "Formula evidence needs a file hash and an exact locator")
            formulas[identifier] = formula
    return formulas


def block_text(block):
    """Deterministic search/review projection; never a second editable source."""
    parts = []
    for item in block["content"]:
        if item["type"] == "display_math":
            parts.append("\\[" + item["latex"] + "\\]")
        else:
            parts.append("".join(run["text"] if run["type"] == "text" else "\\(" + run["latex"] + "\\)"
                                 for run in item["runs"]))
    return "\n\n".join(parts)

"""The docs are evidence only if what they point at exists (AGENTS.md: nothing only in a README).

Checks every Markdown file in docs/, AGENTS.md and README.md:

- every test cited as `file.py::test_name` (or a `test_...` name ending in "...") exists;
- every backticked repository path exists (short paths like `retrieval/search.py` may match the
  end of a real path);
- PowerShell blocks hold no bash-isms (`&&` is PowerShell 7 only; the owner may run 5.1);
- every SEAM(name) in code has a row in docs/DEPTH.md, and every "done" row there has evidence.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = sorted([*ROOT.joinpath("docs").rglob("*.md"), ROOT / "AGENTS.md", ROOT / "README.md"])
SKIP_DIRS = {".git", ".venv", "node_modules", "cdk.out", "build", "reports", "__pycache__", ".next", "out"}
TOP = ("backend/", "docs/", "infra/", "ml/", "evals/", "scripts/", "apps/", "db/", "tools/", "experiments/", ".github/",
       "security/", "requirements/", "demo/", ".zap/")


def _repo_files() -> list[str]:
    out = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if not set(rel.parts) & SKIP_DIRS:
            out.append(rel.as_posix() + ("/" if p.is_dir() else ""))
    return out


FILES = _repo_files()


def _tests() -> set[str]:
    names = set()
    for f in [*ROOT.joinpath("backend", "tests").glob("test_*.py"), *ROOT.joinpath("ml", "tests").glob("test_*.py")]:
        names |= set(re.findall(r"^def (test_\w+)", f.read_text(), flags=re.M))
    for f in ROOT.joinpath("infra", "test").glob("*.test.ts"):
        names |= set(re.findall(r"test\('([^']+)'", f.read_text()))
    return names


TESTS = _tests()
DESCRIBES = {d for f in ROOT.joinpath("infra", "test").glob("*.test.ts") for d in re.findall(r"describe\('([^']+)'", f.read_text())}


def _exists(path: str) -> bool:
    path = path.rstrip("/").removeprefix("./")
    if path.startswith("reports/"):
        return True  # generated output (gitignored), written by the command the doc shows
    if path.startswith(TOP):
        return any(f.rstrip("/") == path for f in FILES) or any(f.startswith(path + "/") for f in FILES)
    return any(f.rstrip("/").endswith("/" + path) or f.rstrip("/") == path for f in FILES)


def _cited_paths(text: str) -> list[str]:
    out = []
    for code in re.findall(r"`([^`\n]+)`", text):
        for token in code.split():
            token = token.strip("(),;:'\"").split("::")[0].split("#")[0]
            if any(c in token for c in "<>*{}$") or "://" in token or token.startswith(("-", "~", "@")):
                continue
            if re.fullmatch(r"[\w./-]+\.(py|ts|tsx|md|yaml|yml|json|jsonl|sql|ps1|sh|toml|tsv|guard|csv|mjs|js|txt)", token) and "/" in token:
                out.append(token)
            elif token.startswith(TOP) and token.endswith("/"):
                out.append(token)
    return out


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_path_a_document_cites_exists(doc):
    missing = sorted({p for p in _cited_paths(doc.read_text(encoding="utf-8")) if not _exists(p)})
    assert missing == []


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_test_a_document_cites_exists(doc):
    text = doc.read_text(encoding="utf-8")
    missing = []
    for m in re.finditer(r"\b(test_[a-z0-9_]+)(\.\.\.|\.py)?", text):
        name, tail = m.group(1), m.group(2)
        if tail == ".py":
            continue  # a test file: checked by the path test
        found = name in TESTS or (tail == "..." and any(t.startswith(name) for t in TESTS))
        if not found:
            missing.append(name + (tail or ""))
    # infra tests are cited by their quoted description: `stacks.test.ts` "operations dashboard"
    for quoted in re.findall(r'\.test\.ts`?(?:::| )"([^"]+)"', text) + re.findall(r"\.test\.ts::([a-z][^`|]+?)`", text):
        if not any(quoted.strip() in t for t in TESTS) and not any(quoted.strip() in d for d in DESCRIBES):
            missing.append(quoted)
    assert sorted(set(missing)) == []


BASHISMS = [(r"&&", "use ; (&& needs PowerShell 7)"), (r"^\s*export \w+=", "use $env:VAR = \"x\""), (r"\brm -rf\b", "use Remove-Item -Recurse -Force"),
            (r"\|\s*(tail|head|grep)\b", "use Select-Object / Select-String"), (r"^\s*sudo\b", "no sudo on Windows"), (r"<<\s*'?EOF", "no heredocs")]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_powershell_blocks_hold_no_bash_isms(doc):
    bad = []
    for block in re.findall(r"(?ms)^```powershell\n(.*?)^```", doc.read_text(encoding="utf-8")):
        for line in block.splitlines():
            unquoted = re.sub(r'"[^"]*"|\'[^\']*\'', '""', line)
            bad += [f"{line.strip()}  ({why})" for pat, why in BASHISMS if re.search(pat, unquoted)]
    assert bad == []


def test_every_seam_in_code_has_a_row_in_depth():
    depth = (ROOT / "docs" / "DEPTH.md").read_text()
    seams = set()
    for f in FILES:
        if f.endswith((".py", ".ts", ".ps1", ".sh", ".yml")) and not f.startswith(("backend/tests/", "docs/")):
            seams |= set(re.findall(r"SEAM\(([a-z0-9-]+)\)", (ROOT / f).read_text(errors="ignore")))
    assert seams and sorted(s for s in seams if f"SEAM({s})" not in depth) == []


def test_every_done_row_in_depth_has_evidence():
    rows = [r for r in (ROOT / "docs" / "DEPTH.md").read_text().splitlines() if r.startswith("| ") and not r.startswith("| Item")]
    empty = [r.split("|")[1].strip() for r in rows if len(r.split("|")) > 3 and r.split("|")[2].strip().startswith("done")
             and not r.split("|")[3].strip()]
    assert empty == []

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/judit/parser_parity_synthetic.json"
DONOR_COMMIT = "0d0f8cbb495292a3f3d61fba75428f3136059ea8"
DEFAULT_OUTPUT = ROOT / "docs/evaluation/evidence/m03-donor-template.json"


def capture(donor_path: Path) -> dict[str, Any]:
    donor_path = donor_path.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(donor_path), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != DONOR_COMMIT:
        raise ValueError(f"expected donor {DONOR_COMMIT}, got {commit}")
    if subprocess.check_output(
        ["git", "-C", str(donor_path), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("donor worktree must be clean to capture the reference")

    sys.path.insert(0, str(donor_path))
    from resumo.generator import GeradorTemplate
    from resumo.knowledge import BaseConhecimento
    from resumo.parser import extrair_fatos, extrair_processos
    from resumo.render import renderizar

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    [facts] = [extrair_fatos(item) for item in extrair_processos(payload)]
    knowledge = BaseConhecimento.construir(donor_path / "kb")
    context = knowledge.contexto_para(facts)
    output = GeradorTemplate().gerar(facts, context)
    return {
        "provenance": {
            "donor_repository": "oigorbrito/rag_resumo",
            "commit": commit,
            "fixture": FIXTURE.relative_to(ROOT).as_posix(),
            "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "command": "GeradorTemplate().gerar(...) + renderizar(fatos, secoes)",
            "mode": "deterministic template and original renderer; no Anthropic client or network",
        },
        "output": output,
        "document": renderizar(facts, output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--donor-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = capture(args.donor_path)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["provenance"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

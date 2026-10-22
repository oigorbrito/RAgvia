from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_IMPORTS = {
    "celery",
    "redis",
    "rq",
    "langchain",
    "llama_index",
    "llamaindex",
    "pinecone",
    "qdrant_client",
    "weaviate",
}

FORBIDDEN_RUNTIME_TERMS = {
    "hydratask",
    "azure openai sample",
    "legal-rag-engine",
}

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".sql", ".toml", ".yaml", ".yml", ".json", ".md"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS or part.startswith(".") or part.startswith("_") for part in path.parts):
            continue
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            out.append(path)
    return out


def python_import_violations(path: Path) -> list[str]:
    if path.suffix != ".py":
        return []
    try:
        tree = ast.parse(read(path), filename=str(path))
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        for name in names:
            root = name.split(".", 1)[0]
            if root in FORBIDDEN_IMPORTS:
                violations.append(f"forbidden import: {name}")
    return violations


def donor_identity_violations(path: Path) -> list[str]:
    rel = path.relative_to(ROOT)
    if rel == Path("scripts/migration_harness.py"):
        return []
    if path.name in {"AGENTS.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"}:
        return []
    if len(rel.parts) >= 2 and rel.parts[0] == "docs" and rel.parts[1] in {"migrations", "engineering"}:
        return []
    text = read(path).lower()
    return [
        f"donor identity leaked into runtime/project file: {term}"
        for term in FORBIDDEN_RUNTIME_TERMS
        if term in text
    ]


def queue_invariant_violations() -> list[str]:
    path = ROOT / "app" / "queue.py"
    if not path.exists():
        return ["missing app/queue.py"]
    text = read(path).lower()
    compact = re.sub(r"\s+", " ", text)
    errors: list[str] = []
    if "for update skip locked" not in compact:
        errors.append("queue claim must contain FOR UPDATE SKIP LOCKED")
    if "worker_id" not in text:
        errors.append("queue operations must enforce worker ownership")
    if "idempotency_key" not in text:
        errors.append("queue must support idempotent enqueue")
    return errors


def process_durability_violations() -> list[str]:
    processes = ROOT / "app" / "processes.py"
    migration = ROOT / "sql" / "011_finalized_version_immutability.sql"
    errors: list[str] = []
    if not processes.exists():
        return ["missing app/processes.py"]
    if not migration.exists():
        errors.append("missing finalized-version immutability migration")
        return errors

    text = read(processes)
    migration_text = read(migration).lower()
    if "RETURNING id, finalized" not in text:
        errors.append("staging must know whether an existing source version is finalized")
    if 'if not bool(version["finalized"])' not in text:
        errors.append("finalized source retries must not renew process activity")
    if 'if bool(candidate["finalized"]):' not in text:
        errors.append("finalize_version must short-circuit already-finalized versions")
    if "raise exception" not in migration_text or "finalized" not in migration_text:
        errors.append("database must reject mutation of finalized Judit source fields")
    return errors


def rag_invariant_violations() -> list[str]:
    errors: list[str] = []
    rag = ROOT / "app" / "rag.py"
    summary_policy = ROOT / "app" / "summary_policy.py"
    prompts = ROOT / "app" / "prompts.py"
    validation = ROOT / "app" / "validation.py"
    retrieval = ROOT / "app" / "retrieval.py"
    embeddings = ROOT / "app" / "embeddings.py"
    if not rag.exists():
        return ["missing app/rag.py"]
    if not validation.exists():
        return ["missing app/validation.py"]
    if not retrieval.exists():
        return ["missing app/retrieval.py"]

    rag_text = read(rag)
    policy_text = read(summary_policy) if summary_policy.exists() else ""
    validation_text = read(validation)
    retrieval_text = read(retrieval)
    retrieval_compact = re.sub(r"\s+", " ", retrieval_text)
    prompt_text = read(prompts) if prompts.exists() else ""
    if "validar(" not in rag_text:
        errors.append("RAG publishing path must call validar()")
    if 'SONNET_MODEL = "claude-sonnet-5"' not in rag_text:
        errors.append("RAG default generation model must be Claude Sonnet 5")
    if 'OPUS_MODEL = "claude-opus-5"' not in rag_text:
        errors.append("RAG long-process generation model must be Claude Opus 5")
    if "OPUS_STEP_THRESHOLD = 100" not in rag_text:
        errors.append("RAG must select Opus only above 100 total movements")
    if "MAX_TOKENS = 4000" not in rag_text or '"max_tokens": MAX_TOKENS' not in rag_text:
        errors.append("generation requests must pin max_tokens=4000")
    if "REQUESTED_TEMPERATURE = 0.2" not in rag_text:
        errors.append("RAG must preserve the requested temperature 0.2 design intent")
    if "CURRENT_MODELS_SUPPORT_CUSTOM_TEMPERATURE = False" not in rag_text:
        errors.append("current Sonnet/Opus requests must document custom-temperature incompatibility")
    if 'if CURRENT_MODELS_SUPPORT_CUSTOM_TEMPERATURE:' not in rag_text:
        errors.append("custom temperature must only be sent behind the explicit provider capability guard")
    if '"cache_control"' not in rag_text:
        errors.append("system prompt must use Anthropic cache_control")
    if not prompts.exists() or "PROCESS_SUMMARY_SYSTEM_PROMPT" not in prompt_text:
        errors.append("cacheable system prompt must live in app/prompts.py")
    if "secrecy_level" not in rag_text or 'base["secrecy_level"] > 0' not in rag_text:
        errors.append("secret cases must be truncated before generation")
    if not summary_policy.exists():
        errors.append("missing app/summary_policy.py")
    elif "RESTRICTED_MODEL = \"local-deterministic\"" not in policy_text:
        errors.append("restricted cases must retain deterministic local generation")
    if "class\\s*=" not in validation_text:
        errors.append("validator must reject class= in JSX")
    if "LEXICAL_WEIGHT = 0.5" not in retrieval_text or "VECTOR_WEIGHT = 0.5" not in retrieval_text:
        errors.append("long retrieval must preserve 0.5 lexical / 0.5 vector weighting")
    expected_weighted_score = (
        "LEXICAL_WEIGHT * lexical.get(step.id, 0.0) + "
        "VECTOR_WEIGHT * vector.get(step.id, 0.0)"
    )
    if expected_weighted_score not in retrieval_compact:
        errors.append("long retrieval score must use the configured lexical/vector weights")
    if "async def lexical_search(" not in retrieval_text:
        errors.append("long retrieval must expose PostgreSQL lexical search")
    if "to_tsvector('portuguese'" not in retrieval_text or "websearch_to_tsquery('portuguese'" not in retrieval_text:
        errors.append("PostgreSQL lexical retrieval must use Portuguese text search")
    if "lexical_scores = await lexical_search(" in rag_text:
        errors.append("final RAG ranking must not use PostgreSQL ts_rank_cd lexical scores")
    if "lexical = bm25" not in retrieval_text:
        errors.append("long retrieval must use literal BM25 as the final lexical signal")
    if "len(steps) > 40" in rag_text and not embeddings.exists():
        errors.append("conditional vector retrieval requires app/embeddings.py")
    return errors


def claim_verification_invariant_violations() -> list[str]:
    verification = ROOT / "app" / "claim_verification.py"
    evidence = ROOT / "app" / "claim_evidence.py"
    rag = ROOT / "app" / "rag.py"
    migration = ROOT / "sql" / "033_claim_semantic_verification.sql"
    engineering_doc = ROOT / "docs" / "engineering" / "rag-claim-verification.md"
    migration_doc = ROOT / "docs" / "migrations" / "claim-semantic-verification.md"
    unit_tests = ROOT / "tests" / "test_claim_verification.py"
    persistence_tests = ROOT / "tests" / "integration" / "test_validation_persistence_postgres.py"
    secrecy_tests = ROOT / "tests" / "integration" / "test_pipeline_secrecy_e2e.py"

    required = (
        verification,
        evidence,
        rag,
        migration,
        engineering_doc,
        migration_doc,
        unit_tests,
        persistence_tests,
        secrecy_tests,
    )
    missing = [f"missing {path.relative_to(ROOT)}" for path in required if not path.exists()]
    if missing:
        return missing

    verification_text = read(verification)
    evidence_text = read(evidence)
    rag_text = read(rag)
    migration_text = read(migration)
    unit_text = read(unit_tests)
    persistence_text = read(persistence_tests)
    secrecy_text = read(secrecy_tests)

    errors: list[str] = []
    for status in ("supported", "contradicted", "insufficient", "not_evaluated"):
        if status not in verification_text or status not in migration_text:
            errors.append(f"claim verification status contract missing: {status}")

    if "verification_source_unavailable" not in verification_text:
        errors.append("missing explicit unavailable-evidence verification result")
    if "claim contradicted by cited evidence" not in verification_text:
        errors.append("contradicted claims must fail validation")
    if "claim has insufficient cited evidence" not in verification_text:
        errors.append("insufficient material claims must enter correction/failure handling")
    if "claim_evidence_is_publishable" not in evidence_text or "claim_evidence_is_publishable" not in rag_text:
        errors.append("RAG publication must be gated by claim evidence")
    if "process_summary_claim_evidence_excerpts" not in migration_text:
        errors.append("literal verification excerpts must have a dedicated audit table")
    if "evidence_excerpt_sha256" not in migration_text:
        errors.append("verification relations must persist excerpt hashes")

    required_unit_cases = (
        "test_process_amount_mismatch_is_contradicted",
        "test_missing_deterministic_date_is_insufficient_and_retryable",
        "test_unanchored_intrinsically_material_claim_is_retryable",
        "test_attachment_verification_preserves_available_positions_and_excerpt_hash",
    )
    for test_name in required_unit_cases:
        if test_name not in unit_text:
            errors.append(f"missing claim-verification regression test: {test_name}")

    if "test_second_invalid_generation_is_persisted_as_failed_validation" not in persistence_text:
        errors.append("second failed validation must be persisted explicitly")
    if "local-deterministic" not in secrecy_text or "is_restricted_local_summary" not in secrecy_text:
        errors.append("restricted-process pipeline must remain deterministic and provider-free")

    return errors


def embedding_invariant_violations() -> list[str]:
    embeddings = ROOT / "app" / "embeddings.py"
    schema = ROOT / "sql" / "002_process_data.sql"
    errors: list[str] = []
    if not embeddings.exists():
        return ["missing app/embeddings.py"]
    if not schema.exists():
        return ["missing sql/002_process_data.sql"]

    embedding_text = read(embeddings)
    schema_text = read(schema).lower()
    if "VECTOR_DIMENSIONS = 1536" not in embedding_text:
        errors.append("embedding provider must stay aligned to vector(1536)")
    if "embedding vector(1536)" not in schema_text:
        errors.append("process_steps embedding column must remain vector(1536)")
    if 'request["dimensions"] = VECTOR_DIMENSIONS' not in embedding_text:
        errors.append("text-embedding-3 requests must pin output dimensions")
    return errors


def webhook_invariant_violations() -> list[str]:
    api = ROOT / "app" / "api.py"
    judit = ROOT / "app" / "judit.py"
    judit_tasks = ROOT / "app" / "judit_tasks.py"
    errors: list[str] = []
    if not api.exists():
        return ["missing app/api.py"]
    if not judit.exists():
        return ["missing app/judit.py"]
    if not judit_tasks.exists():
        return ["missing app/judit_tasks.py"]

    api_text = read(api)
    judit_text = read(judit)
    task_text = read(judit_tasks)
    if "status_code=404" not in api_text:
        errors.append("invalid webhook token must return 404")
    if 'task_name="finalize_judit_request"' not in api_text:
        errors.append("request_completed must enqueue finalization instead of doing heavy work inline")
    if "finalize_version(" in api_text:
        errors.append("webhook API must not finalize process data inline")
    if "callback_id" not in api_text or "judit_deliveries" not in api_text:
        errors.append("webhook deliveries must be persisted/idempotent by callback_id")
    if 'event_type == "response_created"' not in judit_text:
        errors.append("Judit adapter must understand response_created envelope")
    if 'self.event_type == "request_completed"' not in judit_text:
        errors.append("Judit adapter must understand request_completed envelope")
    if "cached_response" not in judit_text or "tags" not in judit_text:
        errors.append("Judit adapter must read cached_response from current payload/tags shape")
    if "source_cached_response" not in task_text:
        errors.append("finalizer must suppress LLM enqueue for cached-only responses")
    return errors


def deployment_invariant_violations() -> list[str]:
    compose = ROOT / "compose.production.yaml"
    dockerfile = ROOT / "Dockerfile"
    constraints = ROOT / "requirements" / "constraints.txt"
    validator = ROOT / "scripts" / "validate_production_compose.py"
    restore_drill = ROOT / "scripts" / "verify_backup_restore.sh"
    errors: list[str] = []
    for path in (compose, dockerfile, constraints, validator, restore_drill):
        if not path.exists():
            errors.append(f"missing {path.relative_to(ROOT)}")
    if errors:
        return errors

    compose_text = read(compose)
    docker_text = read(dockerfile)
    validator_text = read(validator)
    if "${RPY_IMAGE:?" not in compose_text:
        errors.append("production application services must require RPY_IMAGE")
    if "@sha256:" not in compose_text:
        errors.append("production service images must include immutable digests")
    if not re.search(r"^FROM\s+\S+@sha256:[0-9a-fA-F]{64}\s*$", docker_text, re.MULTILINE):
        errors.append("Dockerfile base image must be pinned by sha256 digest")
    if "--constraint" not in docker_text:
        errors.append("Docker build must install Python dependencies under the constraints lock")
    if "IMMUTABLE_IMAGE_RE" not in validator_text:
        errors.append("production compose validator must enforce immutable image references")
    for required in (
        "read_only: true",
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "provider-gateway:",
        "EGRESS_PROXY_ALLOWED_HOSTS",
    ):
        if required not in compose_text:
            errors.append(f"production compose missing runtime/egress invariant: {required}")
    if "_validate_runtime_confinement" not in validator_text:
        errors.append("production compose validator must enforce runtime confinement")
    if "_validate_egress_topology" not in validator_text:
        errors.append("production compose validator must enforce egress topology")
    if "seccomp=unconfined" not in validator_text:
        errors.append("production compose validator must reject seccomp opt-out")
    return errors


def documentation_invariant_violations() -> list[str]:
    required = [
        ROOT / "AGENTS.md",
        ROOT / "docs" / "engineering" / "empirical-engineering.md",
        ROOT / "docs" / "deployment" / "production.md",
        ROOT / "docs" / "deployment" / "backup-restore.md",
    ]
    return [f"missing {path.relative_to(ROOT)}" for path in required if not path.exists()]


def dependency_violations() -> list[str]:
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return ["missing pyproject.toml"]
    text = read(pyproject).lower()
    return [
        f"forbidden dependency declared: {name}"
        for name in FORBIDDEN_IMPORTS
        if re.search(rf"(^|[\"']){re.escape(name)}([\[<>=\"']|$)", text)
    ]


def main() -> int:
    files = iter_files()
    violations: list[tuple[str, str]] = []

    for path in files:
        rel = str(path.relative_to(ROOT))
        for msg in python_import_violations(path):
            violations.append((rel, msg))
        for msg in donor_identity_violations(path):
            violations.append((rel, msg))

    checks = [
        ("pyproject.toml", dependency_violations),
        ("app/queue.py", queue_invariant_violations),
        ("app/processes.py", process_durability_violations),
        ("app/rag.py", rag_invariant_violations),
        ("app/claim_verification.py", claim_verification_invariant_violations),
        ("app/embeddings.py", embedding_invariant_violations),
        ("app/api.py", webhook_invariant_violations),
        ("compose.production.yaml", deployment_invariant_violations),
        ("docs/engineering/empirical-engineering.md", documentation_invariant_violations),
    ]
    for label, check in checks:
        for msg in check():
            violations.append((label, msg))

    if violations:
        print("Migration harness: FAILED")
        for path, msg in violations:
            print(f" - {path}: {msg}")
        return 1

    print(f"Migration harness: OK ({len(files)} project files checked; durable invariants verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

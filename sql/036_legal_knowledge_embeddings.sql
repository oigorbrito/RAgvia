CREATE TABLE IF NOT EXISTS legal_knowledge_embeddings (
    entry_id TEXT NOT NULL CHECK (length(trim(entry_id)) > 0),
    embedding_content_sha256 TEXT NOT NULL CHECK (embedding_content_sha256 ~ '^[0-9a-f]{64}$'),
    provider TEXT NOT NULL CHECK (provider = 'bge'),
    model TEXT NOT NULL CHECK (model = 'BAAI/bge-m3'),
    dimensions INTEGER NOT NULL CHECK (dimensions = 1024),
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entry_id, embedding_content_sha256, provider, model)
);

CREATE INDEX IF NOT EXISTS idx_legal_knowledge_embeddings_bge_m3_hnsw
    ON legal_knowledge_embeddings USING hnsw (embedding vector_cosine_ops)
    WHERE provider = 'bge' AND model = 'BAAI/bge-m3';

ALTER TABLE process_summary_glossary_sources
    DROP CONSTRAINT IF EXISTS process_summary_glossary_sources_kind_check;
ALTER TABLE process_summary_glossary_sources
    ADD CONSTRAINT process_summary_glossary_sources_kind_check
    CHECK (kind IN ('class', 'subject', 'movement', 'concept'));

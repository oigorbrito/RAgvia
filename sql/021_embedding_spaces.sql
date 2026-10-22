CREATE TABLE IF NOT EXISTS process_step_embeddings (
    step_id UUID NOT NULL REFERENCES process_steps(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('bge', 'cohere')),
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions = 1024),
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (step_id, provider, model)
);

CREATE INDEX IF NOT EXISTS idx_process_step_embeddings_space
    ON process_step_embeddings (provider, model, step_id);

-- Each semantic space has its own ANN index. Never run an ANN query across
-- providers/models even when their physical vector dimensions happen to match.
CREATE INDEX IF NOT EXISTS idx_process_step_embeddings_bge_m3_hnsw
    ON process_step_embeddings USING hnsw (embedding vector_cosine_ops)
    WHERE provider = 'bge' AND model = 'BAAI/bge-m3';

CREATE INDEX IF NOT EXISTS idx_process_step_embeddings_cohere_v4_hnsw
    ON process_step_embeddings USING hnsw (embedding vector_cosine_ops)
    WHERE provider = 'cohere' AND model = 'embed-v4.0';

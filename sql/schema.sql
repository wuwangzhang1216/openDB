CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- files: one row per uploaded file
-- ============================================================
CREATE TABLE files (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename        TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    file_size       BIGINT NOT NULL,
    file_path       TEXT NOT NULL,           -- path on local filesystem
    checksum        TEXT NOT NULL,           -- SHA-256
    status          TEXT NOT NULL DEFAULT 'processing',
                    -- 'processing' | 'ready' | 'failed'
    error_message   TEXT,
    tags            TEXT[] DEFAULT '{}',
    metadata        JSONB DEFAULT '{}',      -- user-provided + auto-extracted
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_tags ON files USING GIN(tags);
CREATE INDEX idx_files_metadata ON files USING GIN(metadata jsonb_path_ops);
CREATE INDEX idx_files_filename ON files USING GIN(filename gin_trgm_ops);
CREATE INDEX idx_files_created ON files(created_at DESC);
CREATE UNIQUE INDEX idx_files_checksum_ready ON files(checksum) WHERE status = 'ready';

-- ============================================================
-- file_text: pre-assembled plain text for /read
-- ============================================================
CREATE TABLE file_text (
    file_id         UUID PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    full_text       TEXT NOT NULL,
    total_lines     INT NOT NULL,
    line_index      INT[] NOT NULL DEFAULT '{}',
    toc             TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- pages: page-level chunks for full-text search
-- ============================================================
CREATE TABLE pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id         UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page_number     INT NOT NULL,
    section_title   TEXT,
    content_type    TEXT DEFAULT 'text',  -- 'text' | 'table' | 'note'
    text            TEXT NOT NULL,
    line_start      INT NOT NULL,
    line_end        INT NOT NULL,
    tsv             TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('english', text)
                    ) STORED,
    text_jieba      TEXT,                -- jieba-tokenized text for CJK FTS
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pages_file ON pages(file_id, page_number);
CREATE INDEX idx_pages_tsv ON pages USING GIN(tsv);
CREATE INDEX idx_pages_trgm ON pages USING GIN(text gin_trgm_ops);
CREATE INDEX idx_pages_jieba ON pages USING GIN(
    to_tsvector('simple', COALESCE(text_jieba, ''))
);

-- ============================================================
-- Auto-update timestamp
-- ============================================================
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER files_updated
    BEFORE UPDATE ON files
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- ============================================================
-- memories: agent memory entries
-- ============================================================
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content         TEXT NOT NULL,
    memory_type     TEXT NOT NULL DEFAULT 'semantic',
                    -- 'episodic' | 'semantic' | 'procedural'
    pinned          BOOLEAN NOT NULL DEFAULT false,
    source          TEXT NOT NULL DEFAULT 'unknown',
                    -- 'user_explicit' | 'ai_inference' | 'tool_extraction' | 'unknown'
    superseded_id   UUID DEFAULT NULL,
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    last_accessed   TIMESTAMPTZ DEFAULT NULL,
    access_count    INTEGER NOT NULL DEFAULT 0,
    workspace_id    TEXT NOT NULL DEFAULT '_default',
    content_jieba   TEXT,                -- jieba-tokenized content for CJK FTS
    tags            TEXT[] DEFAULT '{}',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_memories_type ON memories(memory_type);
CREATE INDEX idx_memories_created ON memories(created_at DESC);
CREATE INDEX idx_memories_tags ON memories USING GIN(tags);
CREATE INDEX idx_memories_workspace ON memories(workspace_id);
CREATE INDEX idx_memories_confidence ON memories(confidence) WHERE confidence >= 0.3;
CREATE INDEX idx_memories_tsv ON memories USING GIN(
    to_tsvector('english', content)
);
CREATE INDEX idx_memories_jieba ON memories USING GIN(
    to_tsvector('simple', COALESCE(content_jieba, ''))
);

CREATE TRIGGER memories_updated
    BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- ============================================================
-- eval_captures: opt-in real query capture for offline evals
-- ============================================================
CREATE TABLE eval_captures (
    id            BIGSERIAL PRIMARY KEY,
    tool_name     TEXT NOT NULL CHECK (tool_name IN ('search', 'memory_recall')),
    query         TEXT NOT NULL CHECK (length(query) <= 51200),
    result_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_count  INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_eval_captures_created ON eval_captures(created_at DESC);
CREATE INDEX idx_eval_captures_tool ON eval_captures(tool_name);

-- ============================================================
-- file_links: deterministic lightweight link graph between indexed files
-- ============================================================
CREATE TABLE file_links (
    id            BIGSERIAL PRIMARY KEY,
    from_file_id  UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    to_file_id    UUID REFERENCES files(id) ON DELETE SET NULL,
    target        TEXT NOT NULL,
    link_type     TEXT NOT NULL DEFAULT 'reference',
    context       TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(from_file_id, target, link_type)
);

CREATE INDEX idx_file_links_from ON file_links(from_file_id);
CREATE INDEX idx_file_links_to ON file_links(to_file_id);
CREATE INDEX idx_file_links_target ON file_links(target);

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
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pages_file ON pages(file_id, page_number);
CREATE INDEX idx_pages_tsv ON pages USING GIN(tsv);
CREATE INDEX idx_pages_trgm ON pages USING GIN(text gin_trgm_ops);

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

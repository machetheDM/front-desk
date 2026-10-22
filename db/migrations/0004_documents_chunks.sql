SET search_path = app, extensions, pg_catalog;

CREATE TABLE app.documents (
  tenant_id     uuid NOT NULL REFERENCES app.tenants(tenant_id),
  document_id   uuid NOT NULL DEFAULT gen_random_uuid(),
  title         text NOT NULL,
  source_uri    text NOT NULL,
  content_sha256 text NOT NULL,
  version       int NOT NULL DEFAULT 1,
  -- Only approved documents are retrievable. Loading a file does not approve it.
  approved      boolean NOT NULL DEFAULT false,
  approved_by   text,
  approved_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, document_id),
  UNIQUE (tenant_id, source_uri, version)
);
ALTER TABLE app.documents OWNER TO frontdesk_owner;
ALTER TABLE app.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.documents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.documents
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.documents TO frontdesk_app;

-- Chunks and their embeddings live in the same row, behind the same policy. A separate
-- vector store would need its own isolation proof; here there is one.
CREATE TABLE app.chunks (
  tenant_id    uuid NOT NULL REFERENCES app.tenants(tenant_id),
  chunk_id     uuid NOT NULL DEFAULT gen_random_uuid(),
  document_id  uuid NOT NULL,
  ordinal      int NOT NULL,
  heading      text NOT NULL DEFAULT '',
  text         text NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (
                 setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
                 setweight(to_tsvector('english', text), 'B')) STORED,
  embedding    vector(1024),
  embedder     text NOT NULL,
  -- Set when a chunk came from a customer conversation (an approved staff answer promoted to
  -- knowledge). POPIA deletion removes these chunks and their embeddings.
  source_customer_id uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, chunk_id),
  FOREIGN KEY (tenant_id, document_id) REFERENCES app.documents(tenant_id, document_id)
);
CREATE INDEX chunks_tsv ON app.chunks USING gin (tsv);
CREATE INDEX chunks_tenant ON app.chunks (tenant_id, document_id);
-- HNSW over all tenants. RLS filters after the index scan, so for a tenant that is a small
-- slice of the table the index can return too few rows. On Aurora (pgvector >= 0.8) the
-- retrieval code sets hnsw.iterative_scan = relaxed_order; at pilot size the planner uses
-- the tenant index and an exact scan anyway. Recall is measured, not assumed.
CREATE INDEX chunks_embedding ON app.chunks USING hnsw (embedding vector_cosine_ops);
ALTER TABLE app.chunks OWNER TO frontdesk_owner;
ALTER TABLE app.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.chunks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.chunks
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.chunks TO frontdesk_app;

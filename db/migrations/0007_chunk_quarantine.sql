-- Indirect prompt injection defence: a chunk whose text reads like instructions to an AI (or
-- carries links) is quarantined at ingestion. Quarantined chunks are never retrieved until a
-- person reviews the document. See frontdesk/retrieval/quarantine.py and docs/AI-SECURITY.md.
ALTER TABLE app.chunks ADD COLUMN quarantined boolean NOT NULL DEFAULT false;
ALTER TABLE app.chunks ADD COLUMN quarantine_reason text;

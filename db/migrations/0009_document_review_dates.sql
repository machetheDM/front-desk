-- A date by which each approved document must be checked again. A correct citation of an
-- out-of-date price list is still a wrong answer (docs/AI-SECURITY.md, LLM09), so the owner's
-- insights list documents that are due. Loading a document without one sets six months.
ALTER TABLE app.documents ADD COLUMN review_by date;

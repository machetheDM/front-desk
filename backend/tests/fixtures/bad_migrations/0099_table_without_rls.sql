-- FAILING EXAMPLE, kept on purpose: a business table with tenant_id but no RLS.
-- tests/test_migration_lint.py asserts the linter and the migration runner both reject it.
CREATE TABLE app.invoices (
  tenant_id uuid NOT NULL,
  invoice_id uuid PRIMARY KEY,
  amount_cents bigint NOT NULL
);
GRANT SELECT, INSERT ON app.invoices TO frontdesk_app;

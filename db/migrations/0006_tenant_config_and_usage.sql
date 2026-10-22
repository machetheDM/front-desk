-- Structured, approved facts the typed tools read (service zones, office hours). Tools look
-- these up exactly instead of letting the model paraphrase a document.
CREATE TABLE app.tenant_config (
  tenant_id   uuid NOT NULL REFERENCES app.tenants(tenant_id),
  key         text NOT NULL,
  value       jsonb NOT NULL,
  approved_by text NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, key)
);
ALTER TABLE app.tenant_config OWNER TO frontdesk_owner;
ALTER TABLE app.tenant_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.tenant_config FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.tenant_config
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.tenant_config TO frontdesk_app;

-- Model usage per tenant per month. Claude on Bedrock bills through AWS Marketplace, which
-- a service-filtered budget misses, so the app meters and caps spend itself.
CREATE TABLE app.usage_meter (
  tenant_id     uuid NOT NULL REFERENCES app.tenants(tenant_id),
  month         date NOT NULL,
  model         text NOT NULL,
  calls         int NOT NULL DEFAULT 0,
  input_tokens  bigint NOT NULL DEFAULT 0,
  output_tokens bigint NOT NULL DEFAULT 0,
  cached_tokens bigint NOT NULL DEFAULT 0,
  cost_usd_micros bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, month, model)
);
ALTER TABLE app.usage_meter OWNER TO frontdesk_owner;
ALTER TABLE app.usage_meter ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.usage_meter FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.usage_meter
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.usage_meter TO frontdesk_app;

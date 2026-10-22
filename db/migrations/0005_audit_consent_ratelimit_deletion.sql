-- Audit reads, not only writes. "Who read this customer's conversation" must have an answer.
CREATE TABLE app.audit_log (
  tenant_id     uuid NOT NULL REFERENCES app.tenants(tenant_id),
  audit_id      bigint GENERATED ALWAYS AS IDENTITY,
  actor_type    text NOT NULL CHECK (actor_type IN ('staff', 'assistant', 'system', 'operator')),
  actor_id      text NOT NULL,
  action        text NOT NULL CHECK (action IN ('read', 'write', 'delete', 'export', 'login')),
  resource_type text NOT NULL,
  resource_id   text NOT NULL,
  -- Identifiers and counts only, never message text.
  detail        jsonb NOT NULL DEFAULT '{}',
  at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, audit_id)
);
CREATE INDEX audit_by_resource ON app.audit_log (tenant_id, resource_type, resource_id, at);
CREATE INDEX audit_by_actor ON app.audit_log (tenant_id, actor_id, at);
ALTER TABLE app.audit_log OWNER TO frontdesk_owner;
ALTER TABLE app.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.audit_log
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON app.audit_log
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
-- TRUNCATE skips row triggers, so it is revoked explicitly as well.
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON app.audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION app.reject_mutation();
GRANT SELECT, INSERT ON app.audit_log TO frontdesk_app;

-- Two kinds of consent, stored as separate records (POPIA s69). Being answered is not
-- permission to be marketed to. Append-only: the latest record per (customer, purpose) wins.
CREATE TABLE app.consents (
  tenant_id    uuid NOT NULL REFERENCES app.tenants(tenant_id),
  consent_id   bigint GENERATED ALWAYS AS IDENTITY,
  customer_id  uuid NOT NULL,
  purpose      text NOT NULL CHECK (purpose IN ('service', 'marketing')),
  granted      boolean NOT NULL,
  source       text NOT NULL,
  evidence     text NOT NULL DEFAULT '',
  recorded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, consent_id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES app.customers(tenant_id, customer_id)
);
ALTER TABLE app.consents OWNER TO frontdesk_owner;
ALTER TABLE app.consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.consents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.consents
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
CREATE TRIGGER consents_append_only BEFORE UPDATE OR DELETE ON app.consents
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
GRANT SELECT, INSERT ON app.consents TO frontdesk_app;

CREATE TABLE app.rate_limits (
  tenant_id    uuid NOT NULL REFERENCES app.tenants(tenant_id),
  bucket_key   text NOT NULL,
  window_start timestamptz NOT NULL,
  hits         int NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, bucket_key, window_start)
);
ALTER TABLE app.rate_limits OWNER TO frontdesk_owner;
ALTER TABLE app.rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.rate_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.rate_limits
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.rate_limits TO frontdesk_app;

CREATE TABLE app.deletion_requests (
  tenant_id    uuid NOT NULL REFERENCES app.tenants(tenant_id),
  request_id   uuid NOT NULL DEFAULT gen_random_uuid(),
  customer_id  uuid NOT NULL,
  requested_by text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  report       jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, request_id)
);
ALTER TABLE app.deletion_requests OWNER TO frontdesk_owner;
ALTER TABLE app.deletion_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.deletion_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.deletion_requests
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.deletion_requests TO frontdesk_app;

CREATE TABLE app.tenants (
  tenant_id   uuid NOT NULL PRIMARY KEY,
  name        text NOT NULL,
  slug        text NOT NULL UNIQUE,
  -- Demo tenants hold made-up data. Real-tenant code paths refuse to read from them
  -- (frontdesk/tenancy.py) and a test asserts demo data never lands in a real tenant.
  is_demo     boolean NOT NULL DEFAULT false,
  timezone    text NOT NULL DEFAULT 'Africa/Johannesburg',
  assistant_name text NOT NULL DEFAULT 'Front Desk assistant',
  created_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE app.tenants OWNER TO frontdesk_owner;
ALTER TABLE app.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.tenants
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.tenants TO frontdesk_app;

CREATE TABLE app.staff (
  tenant_id    uuid NOT NULL REFERENCES app.tenants(tenant_id),
  staff_id     uuid NOT NULL DEFAULT gen_random_uuid(),
  display_name text NOT NULL,
  email        text NOT NULL,
  cognito_sub  text,
  role         text NOT NULL CHECK (role IN ('agent', 'owner')),
  active       boolean NOT NULL DEFAULT true,
  -- Used to pick who a handoff goes to; the handoff always names a person.
  handles_topics text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, staff_id),
  UNIQUE (tenant_id, email)
);
ALTER TABLE app.staff OWNER TO frontdesk_owner;
ALTER TABLE app.staff ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.staff FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.staff
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.staff TO frontdesk_app;

CREATE TABLE app.customers (
  tenant_id       uuid NOT NULL REFERENCES app.tenants(tenant_id),
  customer_id     uuid NOT NULL DEFAULT gen_random_uuid(),
  channel         text NOT NULL CHECK (channel IN ('whatsapp', 'web', 'replay')),
  -- E.164 number for WhatsApp, session id for web. Needed to reply; wiped on deletion.
  channel_address text,
  -- The name exactly as the customer gave it. No title, no pronoun is ever derived from it.
  display_name    text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz,
  PRIMARY KEY (tenant_id, customer_id)
);
CREATE UNIQUE INDEX customers_channel_address ON app.customers (tenant_id, channel, channel_address)
  WHERE channel_address IS NOT NULL;
ALTER TABLE app.customers OWNER TO frontdesk_owner;
ALTER TABLE app.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.customers FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.customers
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.customers TO frontdesk_app;

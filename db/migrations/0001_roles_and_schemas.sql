-- Roles and schemas.
-- frontdesk_owner owns every object and is used only by migrations.
-- frontdesk_app is what Lambdas log in as. It owns nothing and cannot bypass RLS, so even a
-- SQL injection in the app is still confined to the tenant set in app.tenant_id. If the app
-- logged in as the owner, a forgotten FORCE ROW LEVEL SECURITY would silently disable isolation.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'frontdesk_owner') THEN
    CREATE ROLE frontdesk_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'frontdesk_app') THEN
    CREATE ROLE frontdesk_app LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- The migrating user (Aurora master, or postgres locally) must be able to hand objects to
-- frontdesk_owner. On Postgres 16 that requires membership.
GRANT frontdesk_owner TO CURRENT_USER;

CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION frontdesk_owner;
-- Extensions get their own schema. `public` is closed to the app role, so nothing it can
-- create there can shadow a function or operator the app relies on.
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO frontdesk_app;
GRANT USAGE ON SCHEMA extensions TO frontdesk_app;
-- Fixed search path for the app role: pgvector's type and operators resolve, nothing else.
ALTER ROLE frontdesk_app SET search_path = app, extensions, pg_catalog;

-- The tenant context. Missing or empty means "no tenant": every policy compares against NULL
-- and matches nothing, so forgetting to set it fails closed rather than open.
CREATE OR REPLACE FUNCTION app.current_tenant() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;
ALTER FUNCTION app.current_tenant() OWNER TO frontdesk_owner;
GRANT EXECUTE ON FUNCTION app.current_tenant() TO frontdesk_app;

-- Shared trigger for append-only tables (audit_log, conversation_events, consents).
CREATE OR REPLACE FUNCTION app.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only table %: % is not allowed', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$;
ALTER FUNCTION app.reject_mutation() OWNER TO frontdesk_owner;

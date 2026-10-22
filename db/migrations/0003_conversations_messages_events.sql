CREATE TABLE app.conversations (
  tenant_id        uuid NOT NULL REFERENCES app.tenants(tenant_id),
  conversation_id  uuid NOT NULL DEFAULT gen_random_uuid(),
  customer_id      uuid NOT NULL,
  channel          text NOT NULL CHECK (channel IN ('whatsapp', 'web', 'replay')),
  -- A cache of the latest state for the inbox list. The truth is conversation_events;
  -- this column is rebuilt from events by frontdesk/events.py and never read by analytics.
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'handed_over', 'resolved')),
  assigned_staff_id uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, conversation_id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES app.customers(tenant_id, customer_id)
);
ALTER TABLE app.conversations OWNER TO frontdesk_owner;
ALTER TABLE app.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.conversations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.conversations
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.conversations TO frontdesk_app;

CREATE TABLE app.messages (
  tenant_id       uuid NOT NULL REFERENCES app.tenants(tenant_id),
  message_id      uuid NOT NULL DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL,
  direction       text NOT NULL CHECK (direction IN ('in', 'out')),
  author          text NOT NULL CHECK (author IN ('customer', 'assistant', 'staff', 'system')),
  staff_id        uuid,
  -- What the customer wrote, shown to staff only.
  body            text NOT NULL,
  -- What the model was allowed to see. Kept so the transcript can show the redaction.
  body_redacted   text,
  redactions      jsonb NOT NULL DEFAULT '[]',
  channel_message_id text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, message_id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES app.conversations(tenant_id, conversation_id)
);
CREATE UNIQUE INDEX messages_channel_dedupe ON app.messages (tenant_id, channel_message_id)
  WHERE channel_message_id IS NOT NULL;
ALTER TABLE app.messages OWNER TO frontdesk_owner;
ALTER TABLE app.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.messages FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.messages
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
-- UPDATE is granted only so POPIA deletion can overwrite bodies (frontdesk/deletion.py).
GRANT SELECT, INSERT, UPDATE ON app.messages TO frontdesk_app;

-- Conversations are events. Dashboards are derived from this table, never stored as truth.
-- Payloads hold ids, scores and decisions, never message text, so the stream survives a
-- POPIA deletion intact without retaining personal information.
CREATE TABLE app.conversation_events (
  tenant_id       uuid NOT NULL REFERENCES app.tenants(tenant_id),
  event_id        bigint GENERATED ALWAYS AS IDENTITY,
  conversation_id uuid NOT NULL,
  type            text NOT NULL CHECK (type IN (
                    'message_received', 'answer_sent', 'handed_over', 'staff_replied',
                    'resolved', 'degraded', 'refused', 'opted_out', 'rate_limited')),
  payload         jsonb NOT NULL DEFAULT '{}',
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, event_id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES app.conversations(tenant_id, conversation_id),
  CONSTRAINT payload_has_no_text CHECK (NOT (payload ? 'body') AND NOT (payload ? 'text'))
);
CREATE INDEX conversation_events_by_time ON app.conversation_events (tenant_id, occurred_at);
ALTER TABLE app.conversation_events OWNER TO frontdesk_owner;
ALTER TABLE app.conversation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.conversation_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.conversation_events
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
CREATE TRIGGER conversation_events_append_only BEFORE UPDATE OR DELETE ON app.conversation_events
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
GRANT SELECT, INSERT ON app.conversation_events TO frontdesk_app;

CREATE TABLE app.handoffs (
  tenant_id         uuid NOT NULL REFERENCES app.tenants(tenant_id),
  handoff_id        uuid NOT NULL DEFAULT gen_random_uuid(),
  conversation_id   uuid NOT NULL,
  reason            text NOT NULL,
  -- Three lines for the staff member, written from redacted text.
  summary           text NOT NULL,
  assigned_staff_id uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  resolved_at       timestamptz,
  PRIMARY KEY (tenant_id, handoff_id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES app.conversations(tenant_id, conversation_id),
  FOREIGN KEY (tenant_id, assigned_staff_id) REFERENCES app.staff(tenant_id, staff_id)
);
ALTER TABLE app.handoffs OWNER TO frontdesk_owner;
ALTER TABLE app.handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.handoffs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.handoffs
  USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant());
GRANT SELECT, INSERT, UPDATE ON app.handoffs TO frontdesk_app;

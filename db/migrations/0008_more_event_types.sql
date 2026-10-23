-- New conversation events: explicit marketing opt-in, and a staff-sent WhatsApp template
-- (needed outside Meta's 24-hour window). Added as a new migration: applied migrations are
-- never edited (frontdesk/db/migrate.py refuses).
ALTER TABLE app.conversation_events DROP CONSTRAINT conversation_events_type_check;
ALTER TABLE app.conversation_events ADD CONSTRAINT conversation_events_type_check CHECK (type IN (
  'message_received', 'answer_sent', 'handed_over', 'staff_replied', 'resolved', 'degraded',
  'refused', 'opted_out', 'rate_limited', 'opted_in', 'template_sent'));

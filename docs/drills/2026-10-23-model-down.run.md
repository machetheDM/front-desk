# Drill: model down, front desk open (23 October 2026)

Run: `python -m frontdesk.drills model-down` against **local Postgres**, demo tenant only, at 04:23 UTC.
Result: **10 of 10 steps passed**.

| # | Step | Must happen | What happened | Pass | ms |
|---|---|---|---|---|---|
| 1 | A price question while Bedrock throttles | customer told a person will reply; conversation assigned to a named person | action=degraded; staff=Naledi Mokoena; reply starts 'Thanks for your message. A person from Bushveld Solar & Electrical wil' | yes | 162.0 |
| 2 | A request for a person while Bedrock throttles | the rules hand over as normal: no model needed | action=handoff; reason=human_requested | yes | 41.5 |
| 3 | STOP while Bedrock throttles | the opt-out is honoured without a model | action=opted_out | yes | 77.3 |
| 4 | WhatsApp redelivers the same message during the outage | answered once, not twice | first proceed=True; retry proceed=False | yes | 44.4 |
| 5 | The answer Lambda times out outright | the workflow's Catch still replies and hands over | Catch ran degrade: action=degraded; staff=Naledi Mokoena | yes | 51.6 |
| 6 | Staff open the inbox | every affected conversation is in the handoff queue with a summary | 3 of 3 drill conversations in the queue, 3 with a summary | yes | 20.9 |
| 7 | A person answers and resolves | the reply goes out on WhatsApp and the conversation closes | reply 200, sent via ['whatsapp'], resolve 200 | yes | 37.4 |
| 8 | The owner checks the dashboard | insights still compute (they read events, not the model) | owner summary computed from events: 5 handed to staff, 4 because the model was down | yes | 9.0 |
| 9 | Operations is told | the Degraded metric fires so DET-06 pages the operator | 4 Degraded metric points emitted (DET-06 alarms on this metric) | yes | 0.0 |
| 10 | Bedrock recovers | the next message is answered with a citation; nothing to reset by hand | action=answer; citations=1 | yes | 40.2 |

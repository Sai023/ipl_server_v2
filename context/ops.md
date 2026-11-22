# ops — the clocks, the Ops status, the admin alerts (Phase 2 of the SA20 readiness plan)

Files: [logic/ops_plan.py](../logic/ops_plan.py) (pure rules), [ops_notify.py](../ops_notify.py) (Telegram), the ops section of [routes.py](../routes.py), four methods in [db_manager.py](../db_manager.py), the clock thread in [server.py](../server.py). Tests: [tests/test_ops_plan.py](../tests/test_ops_plan.py), [tests/test_ops_notify.py](../tests/test_ops_notify.py), [tests/test_ops_core.py](../tests/test_ops_core.py) (three scenarios on the real routes).

## What it does (business view)

The weekly handover (each member's draft becomes the XI that plays the week) is already safe to repeat: `db.reconcile_competition` is one all-or-nothing transaction per week under a row lock, recorded once in the ledger (`rollover_log`), so it does not matter how many things call it or how late. Phase 2 makes sure **something always calls it**, tells an admin when it did not happen, and shows the state on one status call.

**Layers (each one alone lands the handover; together they land it once):**

| Layer | Who calls | When |
|---|---|---|
| The server's own clock | a thread in the server (`ops_clock_loop`) | once ~3 s after boot, then at the rhythm (never more than 5 minutes apart). A visit that wakes a sleeping Render service is the "first touch after a lock": the boot catches a missed handover up at once. On by default on a hosted service. |
| An external clock | Supabase (`pg_cron` + `pg_net`), later the PC and GitHub | `POST /api/ops/tick?source=supabase` with a token, at the rhythm the answer asks for |
| An admin | the Ops card's "run" (Phase 2 UI, later) or `POST /api/ops/tick` with their session | by hand |
| The older route | `POST /api/rollover?comp=` with the scheduler token | still works, unchanged |

**The rhythm** (`clock_rhythm`): every **minute** from 48 hours before a lock until its handover has run, every **30 minutes** otherwise. The tick answer carries `next_tick_in_s`, so the rhythm is a setting of the server and not a schedule baked into a clock. Why: Render's free tier has 750 hours a month and an always-on minute clock would use about 744 of them.

## The settings (environment variables, all optional; nothing changes for members)

| Variable | Meaning |
|---|---|
| `OPS_TOKEN` | the shared secret an external clock sends (`Authorization: Bearer ...`). The existing scheduler token `ROLLOVER_TOKEN` is accepted too. Compared in constant time; an unset token matches nothing (an empty bearer does not match an empty setting). |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | turn the admin alerts on. Both needed; either missing = dormant (nothing is sent, nothing is recorded). |
| `OPS_APP_CLOCK` | `1` turns the server's own clock on anywhere (local runs), `0` turns it off on a hosted service. |
| `PUBLIC_BASE_URL` | the address that goes into an alert's "Open: ..." (see [origin.md](origin.md)). |

## Routes

- `POST /api/ops/tick[?source=supabase|pc|github|...]` — a clock (token) or an admin. Applies whatever handover is due for **any** competition, leaves the heartbeat `tick:<source>`, judges how things look, sends the alerts that judgement calls for. Answers `{ok, now, source, applied:[{competition, kind, to_week, members, activated}], errors, mode:"busy"|"idle", next_tick_in_s, health, alerts_sent}`; **500** with `error` and `code` when a competition's handover failed (the others in the same tick are still applied, and the failing one left nothing behind). `source` is a short lower-case word; `app` (the server's own clock) cannot be claimed over HTTP and becomes `clock`.
- `GET /api/ops/status` — admin or the clock token: `{ok, now, states, clocks, rhythm, health, alerts:{telegram, open_incidents}, tokens:{ops_token, scheduler_token}, address}`. `states` has one entry per competition (managed or not) with `due`, `awaiting`, `blocked`, `overdue_s`, `next_lock`, `applied_to`, `last_run`. `clocks` lists every clock that ever ticked with its age. The tokens are only shown as booleans.
- `POST /api/ops/test-alert` — admin only. One test message; says plainly whether Telegram is set up and what went wrong (`Telegram answered 401: Unauthorized`), never the token.
- A call that proves nothing counts against the sign-in rate limit, so a token cannot be guessed (the fifth wrong guess in a minute gets 429); a good token is never counted.

## What the rules say (logic/ops_plan.py)

- **State of a competition:** `due` = a handover that may run now; `awaiting` = a lock has passed and nothing has run; `blocked` = locked but not allowed to run (the competition is upcoming and does not start itself, or an admin undid the week and it is held); `overdue_s` = seconds since the lock.
- **Health:** **red** = a handover is still not done **2 minutes** after its lock, or a lock has passed that cannot run; **orange** = a clock that has ticked before has been quiet for **3 minutes** inside a busy window (the in-server clock is never expected to tick); **green** otherwise.
- **Alerts** (Sai's decision: Telegram, plain, only display names, no secrets): an **incident** (late handover `late:<comp>:<week>`, blocked `blocked:<comp>:<week>`, silent clock `silent:<source>`) is announced **once**, repeated as `STILL FAILING (N min)` at most every **30 minutes**, and closed with `RESOLVED:`. An **event** (a week went live; a competition switched itself on) is announced once; a handover older than 24 hours is remembered but never announced (turning alerts on must not replay history). The alert state is one `meta` row (`ops_alerts`), read and written under a row lock so two clocks that call at the same moment cannot send the same message twice.
- **A failed send is not lost:** a new incident or an event whose message did not get out is taken back, so the next tick raises it again. Reminders and "resolved" are not retried. A send that fails never fails the handover that has just run.
- **Without Telegram nothing is recorded**, so a problem that is already there is raised as a new incident the moment Telegram is set up.

## Setting up the two things only the owner can do

**1. Telegram (the alerts).** In Telegram, talk to `@BotFather`: `/newbot`, choose a name and a user name, and it gives you a token. Put the token **only** in a file on your PC and in Render (Environment tab, `TELEGRAM_BOT_TOKEN`): never in a chat or a commit. Send any message to the new bot from your own Telegram account, then find your chat id (the tool `tools/telegram_find_chat.py`, which reads the token from the file and prints only the chat id) and set it as `TELEGRAM_CHAT_ID`. "Send test alert" (or `POST /api/ops/test-alert`) proves it.

**2. The Supabase clock (a second, independent trigger).** *This recipe has NOT been run against a real Supabase project yet; the first run is the test.* In a Supabase project (the free tier is enough): enable the extensions `pg_cron` and `pg_net` (Database, Extensions); store the app's token in the Vault (`select vault.create_secret('<the OPS_TOKEN value>', 'bcl_ops_token');`); then in the SQL editor:

```sql
create table if not exists public.bcl_clock (id int primary key default 1, next_at timestamptz not null default now(), last_request bigint);
insert into public.bcl_clock (id) values (1) on conflict do nothing;

create or replace function public.bcl_tick() returns void language plpgsql security definer as $$
declare tok text; due timestamptz; last_id bigint; txt text; reply jsonb; wait_s int := 60; rid bigint;
begin
  select next_at, last_request into due, last_id from public.bcl_clock where id = 1;
  if now() < due then return; end if;                                -- not yet: the app said how long to wait last time
  if last_id is not null then
    select content into txt from net._http_response where id = last_id;
    begin reply := txt::jsonb; exception when others then reply := null; end;     -- an error page (Render waking up) is not JSON: carry on with a minute
    if reply is not null and (reply ->> 'next_tick_in_s') is not null then
      wait_s := greatest(30, least(1800, (reply ->> 'next_tick_in_s')::int));
    end if;
  end if;
  select decrypted_secret into tok from vault.decrypted_secrets where name = 'bcl_ops_token';
  rid := net.http_post(url := 'https://<YOUR APP ADDRESS>/api/ops/tick?source=supabase',
                       headers := jsonb_build_object('Authorization', 'Bearer ' || tok, 'Content-Type', 'application/json'),
                       body := '{}'::jsonb, timeout_milliseconds := 25000);
  update public.bcl_clock set last_request = rid, next_at = now() + make_interval(secs => wait_s) where id = 1;
end $$;

select cron.schedule('bcl-tick', '* * * * *', $$select public.bcl_tick()$$);
```

How to see that it works: the Ops status (`/api/ops/status`, or the Ops card) lists `supabase` among the clocks with an age under a minute near a lock; in Supabase, `select * from net._http_response order by created desc limit 5;` shows the app's answers; `select * from cron.job_run_details order by start_time desc limit 5;` shows the runs. To stop it: `select cron.unschedule('bcl-tick');`.

## Known limits

- **A sleeping service cannot run its own clock.** If nobody opens the app and no external clock is set up, a lock passes and the handover waits (picking stays locked: fail closed) until the next visit wakes the service, and then the boot tick catches it up. The external clock is what makes it land within two minutes while nobody is looking.
- **Telegram is the only channel.** If it is down, the Ops status still shows everything; a message that could not be sent is raised again (new incidents and events) or lost (reminders, "resolved").
- **The Supabase recipe and the PC runner fallback are not part of this release's tests** (the recipe needs a real project; the PC runner step comes with the PC scheduled tasks).
- **No Ops card yet**: the data is there (`/api/ops/status`), the Admin tab card with "Run due rollovers (preview)" and "Send test alert" is the next piece.
- The clock is not minute-exact in the quiet stretches by design (30 minutes), and Render's free tier can take 30-60 seconds to wake.

## Dead Code Audit

None: every function of `ops_plan` and `ops_notify` is called by `routes.py`; `ops_alert_state` by `/api/ops/status`.

## Open Questions

Whether the external clock should also be a GitHub Actions workflow (as a third layer) and whether the PC runner should carry a rollover step: both are cheap once the tick exists, and wait for the owner's choice.

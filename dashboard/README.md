# Arbiter dashboard

The screen the judges watch the business run on. Arbiter starts from a £50 seed and runs
a real service back office: it takes client payments through Stripe, buys what each job
needs to deliver, and refuses any spend that would kill the job's margin.

It renders the core's `/state` contract: the seed → goal → balance header, a balance-over-time
sparkline, revenue / cost / margin meters, a live event feed (client paid → verified → bought
to deliver → refused), a per-job ledger, and the owner phone-approval card. The signature beat
is the margin-killer refusal: *"it refused to buy that because it would've made the job unprofitable."*

This is a static page — no build step, no dependencies.

## Run it

It uses `fetch`, so serve the folder over http (don't open the file directly):

```powershell
# from the dashboard/ folder
python -m http.server 5173
```

Then open http://localhost:5173 for the landing page, or jump straight to the
dashboard at http://localhost:5173/dashboard.html and click **Run demo**.

(Any static server works, e.g. `npx serve` or the VS Code Live Server extension.)

## Modes

- **Sample (default):** reads `sample_state.json`. No backend needed.
- **Run demo:** plays the sample sequence beat by beat — client pays, agent buys to deliver,
  pauses on the owner-approval beat for the margin-thin spend, and lands on the margin-killer
  refusal as the hero moment.
- **Go live:** polls `GET /state` every ~1.5s and points the Approve / Deny buttons
  at the `approve_url` / `deny_url` the endpoint hands back. Start the core backend
  first, then click **Go live**.

  ```powershell
  # from the repo root
  python -m venv .venv && .venv\Scripts\Activate.ps1
  pip install -e ".[web]"
  python -m uvicorn arbiter.web:app --host 127.0.0.1 --port 8000
  ```

  Then open http://127.0.0.1:8000 (the backend serves the dashboard itself) and
  click **Run demo** / **Go live**.

## The contract

Defined in `../04_integration_contract.md`. The dashboard only reads `/state` and
posts to the approve/deny URLs — it never reimplements any decision logic.

## Files

- `index.html` — landing page (front door, links into the dashboard)
- `dashboard.html` — dashboard layout
- `styles.css` — theme, seed/goal header, per-job ledger, margin-killer hero
- `app.js` — render + Run demo + balance sparkline + per-job ledger + approve/deny wiring

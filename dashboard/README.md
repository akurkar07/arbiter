# Arbiter dashboard

The screen the judges watch. It renders the core's `/state` contract: an earnings /
net / spend meter, the fraud catch-rate, a colour-coded event timeline, an owner
approval card, and the self-spend block as the hero moment.

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
- **Run demo:** plays the sample sequence beat by beat, pauses on the owner-approval
  beat to show the approval card, and lands on the self-spend block.
- **Go live:** polls `GET /state` every ~1.5s and points the Approve / Deny buttons
  at the `approve_url` / `deny_url` the endpoint hands back. Start the core backend
  first, then click **Go live**.

## The contract

Defined in `../04_integration_contract.md`. The dashboard only reads `/state` and
posts to the approve/deny URLs — it never reimplements any decision logic.

## Files

- `index.html` — landing page (front door, links into the dashboard)
- `dashboard.html` — dashboard layout
- `styles.css` — theme, decision colours, hero self-block
- `app.js` — render + Run demo + live poll + approve/deny wiring

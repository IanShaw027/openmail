## Summary

<!-- What and why (1–3 sentences). -->

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Docs / meta
- [ ] Refactor (no behavior change)
- [ ] Security hardening

## Checklist

- [ ] Aligns with **local-first** (no multi-tenant user login)
- [ ] Backend: `cd backend && pytest -q` (with `OPENMAIL_MASTER_KEY` set)
- [ ] Frontend: `cd frontend && npm run build` if UI changed
- [ ] No secrets, recovery keys, or production `.env` in the diff
- [ ] Docs updated if user-facing behavior changed

## Test plan

<!-- How you verified. -->

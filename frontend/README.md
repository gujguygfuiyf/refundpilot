# RefundPilot web (Next.js)

Buyer pages (`/`, `/claims/new`, `/claims/[id]`), the merchant queue (`/merchant`, AG Grid Community) and the
policy simulator (`/merchant/policy`). Talks to the FastAPI backend in `../backend`.

```bash
cd frontend
npm ci
cp .env.example .env.local      # NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
npm run dev                     # http://localhost:3000
```

Open the app on `http://localhost:3000` (not `127.0.0.1`): the backend's default `CORS_ORIGINS` only allows
`localhost`. The merchant pages ask for `MERCHANT_TOKEN` once and keep it in the browser; set
`NEXT_PUBLIC_DEMO_MERCHANT_TOKEN` on the public demo so judges skip that step.

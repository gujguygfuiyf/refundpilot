# RefundPilot web (Next.js)

Buyer pages (`/`, `/claims/new`, `/claims/[id]`), the merchant queue (`/merchant`, AG Grid Community) and the
policy simulator (`/merchant/policy`). Talks to the FastAPI backend in `../backend`.

```bash
cd frontend
npm ci
cp .env.example .env.local      # NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
npm run dev                     # http://localhost:3000
```

Without PayPal keys the backend seeds 8 demo orders on a fake gateway, so the whole flow works offline.
The merchant pages ask for `MERCHANT_TOKEN` once and keep it in the browser; set
`NEXT_PUBLIC_DEMO_MERCHANT_TOKEN` on the public demo so judges skip that step.

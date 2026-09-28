# RideFare Render Deployment Checklist

## Recommended deployment order

1. Push the `ride_fare_predictor` folder to GitHub.
2. Create a Render account and connect GitHub.
3. Deploy the included `render.yaml` as a Blueprint.
4. Confirm the PostgreSQL database is created.
5. Confirm `DATABASE_URL` is connected to the web service.
6. Set `ADMIN_USERNAME` and a strong `ADMIN_PASSWORD`.
7. Keep `COOKIE_SECURE=1` for the HTTPS website.
8. Optionally add `GEMINI_API_KEY`.
9. Wait for the build to finish.
10. Open `/health` and verify that it reports `status: ok` and `database: postgresql`.
11. Test registration, booking, prediction, payment demo, receipt, history, complaint and admin pages.
12. Add a custom domain after the generated Render URL works correctly.

## If deployment fails

Open the Render service logs and check the first Python/import/database error. Common causes are:

- a missing dependency in `requirements.txt`
- an incorrect `DATABASE_URL`
- a missing environment variable
- a model file not committed to GitHub
- a third-party API being unavailable

The app has a SQLite fallback for local development, but a public multi-user deployment should use PostgreSQL.

# RideFare — AI-Based Smart Ride Booking & Dynamic Fare Prediction

RideFare is a Flask + Machine Learning major-project web application for smart ride booking and fare estimation. It combines a Random Forest fare predictor with route-distance calculation, user accounts, booking history, sandbox payments, complaints, feedback, an admin dashboard and an AI support assistant.

## Major-project modules

1. User authentication and profile management
2. Pickup/drop location and route management
3. OSRM road-distance calculation
4. Machine-learning fare prediction (Random Forest, with Linear Regression/XGBoost comparison metrics)
5. Dynamic fare calculation and transparent breakdown
6. Vehicle and passenger-capacity validation
7. Ride booking and lifecycle management
8. Sandbox payment and receipt generation
9. Demo driver assignment and ETA
10. Ride history and feedback
11. Complaint/support management
12. Admin analytics dashboard
13. AI support assistant (optional Gemini integration)
14. Production deployment with Gunicorn + PostgreSQL
15. Health monitoring endpoint for hosting platforms

> **Academic note:** payment, driver assignment and ETA are simulations. The project does not process real money, provide live GPS, or dispatch real drivers.

## Project structure

```text
ride_fare_predictor/
├── app.py
├── train_model.py
├── generate_dataset.py
├── bangalore_ride_data.csv
├── model.pkl
├── requirements.txt
├── Procfile
├── render.yaml
├── .env.example
├── .gitignore
├── README.md
├── RENDER_DEPLOYMENT.md
├── templates/
└── static/
```

## Run locally

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
python app.py
```

Open `http://127.0.0.1:5000`.

Local development uses SQLite automatically when `DATABASE_URL` is not set.

## Deploy as an official public website

### 1. Create a GitHub repository

Create a repository such as `ridefare-smart-booking` and upload the contents of this folder.

Do **not** commit `.env`, API keys, passwords or `users.db`.

### 2. Create the online database and website

The included `render.yaml` is a Render Blueprint configuration. It creates:

- a Flask web service
- a PostgreSQL database
- a generated Flask secret
- a database connection variable
- a production Gunicorn command
- a `/health` deployment health check

On Render, connect the GitHub repository and deploy the Blueprint. If the free PostgreSQL option is not available for your account/region, select an available PostgreSQL plan and keep the same `DATABASE_URL` variable.

### 3. Set required environment variables

In the web service, configure:

```text
SECRET_KEY=<strong-random-value>
DATABASE_URL=<Render PostgreSQL connection string>
COOKIE_SECURE=1
ADMIN_USERNAME=<your-admin-name>
ADMIN_PASSWORD=<strong-admin-password>
```

Optional:

```text
GEMINI_API_KEY=<Google AI Studio key>
GEMINI_MODEL=gemini-3.7-flash
```

If no Gemini key is supplied, the built-in RideFare support assistant continues to work.

### 4. Build and start commands

Build:

```text
pip install -r requirements.txt
```

Start:

```text
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

### 5. Test the deployed website

Check:

- `/health`
- registration/login
- booking and route distance
- fare prediction
- payment demo
- receipt
- ride history
- feedback
- complaints
- admin dashboard

The health endpoint should report `status: ok` and `database: postgresql` after PostgreSQL is connected.

### 6. Connect a custom domain

After the Render service works, add a domain you own (for example `ridefare.in`) in the service's **Custom Domains** settings. Follow the DNS records Render provides. HTTPS is then configured by the hosting platform.

## Admin account

The first startup creates the configured admin user if it does not already exist. Never use the demo password on a public deployment. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in Render before going live.

## Database architecture

Local:

```text
Browser → Flask → SQLite
```

Deployed:

```text
Browser → HTTPS → Flask/Gunicorn → PostgreSQL
                         ↓
                    ML model + OSRM
```

The same application code automatically selects PostgreSQL when `DATABASE_URL` is present and SQLite otherwise.

## Security notes before a real commercial launch

This is an academic major project. Before using it for real customers, additionally implement CSRF protection, email-based password reset, rate limiting, production logging/monitoring, stronger role management, database migrations, real payment-provider integration, privacy/consent controls, and a real driver/dispatch service.

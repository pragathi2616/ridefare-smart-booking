import pandas as pd
import joblib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
import numpy as np

try:
    from xgboost import XGBRegressor
except ImportError:
    XGBRegressor = None

# Bangalore ride dataset: distance + vehicle type -> booking value
DATA_PATH = "bangalore_ride_data.csv"
MODEL_PATH = "model.pkl"

data = pd.read_csv(DATA_PATH)
data = data[["Ride Distance", "Vehicle Type", "Booking Value"]].dropna()

data["Vehicle Type"] = data["Vehicle Type"].replace({
    "Mini": "Uber Go",
    "Sedan": "Premier",
    "SUV": "Uber XL"
})

vehicles = ["Auto", "Bike", "Scooty", "Uber Go", "Go Non AC", "Premier", "Uber XL"]
# Ensure every supported vehicle class is represented without changing the
# original rows beyond the project's existing synthetic balancing approach.
extra = []
for vehicle in vehicles:
    sample = data.sample(min(150, len(data)), replace=True, random_state=42)
    sample = sample.copy()
    sample["Vehicle Type"] = vehicle
    extra.append(sample)
data = pd.concat([data] + extra, ignore_index=True)

le = LabelEncoder()
data["Vehicle Type"] = le.fit_transform(data["Vehicle Type"])

X = data[["Ride Distance", "Vehicle Type"]]
y = data["Booking Value"] * 0.90

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.20, random_state=42
)

models = {
    "Linear Regression": LinearRegression(),
    "Random Forest": RandomForestRegressor(
        n_estimators=200, max_depth=12, random_state=42
    )
}
if XGBRegressor is not None:
    models["XGBoost"] = XGBRegressor(
        n_estimators=250, max_depth=6, learning_rate=0.05,
        subsample=0.9, colsample_bytree=0.9, objective="reg:squarederror",
        random_state=42
    )

metrics = {}
trained = {}
for name, estimator in models.items():
    estimator.fit(X_train, y_train)
    pred = estimator.predict(X_test)
    metrics[name] = {
        "r2": float(r2_score(y_test, pred)),
        "mae": float(mean_absolute_error(y_test, pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, pred)))
    }
    trained[name] = estimator

# Random Forest remains the application's primary model to preserve the
# existing project's behavior while exposing a meaningful model comparison.
primary = trained["Random Forest"]

joblib.dump({
    "model": primary,
    "label_encoder": le,
    "accuracy": metrics["Random Forest"]["r2"],
    "metrics": metrics,
    "feature_names": ["Ride Distance", "Vehicle Type"],
    "primary_model": "Random Forest"
}, MODEL_PATH)

print("Model training complete.")
for name, m in metrics.items():
    print(f"{name}: R2={m['r2']:.4f}, MAE={m['mae']:.2f}, RMSE={m['rmse']:.2f}")

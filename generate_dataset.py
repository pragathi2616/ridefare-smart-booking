"""
Generates a synthetic bangalore_ride_data.csv so the project can be trained
and run out of the box. Replace this file with your real dataset if you
have one -- just make sure it has the same three columns:

    Ride Distance, Vehicle Type, Booking Value
"""

import numpy as np
import pandas as pd

np.random.seed(42)

N = 4000

vehicles = ["Mini", "Sedan", "SUV", "Auto", "Bike", "Scooty", "Go Non AC"]
vehicle_base_rate = {
    "Mini": 12,       # -> mapped to Uber Go later
    "Sedan": 16,      # -> mapped to Premier later
    "SUV": 20,        # -> mapped to Uber XL later
    "Auto": 10,
    "Bike": 6,
    "Scooty": 5,
    "Go Non AC": 11,
}

rows = []
for _ in range(N):
    vehicle = np.random.choice(vehicles)
    distance = np.round(np.random.uniform(0.5, 30), 2)
    rate = vehicle_base_rate[vehicle]
    base_fare = np.random.uniform(20, 40)
    noise = np.random.normal(0, 8)
    booking_value = max(15, base_fare + distance * rate + noise)
    rows.append([distance, vehicle, round(booking_value, 2)])

df = pd.DataFrame(rows, columns=["Ride Distance", "Vehicle Type", "Booking Value"])
df.to_csv("bangalore_ride_data.csv", index=False)

print(f"✅ Generated bangalore_ride_data.csv with {len(df)} rows")

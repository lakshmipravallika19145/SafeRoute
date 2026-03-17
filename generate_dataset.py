import json
import random

# Vijayawada geographic bounds
LAT_MIN = 16.40
LAT_MAX = 16.60
LNG_MIN = 80.55
LNG_MAX = 80.75

dataset = []

for i in range(300):

    location = {
        "id": i + 1,
        "area": f"Location_{i+1}",

        "lat": round(random.uniform(LAT_MIN, LAT_MAX), 6),
        "lng": round(random.uniform(LNG_MIN, LNG_MAX), 6),

        "crime_rate": random.randint(1,10),
        "street_lighting": random.randint(1,10),
        "crowd_density": random.randint(1,10),
        "police_proximity": random.randint(1,10),
        "cctv_coverage": random.randint(1,10),
        "road_visibility": random.randint(1,10),
        "traffic_density": random.randint(1,10),
        "incident_reports": random.randint(1,10)
    }

    dataset.append(location)

with open("safety_data.json", "w") as f:
    json.dump(dataset, f, indent=4)

print("300-location safety dataset created.")
import json
import random
from pathlib import Path


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def make_point(i, lat_min, lat_max, lng_min, lng_max, area_type):
    lat = random.uniform(lat_min, lat_max)
    lng = random.uniform(lng_min, lng_max)

    # Semi-realistic patterns by area type (1..10 scale)
    if area_type == "city_center":
        crime = random.randint(1, 4)
        light = random.randint(7, 10)
        crowd = random.randint(6, 10)
    elif area_type == "highway":
        crime = random.randint(4, 7)
        light = random.randint(4, 7)
        crowd = random.randint(1, 4)
    elif area_type == "industrial":
        crime = random.randint(6, 10)
        light = random.randint(1, 5)
        crowd = random.randint(1, 4)
    elif area_type == "residential":
        crime = random.randint(3, 7)
        light = random.randint(4, 8)
        crowd = random.randint(2, 7)
    else:
        crime = random.randint(1, 10)
        light = random.randint(1, 10)
        crowd = random.randint(1, 10)

    police = clamp(int(round(11 - crime + random.randint(-2, 2))), 1, 10)
    cctv = clamp(int(round((light + police) / 2 + random.randint(-2, 2))), 1, 10)
    visibility = clamp(int(round((light + 10 - crime) / 2 + random.randint(-2, 2))), 1, 10)
    traffic = clamp(int(round((crowd + random.randint(0, 3)))), 1, 10)
    incidents = clamp(int(round(crime / 2 + random.randint(0, 4))), 0, 10)

    return {
        "id": i,
        "area": f"Location_{i} ({area_type.replace('_', ' ').title()})",
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "crime_rate": crime,
        "street_lighting": light,
        "crowd_density": crowd,
        "police_proximity": police,
        "cctv_coverage": cctv,
        "road_visibility": visibility,
        "traffic_density": traffic,
        "incident_reports": incidents,
    }


def main():
    random.seed(42)

    # Vijayawada-ish bounding box (approx)
    lat_min, lat_max = 16.44, 16.58
    lng_min, lng_max = 80.55, 80.72

    # Mix area types for "smarter-looking" data
    weights = [
        ("city_center", 0.25),
        ("residential", 0.35),
        ("highway", 0.20),
        ("industrial", 0.20),
    ]
    choices = [k for k, _ in weights]
    probs = [w for _, w in weights]

    points = []
    for i in range(1, 301):
        area_type = random.choices(choices, probs, k=1)[0]
        points.append(make_point(i, lat_min, lat_max, lng_min, lng_max, area_type))

    out_path = Path(__file__).resolve().parents[1] / "data" / "safety_points.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(points, indent=2), encoding="utf-8")
    print(f"Wrote {len(points)} points to {out_path}")


if __name__ == "__main__":
    main()


import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SAFETY_POINTS_PATH = DATA_DIR / "safety_points.json"
USER_REPORTS_PATH = DATA_DIR / "user_reports.jsonl"

_ROUTES_CACHE: dict = {}
_CACHE_TTL_S = 60

DEFAULT_SAFETY_WEIGHTS = {
    # Matches the formula you specified (positive factors)
    "street_lighting": 0.25,
    "crowd_density": 0.15,
    "police_proximity": 0.10,
    "cctv_coverage": 0.10,
    "road_visibility": 0.10,
    "traffic_density": 0.10,
    # Negative factors (applied as subtraction in scoring)
    "crime_rate": 0.15,
    "incident_reports": 0.05,
}

SAFETY_PERCENT_THRESHOLDS = {
    "safe": 70.0,
    "moderate": 40.0,
}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safety_point_score(p: dict, weights: dict | None = None) -> float:
    """
    Returns the RAW safety score for a single location point.
    Input metrics assumed to be 1..10 (incident_reports may be 0..10).
    """
    lighting = float(p.get("street_lighting", 5))
    crowd = float(p.get("crowd_density", 5))
    crime = float(p.get("crime_rate", 5))
    police = float(p.get("police_proximity", 5))
    cctv = float(p.get("cctv_coverage", 5))
    visibility = float(p.get("road_visibility", 5))
    traffic = float(p.get("traffic_density", 5))
    incidents = float(p.get("incident_reports", 3))

    # If the frontend doesn't pass weights, we use your required formula weights.
    w = dict(DEFAULT_SAFETY_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k in w:
                try:
                    w[k] = float(v)
                except (TypeError, ValueError):
                    pass

    w_lighting = float(w["street_lighting"])
    w_crowd = float(w["crowd_density"])
    w_police = float(w["police_proximity"])
    w_cctv = float(w["cctv_coverage"])
    w_visibility = float(w["road_visibility"])
    w_traffic = float(w["traffic_density"])
    w_crime = float(w["crime_rate"])
    w_incidents = float(w["incident_reports"])

    raw = (
        w_lighting * lighting
        + w_crowd * crowd
        + w_police * police
        + w_cctv * cctv
        + w_visibility * visibility
        + w_traffic * traffic
        - w_crime * crime
        - w_incidents * incidents
    )

    return float(raw)


def _normalize_safety_percent(raw_score: float) -> float:
    """
    Normalize raw score to 0..100.

    With the required weights and metric ranges:
    - raw_min ≈ 0.8 - (0.15*10 + 0.05*10) = -1.2
    - raw_max ≈ 8.0 - (0.15*1 + 0.05*0)  = 7.85
    """
    raw_min = -1.2
    raw_max = 7.85
    if raw_max <= raw_min:
        return 50.0
    pct = (raw_score - raw_min) / (raw_max - raw_min) * 100.0
    return float(_clamp(pct, 0.0, 100.0))


def _zone_label_from_percent(pct_0_100: float) -> str:
    if pct_0_100 >= SAFETY_PERCENT_THRESHOLDS["safe"]:
        return "safe"
    if pct_0_100 >= SAFETY_PERCENT_THRESHOLDS["moderate"]:
        return "moderate"
    return "unsafe"


def _zone_label(score_or_pct: float) -> str:
    """
    Backwards compatible helper:
    - if value <= 10, treat it as 0..10 then convert to percent
    - else treat as percent
    """
    v = float(score_or_pct)
    pct = v * 10.0 if v <= 10.0 else v
    return _zone_label_from_percent(pct)


def _http_get_json(url: str, headers: dict | None = None, timeout_s: int = 12):
    """
    Small HTTP JSON helper with timeouts.
    Raises on network failures; callers should handle and return a clean API error.
    """
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read().decode("utf-8", errors="ignore")
    return json.loads(data)


def _osrm_routes(start_lat: float, start_lng: float, end_lat: float, end_lng: float, timeout_s: int = 12):
    # OSRM expects lng,lat
    base = "https://router.project-osrm.org/route/v1/driving/"
    coords = f"{start_lng},{start_lat};{end_lng},{end_lat}"
    qs = urllib.parse.urlencode(
        {
            "overview": "full",
            "geometries": "geojson",
            "alternatives": "true",
            # Needed for navigation-style UI (turn-by-turn)
            "steps": "true",
        }
    )
    return _http_get_json(base + coords + "?" + qs, timeout_s=timeout_s)


def _cache_key_for_route(start_lat: float, start_lng: float, end_lat: float, end_lng: float) -> str:
    # Round to reduce cache fragmentation; good enough for hackathon demo.
    return f"{round(start_lat,5)}:{round(start_lng,5)}->{round(end_lat,5)}:{round(end_lng,5)}"


def _cache_get(key: str):
    item = _ROUTES_CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if (datetime.now(tz=timezone.utc).timestamp() - ts) > _CACHE_TTL_S:
        _ROUTES_CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value):
    _ROUTES_CACHE[key] = (datetime.now(tz=timezone.utc).timestamp(), value)


def _interpolate_route(a_lat: float, a_lng: float, b_lat: float, b_lng: float, n: int = 80):
    """
    Build a simple GeoJSON LineString coordinates array [[lng,lat],...].
    Used as a fallback when OSRM demo server times out.
    """
    n = max(2, int(n))
    coords = []
    for i in range(n):
        t = i / (n - 1)
        lat = a_lat + (b_lat - a_lat) * t
        lng = a_lng + (b_lng - a_lng) * t
        coords.append([lng, lat])
    return coords


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Haversine distance between two points in meters."""
    R = 6_371_000
    dlat = math.radians(b_lat - a_lat)
    dlng = math.radians(b_lng - a_lng)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _fallback_routes(start_lat: float, start_lng: float, end_lat: float, end_lng: float):
    """
    Return 3 simple alternative routes (direct + two detours).
    Geometry shape is demo-only; scoring still uses your safety dataset.
    Distance and duration use haversine for accuracy.
    """
    base = _interpolate_route(start_lat, start_lng, end_lat, end_lng, n=90)

    # Build two gentle detours by offsetting the midpoint a bit
    mid_lat = (start_lat + end_lat) / 2.0
    mid_lng = (start_lng + end_lng) / 2.0

    # offsets in degrees (~0.003 ≈ 300m)
    det1 = _interpolate_route(start_lat, start_lng, mid_lat + 0.003, mid_lng - 0.003, n=45)[:-1] + _interpolate_route(mid_lat + 0.003, mid_lng - 0.003, end_lat, end_lng, n=45)
    det2 = _interpolate_route(start_lat, start_lng, mid_lat - 0.003, mid_lng + 0.003, n=45)[:-1] + _interpolate_route(mid_lat - 0.003, mid_lng + 0.003, end_lat, end_lng, n=45)

    def approx_distance_m(coords):
        """Sum haversine distances along route segments. Accurate for fallback."""
        if len(coords) < 2:
            return 0.0
        total = 0.0
        for i in range(1, len(coords)):
            a_lng, a_lat = coords[i - 1]
            b_lng, b_lat = coords[i]
            total += _haversine_m(a_lat, a_lng, b_lat, b_lng)
        return float(max(0.0, total))

    routes = []
    for coords in [base, det1, det2]:
        dist = approx_distance_m(coords)
        # Urban average ~25 km/h => 6.94 m/s for ETA
        dur = dist / 6.94 if dist > 0 else 0.0
        routes.append(
            {
                "distance": dist,
                "duration": dur,
                "geometry": {"type": "LineString", "coordinates": coords},
                "legs": [],
            }
        )
    return routes


def _meters_per_degree_lat() -> float:
    return 111_320.0


def _meters_per_degree_lng(at_lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(at_lat))


def _point_to_segment_distance_m(lat: float, lng: float, a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """
    Fast approximation: project lat/lng to local meters using equirectangular scaling,
    then compute point-to-segment distance in 2D.
    """
    ref_lat = (a_lat + b_lat) / 2.0
    mx = _meters_per_degree_lng(ref_lat)
    my = _meters_per_degree_lat()

    px, py = (lng * mx, lat * my)
    ax, ay = (a_lng * mx, a_lat * my)
    bx, by = (b_lng * mx, b_lat * my)

    abx, aby = (bx - ax, by - ay)
    apx, apy = (px - ax, py - ay)
    ab_len2 = abx * abx + aby * aby
    if ab_len2 <= 1e-9:
        dx, dy = (px - ax, py - ay)
        return math.hypot(dx, dy)

    t = (apx * abx + apy * aby) / ab_len2
    t = _clamp(t, 0.0, 1.0)
    cx, cy = (ax + t * abx, ay + t * aby)
    return math.hypot(px - cx, py - cy)


def _route_nearby_points(
    route_coords: list,
    safety_points: list,
    max_distance_m: float = 280.0,
    weights: dict | None = None,
) -> list:
    """
    route_coords: list of [lng, lat] as returned by OSRM GeoJSON.
    Returns list of safety points that are within max_distance_m of any route segment.
    """
    if not route_coords or len(route_coords) < 2:
        return []

    # Light downsample to keep accuracy while reducing cost (was too aggressive)
    step = 2 if len(route_coords) > 400 else 1
    coords = route_coords[::step]
    if coords[-1] != route_coords[-1]:
        coords.append(route_coords[-1])

    segments = []
    for i in range(len(coords) - 1):
        a_lng, a_lat = coords[i]
        b_lng, b_lat = coords[i + 1]
        segments.append((a_lat, a_lng, b_lat, b_lng))

    nearby = []
    for p in safety_points:
        lat = float(p.get("lat"))
        lng = float(p.get("lng"))
        min_d = float("inf")
        for a_lat, a_lng, b_lat, b_lng in segments:
            d = _point_to_segment_distance_m(lat, lng, a_lat, a_lng, b_lat, b_lng)
            if d < min_d:
                min_d = d
            if min_d <= max_distance_m:
                break
        if min_d <= max_distance_m:
            p2 = dict(p)
            raw = _safety_point_score(p2, weights=weights)
            pct = _normalize_safety_percent(raw)
            p2["safety_raw"] = round(raw, 4)
            p2["safety_percent"] = round(pct, 1)
            p2["zone"] = _zone_label_from_percent(pct)
            p2["distance_to_route_m"] = round(min_d, 1)
            nearby.append(p2)

    return nearby


def _route_safety_score(nearby: list, fallback_points: list | None = None) -> float:
    """
    Compute route safety score with distance-weighted average.
    Points closer to the route contribute more than points at the edge.
    Uses inverse-distance weighting: w = 1 / (1 + d/80) so close points dominate.
    If no nearby points, uses fallback_points (nearest within 500m) with strong penalty.
    """
    if nearby:
        weighted_sum = 0.0
        weight_total = 0.0
        for p in nearby:
            d = float(p.get("distance_to_route_m", 0))
            pct = float(p.get("safety_percent", 50.0))
            # Inverse-distance weight: closer = higher influence
            w = 1.0 / (1.0 + d / 80.0)
            weighted_sum += pct * w
            weight_total += w
        if weight_total > 0:
            return weighted_sum / weight_total

    # No points within max_distance: use nearest points with distance penalty
    if fallback_points:
        weighted_sum = 0.0
        weight_total = 0.0
        for p in fallback_points:
            d = float(p.get("distance_to_route_m", 500))
            pct = float(p.get("safety_percent", 50.0))
            w = 1.0 / (1.0 + d / 150.0)  # Strong distance penalty
            weighted_sum += pct * w
            weight_total += w
        if weight_total > 0:
            return weighted_sum / weight_total

    return 50.0  # Unknown area default

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    CORS(app)

    @app.route("/")
    def home():
        return render_template("dashboard.html")

    @app.route("/api/test", methods=["GET"])
    def test_api():
        # Simple health-check endpoint
        return jsonify({"message": "SafeRoute API working"})

    @app.route("/api/safety_points", methods=["GET"])
    def safety_points():
        points = _read_json(SAFETY_POINTS_PATH, default=[])
        # Add computed score/zone for frontend visualization
        enriched = []
        for p in points:
            p2 = dict(p)
            raw = _safety_point_score(p2)
            pct = _normalize_safety_percent(raw)
            p2["safety_raw"] = round(raw, 4)
            p2["safety_percent"] = round(pct, 1)
            p2["zone"] = _zone_label_from_percent(pct)
            enriched.append(p2)
        return jsonify({"count": len(enriched), "points": enriched})

    @app.route("/api/geocode", methods=["GET"])
    def geocode():
        """
        Convert address string to coordinates using Nominatim.
        Used when user types an address and clicks Find without selecting from dropdown.
        """
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"error": "Query too short"}), 400

        headers = {
            "User-Agent": "SafeRoute-Hackathon/1.0 (Flask backend proxy)",
            "Accept-Language": "en",
        }
        params = {
            "format": "json",
            "q": q,
            "limit": "1",
            "addressdetails": "1",
        }
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
        try:
            results = _http_get_json(url, headers=headers, timeout_s=10)
            if not results:
                return jsonify({"error": "Location not found"}), 404
            r = results[0]
            return jsonify({
                "lat": float(r.get("lat")),
                "lng": float(r.get("lon")),
                "display_name": r.get("display_name", ""),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/autocomplete", methods=["GET"])
    def autocomplete():
        """
        Nominatim autocomplete proxy.
        Frontend uses this to avoid CORS/UA issues and keep API calls consistent.
        """
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"results": []})

        limit = 10

        # Vijayawada-ish bounding box (bias results; do NOT hard-restrict only to the box)
        # viewbox = left,top,right,bottom
        viewbox = "80.55,16.58,80.72,16.44"

        headers = {
            # Nominatim policy: identify the application
            "User-Agent": "SafeRoute-Hackathon/1.0 (Flask backend proxy)",
            "Accept-Language": "en",
        }

        def run_search(query: str, bounded: bool):
            params = {
                "format": "json",
                "q": query,
                "limit": str(limit),
                "addressdetails": "1",
                "namedetails": "1",
                "extratags": "1",
                "viewbox": viewbox,
                "bounded": "1" if bounded else "0",
                # Prefer POI-like results (shops/malls/universities) when available
                "featuretype": "city",
            }
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
            return _http_get_json(url, headers=headers, timeout_s=12)

        # 1) Strictly bounded search (best for nearby / Vijayawada)
        data_local = run_search(q, bounded=True)

        # 2) Broader search but still biased by viewbox (helps recognize partial/global names)
        # Add "Vijayawada" bias phrase if the user didn't type it.
        q2 = q if "vijayawada" in q.lower() else (q + " Vijayawada")
        data_biased = run_search(q2, bounded=False)

        # Merge + de-dupe results (by place_id if present, else display_name)
        merged = []
        seen = set()

        def add_items(items):
            for item in items or []:
                key = item.get("place_id") or item.get("osm_id") or item.get("display_name")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)

        add_items(data_local)
        add_items(data_biased)

        results = []
        ql = q.lower()
        has_exact_like = False

        # Primary: Nominatim results (same behavior as before)
        for item in merged[:limit]:
            try:
                disp = item.get("display_name") or ""
                disp_l = disp.lower()
                exact_like = bool(ql and ql in disp_l)
                has_exact_like = has_exact_like or exact_like

                addr = item.get("address") or {}
                results.append(
                    {
                        "display_name": disp,
                        "name": (item.get("namedetails") or {}).get("name")
                        or (disp.split(",")[0].strip() if disp else ""),
                        "address": {
                            "road": addr.get("road"),
                            "suburb": addr.get("suburb") or addr.get("neighbourhood"),
                            "city": addr.get("city") or addr.get("town") or addr.get("village"),
                            "state": addr.get("state"),
                            "postcode": addr.get("postcode"),
                        },
                        "lat": float(item.get("lat")),
                        "lng": float(item.get("lon")),
                        "type": item.get("type"),
                        "class": item.get("class"),
                        "importance": item.get("importance"),
                        "exact_like": exact_like,
                    }
                )
            except (TypeError, ValueError):
                continue

        # Secondary: local safety dataset points from safety_points.json
        # These are appended so they appear in the same dropdown and behave
        # exactly like other suggestions for the frontend.
        try:
            safety_points = _read_json(SAFETY_POINTS_PATH, default=[])
        except Exception:
            safety_points = []

        for p in safety_points:
            try:
                area = (p.get("area") or "").strip()
                if not area:
                    continue
                if ql not in area.lower():
                    continue
                lat = float(p.get("lat"))
                lng = float(p.get("lng"))
            except (TypeError, ValueError):
                continue

            disp = area
            results.append(
                {
                    "display_name": disp,
                    "name": area,
                    "address": {
                        "road": None,
                        "suburb": None,
                        "city": None,
                        "state": None,
                        "postcode": None,
                    },
                    "lat": lat,
                    "lng": lng,
                    "type": "safety_point",
                    "class": "poi",
                    "importance": 0.0,
                    "exact_like": True,
                }
            )
            has_exact_like = True

        message = None
        if results and not has_exact_like:
            message = "No exact match found — showing nearby results."

        return jsonify({"results": results, "message": message})

    @app.route("/api/routes", methods=["POST"])
    def routes():
        """
        Google-Maps-like endpoint:
        - generates up to 3 OSRM routes
        - scores each route using the safety dataset
        - returns distance, ETA, geometry, score, zone, and worst points
        """
        payload = request.get_json(silent=True) or {}
        start = payload.get("start") or {}
        end = payload.get("end") or {}
        weights = payload.get("weights")
        if weights is not None and not isinstance(weights, dict):
            return jsonify({"error": "weights must be an object"}), 400

        try:
            start_lat = float(start.get("lat"))
            start_lng = float(start.get("lng"))
            end_lat = float(end.get("lat"))
            end_lng = float(end.get("lng"))
        except (TypeError, ValueError):
            return jsonify({"error": "start/end must include lat,lng"}), 400

        cache_key = _cache_key_for_route(start_lat, start_lng, end_lat, end_lng)
        cached = _cache_get(cache_key)
        osrm = None

        if cached:
            osrm = cached
        else:
            try:
                osrm = _osrm_routes(start_lat, start_lng, end_lat, end_lng, timeout_s=12)
                _cache_set(cache_key, osrm)
            except Exception:
                return jsonify({
                    "error": "Routing service unavailable. Please try again.",
                    "routes": [],
                }), 503

        if not osrm or osrm.get("code") != "Ok":
            return jsonify({
                "error": "No routes found between the selected locations.",
                "routes": [],
            }), 404

        points = _read_json(SAFETY_POINTS_PATH, default=[])
        max_distance_m = float(payload.get("max_distance_m", 280.0))

        # Use only OSRM routes (real road-following paths); no straight-line fallbacks
        osrm_routes = list((osrm.get("routes") or [])[:3])

        routes_out = []
        for r in osrm_routes:
            coords = (r.get("geometry") or {}).get("coordinates") or []
            nearby = _route_nearby_points(coords, points, max_distance_m=max_distance_m, weights=weights)
            fallback_pts = []
            if not nearby:
                fallback_pts = _route_nearby_points(coords, points, max_distance_m=500.0, weights=weights)

            route_score = _route_safety_score(nearby, fallback_pts if fallback_pts else None)
            route_score = float(_clamp(route_score, 0.0, 100.0))
            zone = _zone_label_from_percent(route_score)
            worst = sorted(nearby, key=lambda p: float(p.get("safety_percent", 0.0)))[:10]

            # Per-route AI message for navigation panel
            route_ai_messages = {
                "safe": "This route is well-lit and frequently used. It is recommended for safer travel.",
                "moderate": "This route has moderate safety conditions. Stay aware of surroundings.",
                "unsafe": "This route passes through higher-risk areas. Consider taking precautions such as staying alert or carrying personal safety tools.",
            }
            route_ai_msg = route_ai_messages.get(zone, route_ai_messages["moderate"])

            routes_out.append(
                {
                    "distance_m": float(r.get("distance") or 0.0),
                    "duration_s": float(r.get("duration") or 0.0),
                    "geometry": r.get("geometry"),
                    # leg/step info for live navigation UI
                    "legs": r.get("legs") or [],
                    # 0..100
                    "route_score": round(route_score, 1),
                    "zone": zone,
                    "ai_message": route_ai_msg,
                    "nearby_points_count": len(nearby),
                    "worst_points": worst,
                }
            )

        # "Balanced" and "Fastest" are presentation labels. Safest is highest score.
        ranked = sorted(routes_out, key=lambda x: x["route_score"], reverse=True)
        ai_best = ranked[0] if ranked else None

        # AI safety recommendation messages based on zone
        AI_RECOMMENDATIONS = {
            "safe": "This route is well-lit and frequently used. It is recommended for safer travel.",
            "moderate": "This route has moderate safety conditions. Stay aware of surroundings.",
            "unsafe": "This route passes through higher-risk areas. Consider taking precautions such as staying alert or carrying personal safety tools.",
        }
        ai_message = AI_RECOMMENDATIONS.get(ai_best["zone"], AI_RECOMMENDATIONS["moderate"]) if ai_best else None
        if ai_best:
            ai_best = {**ai_best, "ai_message": ai_message}

        return jsonify(
            {
                "routes": routes_out,
                "ai_recommendation": ai_best,
                "ai_message": ai_message,
            }
        )

    @app.route("/api/report", methods=["POST"])
    def report():
        payload = request.get_json(silent=True) or {}
        lat = payload.get("lat")
        lng = payload.get("lng")
        place_name = (payload.get("place_name") or "").strip()[:120]
        description = (payload.get("description") or "").strip()
        rating = payload.get("rating")  # 1..5 (user perception)

        if lat is None or lng is None:
            return jsonify({"error": "lat and lng are required"}), 400

        record = {
            "id": f"rep_{int(datetime.now(tz=timezone.utc).timestamp() * 1000)}",
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "lat": float(lat),
            "lng": float(lng),
            "place_name": place_name,
            "description": description[:300],
            "rating": int(rating) if rating is not None else None,
        }
        _append_jsonl(USER_REPORTS_PATH, record)
        return jsonify({"ok": True, "report": record})

    @app.route("/api/score_route", methods=["POST"])
    def score_route():
        payload = request.get_json(silent=True) or {}
        coords = payload.get("coordinates")  # expected: [[lng, lat], ...]
        if not isinstance(coords, list) or len(coords) < 2:
            return jsonify({"error": "coordinates must be a list of [lng, lat] with length >= 2"}), 400

        max_distance_m = float(payload.get("max_distance_m", 280.0))
        weights = payload.get("weights")
        if weights is not None and not isinstance(weights, dict):
            return jsonify({"error": "weights must be an object"}), 400
        points = _read_json(SAFETY_POINTS_PATH, default=[])
        nearby = _route_nearby_points(coords, points, max_distance_m=max_distance_m, weights=weights)
        fallback_pts = []
        if not nearby:
            fallback_pts = _route_nearby_points(coords, points, max_distance_m=500.0, weights=weights)

        route_score = _route_safety_score(nearby, fallback_pts if fallback_pts else None)
        route_score = float(_clamp(route_score, 0.0, 100.0))
        label = _zone_label_from_percent(route_score)

        # Surface the most risky nearby points
        worst = sorted(nearby, key=lambda p: float(p.get("safety_percent", 0.0)))[:10]

        return jsonify(
            {
                "route_score": round(route_score, 1),
                "zone": label,
                "nearby_points_count": len(nearby),
                "nearby_points": nearby,
                "worst_points": worst,
            }
        )

    return app


if __name__ == "__main__":
    app = create_app()
    # Runs on http://127.0.0.1:5000 with debug enabled
    app.run(host="127.0.0.1", port=5000, debug=True)
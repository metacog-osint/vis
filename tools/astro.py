"""Sun and moon for illumination tables: positions, twilight, moonrise and moonset, and the lit fraction of the moon.

Low-precision formulas, the same family SunCalc (BSD-2) and the Astronomical Almanac's low-precision pages use: sun to
about 0.01 deg (Meeus), moon to about 0.3 deg (the main periodic terms only). Rise and set times come out within a few
minutes, which is what an illumination table needs. Standard library only; all times UTC.
"""
import math
from datetime import datetime, timedelta, timezone

D2R, R2D = math.pi / 180, 180 / math.pi
OBLIQ = 23.4397 * D2R


def jd(t):
    return t.timestamp() / 86400.0 + 2440587.5


def sidereal_deg(t, lon):
    d = jd(t) - 2451545.0
    T = d / 36525.0
    return (280.46061837 + 360.98564736629 * d + 0.000387933 * T * T + lon) % 360.0


def sun_radec(t):
    n = jd(t) - 2451545.0
    T = n / 36525.0
    L0 = (280.46646 + 36000.76983 * T) % 360
    M = math.radians((357.52911 + 35999.05029 * T) % 360)
    C = (1.914602 - 0.004817 * T) * math.sin(M) + 0.019993 * math.sin(2 * M) + 0.000289 * math.sin(3 * M)
    om = math.radians(125.04 - 1934.136 * T)
    lam = math.radians(L0 + C - 0.00569 - 0.00478 * math.sin(om))
    eps = OBLIQ + math.radians(0.00256 * math.cos(om))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    return ra, dec, 149598000.0


def moon_radec(t):
    """The main periodic terms (Meeus, Astronomical Algorithms ch. 47): equation of centre, evection, variation,
    annual equation and the reduction to the ecliptic; about 0.3 deg."""
    d = jd(t) - 2451545.0
    L = math.radians(218.316 + 13.176396 * d)
    M = math.radians(134.963 + 13.064993 * d)          # moon's mean anomaly
    F = math.radians(93.272 + 13.229350 * d)           # argument of latitude
    D = math.radians(297.850 + 12.190749 * d)          # mean elongation
    S = math.radians(357.529 + 0.98560028 * d)         # sun's mean anomaly
    lam = L + math.radians(6.289 * math.sin(M) + 1.274 * math.sin(2 * D - M) + 0.658 * math.sin(2 * D)
                           + 0.214 * math.sin(2 * M) - 0.186 * math.sin(S) - 0.114 * math.sin(2 * F))
    beta = math.radians(5.128 * math.sin(F) + 0.281 * math.sin(M + F) + 0.278 * math.sin(M - F) + 0.173 * math.sin(2 * D - F))
    dist = 385001 - 20905 * math.cos(M) - 3699 * math.cos(2 * D - M) - 2956 * math.cos(2 * D)
    ra = math.atan2(math.sin(lam) * math.cos(OBLIQ) - math.tan(beta) * math.sin(OBLIQ), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(OBLIQ) + math.cos(beta) * math.sin(OBLIQ) * math.sin(lam))
    return ra, dec, dist


def altitude(ra, dec, t, lat, lon):
    H = math.radians(sidereal_deg(t, lon)) - ra
    la = math.radians(lat)
    return math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(H)) * R2D


def sun_alt(t, lat, lon):
    ra, dec, _ = sun_radec(t)
    return altitude(ra, dec, t, lat, lon)


def moon_alt(t, lat, lon):
    """Topocentric altitude of the moon's centre (parallax applied, refraction not)."""
    ra, dec, dist = moon_radec(t)
    h = altitude(ra, dec, t, lat, lon)
    return h - math.degrees(math.asin(6378.14 / dist)) * math.cos(math.radians(h))


def sun_lon(t):
    """Sun's apparent ecliptic longitude, degrees (the same terms as sun_radec)."""
    T = (jd(t) - 2451545.0) / 36525.0
    L0 = (280.46646 + 36000.76983 * T) % 360
    M = math.radians((357.52911 + 35999.05029 * T) % 360)
    C = (1.914602 - 0.004817 * T) * math.sin(M) + 0.019993 * math.sin(2 * M) + 0.000289 * math.sin(3 * M)
    om = math.radians(125.04 - 1934.136 * T)
    return (L0 + C - 0.00569 - 0.00478 * math.sin(om)) % 360


def moon_lon(t):
    """Moon's ecliptic longitude, degrees (the same main terms as moon_radec)."""
    d = jd(t) - 2451545.0
    L = 218.316 + 13.176396 * d
    M = math.radians(134.963 + 13.064993 * d)
    F = math.radians(93.272 + 13.229350 * d)
    D = math.radians(297.850 + 12.190749 * d)
    S = math.radians(357.529 + 0.98560028 * d)
    return (L + 6.289 * math.sin(M) + 1.274 * math.sin(2 * D - M) + 0.658 * math.sin(2 * D)
            + 0.214 * math.sin(2 * M) - 0.186 * math.sin(S) - 0.114 * math.sin(2 * F)) % 360


PHASES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
          "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]


def moon_phase(t):
    """The moon's phase at t: {"angle": elongation east of the sun 0-360 deg (0 new, 90 first quarter, 180 full,
    270 last quarter), "name": one of PHASES (each name covers 45 deg centred on its point), "waxing": bool}."""
    a = (moon_lon(t) - sun_lon(t)) % 360
    return {"angle": round(a, 1), "name": PHASES[int(((a + 22.5) % 360) // 45)], "waxing": a < 180}


def moon_fraction(t):
    """Lit fraction of the moon's disc, 0 to 1."""
    sra, sdec, sdist = sun_radec(t)
    mra, mdec, mdist = moon_radec(t)
    psi = math.acos(max(-1.0, min(1.0, math.sin(sdec) * math.sin(mdec) + math.cos(sdec) * math.cos(mdec) * math.cos(sra - mra))))
    inc = math.atan2(sdist * math.sin(psi), mdist - sdist * math.cos(psi))
    return (1 + math.cos(inc)) / 2


SUN_EVENTS = [("sunset", -0.833), ("civil", -6.0), ("nautical", -12.0), ("astronomical", -18.0)]
MOON_H0 = -0.825    # deg: the topocentric centre's altitude when the upper limb touches the horizon (refraction 34', semi-diameter 15.5')


def crossings(f, t0, t1, h, step_min=5):
    """Times f crosses h between t0 and t1: [(time, rising)], refined by bisection to under a minute."""
    out, t = [], t0
    prev = f(t) - h
    while t < t1:
        tn = t + timedelta(minutes=step_min)
        cur = f(tn) - h
        if (prev < 0) != (cur < 0):
            a, b = t, tn
            for _ in range(10):
                m = a + (b - a) / 2
                if (f(m) - h < 0) == (prev < 0):
                    a = m
                else:
                    b = m
            out.append((a + (b - a) / 2, prev < 0))
        t, prev = tn, cur
    return out


def night(lat, lon, day):
    """Illumination for the night that follows `day` (a UTC date) at a place: twilight times, moon times, the moon's
    lit fraction at local midnight, and minutes of dark (sun below -12 deg) with the moon down or under a quarter lit.
    The night runs from local solar noon to the next local solar noon."""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc) - timedelta(hours=lon / 15.0)
    end = noon + timedelta(days=1)
    s = lambda t: sun_alt(t, lat, lon)  # noqa: E731
    m = lambda t: moon_alt(t, lat, lon)  # noqa: E731
    out = {"day": day.strftime("%Y-%m-%d"), "local_noon_utc": noon.strftime("%Y-%m-%dT%H:%MZ")}
    for name, h in SUN_EVENTS:
        c = crossings(s, noon, end, h)
        out[name + "_dusk"] = next((x[0].strftime("%Y-%m-%dT%H:%MZ") for x in c if not x[1]), None)
        out[name + "_dawn"] = next((x[0].strftime("%Y-%m-%dT%H:%MZ") for x in c if x[1]), None)
    mc = crossings(m, noon, end, MOON_H0)
    out["moonrise"] = [x[0].strftime("%Y-%m-%dT%H:%MZ") for x in mc if x[1]]
    out["moonset"] = [x[0].strftime("%Y-%m-%dT%H:%MZ") for x in mc if not x[1]]
    midnight = noon + timedelta(hours=12)
    out["moon_lit"] = round(moon_fraction(midnight), 3)
    ph = moon_phase(midnight)
    out["moon_phase"] = ph["name"]
    out["moon_waxing"] = ph["waxing"]
    dark = dark_low = 0
    t = noon
    while t < end:
        if s(t) < -12.0:
            dark += 5
            if m(t) < 0 or moon_fraction(t) < 0.25:
                dark_low += 5
        t += timedelta(minutes=5)
    out["dark_min"] = dark
    out["dark_low_light_min"] = dark_low
    return out


def sun_day(lat, lon, day):
    """Sunrise, sunset, solar noon and day length for the local solar day of `day` (a UTC date): the 24 hours from
    local solar midnight, lon/15 hours off UTC midnight. Rise and set use the -0.833 deg standard altitude
    (refraction and the sun's semi-diameter). When the sun never rises or never sets, `polar` says which."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=lon / 15.0)
    end = start + timedelta(days=1)
    s = lambda t: sun_alt(t, lat, lon)  # noqa: E731
    c = crossings(s, start, end, -0.833)
    rise = next((x[0] for x in c if x[1]), None)
    sset = next((x[0] for x in c if not x[1] and (rise is None or x[0] > rise)), None)
    t, best, best_t = start, -99.0, start
    while t < end:                    # solar noon: the highest sun, to 5 minutes, then refined by golden-section
        a = s(t)
        if a > best:
            best, best_t = a, t
        t += timedelta(minutes=5)
    lo, hi = best_t - timedelta(minutes=5), best_t + timedelta(minutes=5)
    for _ in range(12):
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if s(m1) < s(m2):
            lo = m1
        else:
            hi = m2
    noon = lo + (hi - lo) / 2
    fmt = lambda x: x.strftime("%Y-%m-%dT%H:%MZ") if x else None  # noqa: E731
    out = {"day": day.strftime("%Y-%m-%d"), "sunrise": fmt(rise), "sunset": fmt(sset), "solar_noon": fmt(noon),
           "noon_altitude_deg": round(s(noon), 1), "day_length_min": None, "polar": None}
    if rise and sset:
        out["day_length_min"] = round((sset - rise).total_seconds() / 60)
    elif not c:
        out["polar"] = "midnight_sun" if s(noon) > -0.833 else "polar_night"
        out["day_length_min"] = 1440 if out["polar"] == "midnight_sun" else 0
    return out

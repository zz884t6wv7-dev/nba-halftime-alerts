
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ODDS_URL = (
    "https://api.the-odds-api.com/v4/sports/"
    "basketball_nba/odds"
)
SCORE_URL = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/scoreboard"
)
CACHE_FILE = "odds_cache.json"
STATE_FILE = "nba_state.json"
THRESHOLD = 0.70



def get_json(url, headers=None):
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP ERROR: {e.code}")
        print(f"URL: {req.full_url.split('?')[0]}")
        print(f"SERVER MESSAGE: {e.read().decode('utf-8', errors='replace')[:500]}")
        raise



def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def american_probability(price):
    price = float(price)
    if price < 0:
        return -price / (-price + 100)
    return 100 / (price + 100)


def refresh_odds(api_key):
    params = urllib.parse.urlencode({
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
    })
    events = get_json(f"{ODDS_URL}?{params}")
    snapshot = {
        "date_et": datetime.now(ET).date().isoformat(),
        "events": []
    }

    for event in events:
        team_probs = {}
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue
                raw = {
                    o["name"]: american_probability(o["price"])
                    for o in outcomes
                }
                total = sum(raw.values())
                if total <= 0:
                    continue
                for name, p in raw.items():
                    team_probs.setdefault(name, []).append(p / total)

        if len(team_probs) != 2:
            continue

        averaged = {
            name: sum(values) / len(values)
            for name, values in team_probs.items() if values
        }
        if len(averaged) != 2:
            continue

        favorite = max(averaged, key=averaged.get)
        probability = averaged[favorite]
        if probability > THRESHOLD:
            snapshot["events"].append({
                "id": event.get("id"),
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "commence_time": event.get("commence_time"),
                "favorite": favorite,
                "probability": round(probability, 4),
            })

    save_json(CACHE_FILE, snapshot)
    print(f"Zapisano kursy. Faworyci >70%: {len(snapshot['events'])}")
    return snapshot


def load_odds(api_key):
    today = datetime.now(ET).date().isoformat()
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date_et") == today:
                return cache
        except (OSError, ValueError):
            pass
    return refresh_odds(api_key)



def normalize(text):
    name = " ".join((text or "").lower().split())

    aliases = {
        "los angeles clippers": "la clippers",
        "la clippers": "la clippers",
        "los angeles lakers": "la lakers",
        "la lakers": "la lakers",
    }

    return aliases.get(name, name)



def team_full_name(team):
    return normalize(
        f"{team.get('teamCity', '')} {team.get('teamName', '')}"
    )


def send_alert(topic, message):
    url = f"https://ntfy.sh/{urllib.parse.quote(topic, safe='')}"
    request = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers={
            "Title": "Alert NBA — faworyt zagrożony",
            "Priority": "high",
            "Tags": "basketball",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        print(f"ntfy status: {response.status}")


def main():
    api_key = os.environ.get("ODDS_API_KEY")
    topic = os.environ.get("NTFY_TOPIC")
    if not api_key or not topic:
        raise RuntimeError("Brakuje ODDS_API_KEY lub NTFY_TOPIC.")

    mode = os.environ.get("MODE", "monitor").lower()
    if mode == "refresh":
        refresh_odds(api_key)
        return

    
    odds = load_odds(api_key)
    scoreboard = get_json(SCORE_URL)
    games = scoreboard.get("events", [])

    state = {"sent": []}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            pass

    sent = set(state.get("sent", []))
    alerted = 0

    for game in games:       
        print(
            "MECZ:",
            game.get("name"),
            "| STATUS:",
            game.get("status", {}).get("type", {}).get("description"),
            "| SZCZEGÓŁY:",
            game.get("status", {}).get("type", {}).get("detail"),
        )
        status_info = game.get("status", {})
        status_type = status_info.get("type", {})
        status_text = normalize(
            status_type.get("description", "")
            or status_type.get("detail", "")
        )


    for game in games:
        status_info = game.get("status", {})
        status_type = status_info.get("type", {})
        status_text = normalize(
            status_type.get("description", "")
            or status_type.get("detail", "")
        )

        if "halftime" not in status_text:
            continue

        competitions = game.get("competitions", [])
        if not competitions:
            continue

        competitors = competitions[0].get("competitors", [])
        home_data = next(
            (c for c in competitors if c.get("homeAway") == "home"),
            None,
        )
        away_data = next(
            (c for c in competitors if c.get("homeAway") == "away"),
            None,
        )

        if not home_data or not away_data:
            continue

        home = home_data.get("team", {})
        away = away_data.get("team", {})
        home_name = team_full_name(home)
        away_name = team_full_name(away)

        home_score = int(home_data.get("score") or 0)
        away_score = int(away_data.get("score") or 0)

        for event in odds.get("events", []):
            event_id = event.get("id")
            if event_id in sent:
                continue

            favorite = normalize(event.get("favorite"))
            if favorite not in (home_name, away_name):
                continue

            if favorite == home_name:
                fav_score, opp_score = home_score, away_score
                fav_display = home.get("displayName", home_name)
                opponent = away.get("displayName", away_name)
            else:
                fav_score, opp_score = away_score, home_score
                fav_display = away.get("displayName", away_name)
                opponent = home.get("displayName", home_name)

            if fav_score <= opp_score:
                pct = round(event["probability"] * 100)
                message = (
                    f"{fav_display} — {pct}% szans przed meczem\n"
                    f"Do przerwy: {fav_score}:{opp_score} "
                    f"z {opponent}\n"
                    "Faworyt przegrywa lub remisuje do przerwy."
                )
                send_alert(topic, message)
                sent.add(event_id)
                alerted += 1
                break

    state["sent"] = list(sent)[-500:]
    save_json(STATE_FILE, state)
    print(f"Sprawdzono {len(games)} wydarzeń; wysłane alerty: {alerted}")

    # Keep recent IDs to prevent duplicate alerts.
    state["sent"] = list(sent)[-500:]
    save_json(STATE_FILE, state)
    print(f"Sprawdzono {len(games)} meczów; wysłane alerty: {alerted}")


if __name__ == "__main__":
    main()

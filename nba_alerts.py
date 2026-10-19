
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
    "https://cdn.nba.com/static/json/liveData/"
    "scoreboard/todaysScoreboard_00.json"
)
CACHE_FILE = "odds_cache.json"
STATE_FILE = "nba_state.json"
THRESHOLD = 0.70


def get_json(url, headers=None):
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": "NBAHalftimeAlerts/1.0"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


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
        if probability >= THRESHOLD:
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
    return " ".join((text or "").lower().split())


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
    games = scoreboard.get("scoreboard", {}).get("games", [])

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
        status = normalize(game.get("gameStatusText", ""))
        period = game.get("period", 0)

        # Alert only at halftime, not at the end of any other quarter.
        if "halftime" not in status and not (
            period == 2 and status in ("end of 2nd quarter", "end of 2nd")
        ):
            continue

        home = game.get("homeTeam", {})
        away = game.get("awayTeam", {})
        home_name = team_full_name(home)
        away_name = team_full_name(away)

        home_score = int(home.get("score") or 0)
        away_score = int(away.get("score") or 0)

        for event in odds.get("events", []):
            if event.get("id") in sent:
                continue

            favorite = normalize(event.get("favorite"))
            if favorite not in (home_name, away_name):
                continue

            # Trigger if the pre-game favorite is tied or behind.
            if favorite == home_name:
                fav_score, opp_score = home_score, away_score
                fav_display = (
                    f"{home.get('teamCity', '')} {home.get('teamName', '')}"
                ).strip()
                opponent = (
                    f"{away.get('teamCity', '')} {away.get('teamName', '')}"
                ).strip()
            else:
                fav_score, opp_score = away_score, home_score
                fav_display = (
                    f"{away.get('teamCity', '')} {away.get('teamName', '')}"
                ).strip()
                opponent = (
                    f"{home.get('teamCity', '')} {home.get('teamName', '')}"
                ).strip()

            if fav_score <= opp_score:
                pct = round(event["probability"] * 100)
                message = (
                    f"{fav_display} — {pct}% szans przed meczem\n"
                    f"Do przerwy: {fav_score}:{opp_score} "
                    f"z {opponent}\n"
                    f"Faworyt przegrywa lub remisuje do przerwy."
                )
                send_alert(topic, message)
                sent.add(event["id"])
                alerted += 1
                break

    # Keep recent IDs to prevent duplicate alerts.
    state["sent"] = list(sent)[-500:]
    save_json(STATE_FILE, state)
    print(f"Sprawdzono {len(games)} meczów; wysłane alerty: {alerted}")


if __name__ == "__main__":
    main()

rop-in replacement for the /api/leaderboard route in server.py.
Paste this block after the /api/rollover route.

Response shape (200 OK):
{
  "week_no": null | int,
  "generated_at": "2026-04-04T18:30:00+00:00",
  "meta": {
    "league_avg":   472.5,
    "top_score":    484,
    "member_count": 2
  },
  "standings": [
    {
      "rank": 1,
      "name": "Sai",
      "total_pts": 484,
      "matches_counted": 1,
      "mvp": {"player_id": "r01", "player_name": "Virat Kohli", "pts": 220}
    },
    {
      "rank": 2,
      "name": "Moe",
      "total_pts": 461,
      "matches_counted": 1,
      "mvp": {"player_id": "s03", "player_name": "Ishan Kishan", "pts": 174}
    }
  ]
}

DENSE_RANK tie behaviour:
  Two users with identical total_pts share the same rank integer.
  e.g. Sai=484 rank=1, Moe=461 rank=2 (no tie → sequential).
  If both had 484: both rank=1, next user rank=2 (no gap).
"""

import sqlite3
from flask import request, jsonify


@app.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    """
    GET /api/leaderboard          → global leaderboard (all weeks)
    GET /api/leaderboard?week=N   → week N only

    Query params
    ------------
    week : int (optional)
        When supplied and a valid positive integer, restricts scoring to
        matches in that week only.  Omit (or pass a non-integer) for the
        all-season aggregate view.

    Errors
    ------
    500  Database error (sqlite3.Error)
    500  Unexpected server error
    """
    try:
        week_param = request.args.get("week", "").strip()
        week_no    = int(week_param) if week_param.isdigit() else None

        result = db.get_leaderboard(week_no=week_no)
        return jsonify(result), 200

    except sqlite3.Error as e:
        _log(f"GET /api/leaderboard DB error: {e}", "error")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        _log(f"GET /api/leaderboard failed: {e}", "error")
        return jsonify({"error": str(e)}), 500
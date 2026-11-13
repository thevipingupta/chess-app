#!/usr/bin/env python3
"""Synthetic contact center source generator (contact-center-medallion-demo).

Produces four reproducible source feeds for 14 weeks (2026-03-02 .. 2026-06-07)
as daily batch files, plus a reason-code reference and a log of planted data
quality issues. Every identifier and name is synthetic. Standard library only.

Outputs (under data/source/ by default):
    acd/        ACD queue interval export (CSV), one row per queue per 30 min
    contacts/   Contact records (JSON Lines), one line per contact
    wfm/        Workforce management export (CSV), one row per agent per day
    roster/     Agent roster SCD2 feed (CSV), delta rows on change dates
    _reference/reason_codes.csv
    _planted_issues.csv

Run from the project root:
    python src/generate/generate_sources.py
    python src/generate/generate_sources.py --seed 7 --out <scratch dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEFAULT_SEED = 20260302
START_DATE = date(2026, 3, 2)              # Monday -> batch B001
WEEKS = 14
DAYS = WEEKS * 7                           # 2026-03-02 .. 2026-06-07
POLICY_DEADLINE = date(2026, 4, 17)        # Friday of week 7
SURGE_DAYS = 10                            # surge window ends on the deadline
SURGE_START = POLICY_DEADLINE - timedelta(days=SURGE_DAYS - 1)
SHORTFALL_WEEK = 10                        # 1-based; 2026-05-04 .. 2026-05-10
LATE_ACD_DAY_INDEX = 28                    # 2026-03-30 ACD file lands one batch late
REPEAT_PROBABILITY = 0.235                 # share of case-requiring contacts that repeat (~15% of all)
CASE_WINDOW = timedelta(days=7)
SL_SECONDS = 20
ABANDON_THRESHOLD_SHARE = 0.30             # callers who give up near the 180 s mark
DUPLICATE_RATE = 0.005                     # duplicate contact lines in JSON files
CASE_NULL_RATE = 0.01                      # null linked_case_ref on case-requiring reasons

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
OPEN_SECONDS = 8 * 3600                    # 08:00 local
N_INTERVALS = 20                           # 08:00-18:00 in 30-minute intervals
INTERVAL_SECONDS = 1800
INTERVAL_WEIGHTS = (
    0.55, 0.80, 1.00, 1.10, 1.15, 1.15, 1.10, 0.85, 0.60, 0.60,   # lunch dip 11:30-13:00
    0.80, 1.00, 1.10, 1.10, 1.05, 1.00, 0.90, 0.75, 0.60, 0.45,
)
WEIGHT_TOTAL = sum(INTERVAL_WEIGHTS)
DOW_FACTOR = {0: 1.25, 1: 1.10, 2: 1.05, 3: 1.00, 4: 0.90, 5: 0.35, 6: 0.30}  # Monday peak

# code -> (description, requires_case)
REASON_CODES = {
    "CLM-STATUS": ("Claim status inquiry", True),
    "CLM-PAYMENT": ("Benefit payment question", True),
    "CLM-DOC": ("Document upload follow-up", True),
    "CLM-APPEAL": ("Appeal or determination dispute", True),
    "CLM-NEW": ("New claim intake assistance", True),
    "EMP-NOTICE": ("Employer leave notice question", True),
    "EMP-WAGE": ("Wage statement question", True),
    "EMP-ACCT": ("Employer account access", False),
    "PRV-CERT": ("Provider certification question", True),
    "GEN-PWD": ("Account or password help", False),
    "GEN-INFO": ("General program information", False),
}
CUSTOMER_REASON_WEIGHTS = {
    "claimant": (("CLM-STATUS", .28), ("CLM-PAYMENT", .18), ("CLM-DOC", .12),
                 ("CLM-APPEAL", .05), ("CLM-NEW", .10), ("GEN-PWD", .12), ("GEN-INFO", .15)),
    "employer": (("EMP-NOTICE", .35), ("EMP-WAGE", .30), ("EMP-ACCT", .15), ("GEN-INFO", .20)),
    "provider": (("PRV-CERT", .70), ("GEN-INFO", .30)),
}
ANSWERED_DISPOSITIONS = (
    ("RESOLVED", .55), ("INFO_PROVIDED", .20), ("ESCALATED", .10),
    ("CALLBACK_SCHEDULED", .10), ("TRANSFERRED", .05),
)


@dataclass(frozen=True)
class Queue:
    queue_id: str
    name: str
    channel: str
    acd: bool                 # True if measured in the ACD interval export
    team: str | None          # team that answers the queue
    base_daily: int           # average contacts per weekday before factors
    aht_seconds: int          # average handle time (0 for self-service)
    customer_mix: tuple       # ((customer_type, weight), ...)


QUEUES = (
    Queue("Q_PHONE_CLM", "Claimant Phone Line", "phone", True, "CLAIMANT", 380, 420,
          (("claimant", .85), ("employer", .10), ("provider", .05))),
    Queue("Q_PHONE_EMP", "Employer Phone Line", "phone", True, "EMPLOYER", 220, 360,
          (("employer", .85), ("provider", .10), ("claimant", .05))),
    Queue("Q_CHAT", "Chat Support", "chat", True, "CHAT_MSG", 160, 540,
          (("claimant", .60), ("employer", .30), ("provider", .10))),
    Queue("Q_SECURE_MSG", "Secure Message Inbox", "secure_message", False, "CHAT_MSG", 150, 300,
          (("claimant", .70), ("employer", .20), ("provider", .10))),
    Queue("Q_WEB_INTAKE", "Web Form Intake", "web_form", False, None, 120, 0,
          (("claimant", .60), ("employer", .30), ("provider", .10))),
)

ISSUE_RULES = {
    "duplicate_contact_id": (
        "Bronze: GROUP BY contact_id HAVING COUNT(*) > 1 across CONTACT files. "
        "Silver: keep one row per contact_id (dedupe on contact_id, then _record_hash)."),
    "null_linked_case_ref": (
        "Silver DQ: linked_case_ref IS NULL where contact_reason_code has requires_case = true "
        "in _reference/reason_codes.csv. Route to quarantine with a reason code."),
    "negative_handle_seconds": (
        "Silver DQ: handle_seconds < 0. Route to quarantine with a reason code."),
    "agent_not_in_roster": (
        "Silver DQ: agent_id has no roster row whose effective_start <= contact date <= "
        "effective_end. Route to quarantine with a reason code."),
    "service_level_above_100": (
        "Silver DQ: service_level_pct > 100 or < 0 in ACD files. Route to quarantine."),
    "late_acd_file": (
        "Batch completeness check: each business date should land in the ACD file for its own "
        "batch. Flag B029 (no ACD file) and B030 (ACD file dated 2026-03-30 in addition to 2026-03-31)."),
}

ACD_COLUMNS = ["interval_start_local", "queue_id", "queue_name", "calls_offered", "calls_answered",
               "calls_abandoned", "avg_wait_seconds", "max_wait_seconds", "service_level_pct"]
WFM_COLUMNS = ["work_date", "agent_id", "team", "scheduled_minutes", "logged_in_minutes",
               "adherence_pct", "occupancy_pct", "shift_type"]
ROSTER_COLUMNS = ["agent_id", "agent_name_masked", "team", "hire_date", "effective_start",
                  "effective_end", "is_current"]
PLANTED_COLUMNS = ["issue_id", "issue_type", "source", "batch_id", "file_name", "line_number",
                   "record_key", "detail", "expected_detection_rule"]

# --------------------------------------------------------------------------
# Agents and roster
# --------------------------------------------------------------------------
INITIAL_AGENTS = (
    [(f"AGT-{i:04d}", "CLAIMANT") for i in range(1, 10)]
    + [(f"AGT-{i:04d}", "EMPLOYER") for i in range(10, 15)]
    + [(f"AGT-{i:04d}", "CHAT_MSG") for i in range(15, 22)]
)
TEAM_TRANSFERS = {                         # agent -> (effective date, new team)
    "AGT-0013": (date(2026, 3, 16), "CHAT_MSG"),
    "AGT-0004": (date(2026, 4, 6), "EMPLOYER"),
    "AGT-0019": (date(2026, 5, 11), "CLAIMANT"),
}
HIRES = (
    ("AGT-0023", "CLAIMANT", date(2026, 4, 27)),
    ("AGT-0022", "CHAT_MSG", date(2026, 5, 18)),
)
LEAVERS = {"AGT-0021": date(2026, 4, 24)}  # first day no longer employed


@dataclass
class Version:
    team: str
    start: date
    end: date | None


@dataclass
class Agent:
    agent_id: str
    name_masked: str
    hire_date: date
    versions: list
    part_time: bool
    start_hour: int

    @property
    def shift(self):
        length = 5 if self.part_time else 8
        tag = "PT" if self.part_time else "FT"
        stype = f"{tag}_{self.start_hour:02d}00_{self.start_hour + length:02d}00"
        return stype, self.start_hour, length

    def team_on(self, day: date):
        for v in self.versions:
            if v.start <= day and (v.end is None or day <= v.end):
                return v.team
        return None


@dataclass
class Shift:
    agent_id: str
    team: str | None
    off: bool
    shift_type: str
    scheduled_min: int
    login: int                 # seconds from local midnight (includes late start)
    logout: int
    adherence: float
    free_at: int = 0


def masked_name(rng: random.Random) -> str:
    return f"{chr(rng.randint(65, 90))}*** {chr(rng.randint(65, 90))}."


def build_agents(rng: random.Random) -> dict:
    raw = {}
    for aid, team in INITIAL_AGENTS:
        hire = date(2019, 1, 1) + timedelta(days=rng.randint(0, 2370))
        raw[aid] = [hire, [Version(team, hire, None)]]
    for aid, team, hire in HIRES:
        raw[aid] = [hire, [Version(team, hire, None)]]
    for aid, (eff, team) in TEAM_TRANSFERS.items():
        versions = raw[aid][1]
        versions[-1].end = eff - timedelta(days=1)
        versions.append(Version(team, eff, None))
    for aid, first_day_out in LEAVERS.items():
        raw[aid][1][-1].end = first_day_out - timedelta(days=1)

    agents = {}
    for aid in sorted(raw):
        hire, versions = raw[aid]
        part_time = rng.random() < 0.20
        start_hour = rng.choice((13, 8)) if part_time else rng.choice((8, 9, 10))
        agents[aid] = Agent(aid, masked_name(rng), hire, versions, part_time, start_hour)
    return agents


def _roster_row(ag: Agent, v: Version, end_str: str, current: bool) -> dict:
    return {
        "agent_id": ag.agent_id,
        "agent_name_masked": ag.name_masked,
        "team": v.team,
        "hire_date": ag.hire_date.isoformat(),
        "effective_start": v.start.isoformat(),
        "effective_end": end_str,
        "is_current": "true" if current else "false",
    }


def roster_events(agents: dict) -> dict:
    """SCD2 feed: each version is sent open when created and again closed when superseded."""
    events = defaultdict(list)
    for aid in sorted(agents):
        ag = agents[aid]
        for i, v in enumerate(ag.versions):
            nxt = ag.versions[i + 1] if i + 1 < len(ag.versions) else None
            events[max(v.start, START_DATE)].append(_roster_row(ag, v, "9999-12-31", True))
            if nxt is not None:
                closed_day, closed_end = nxt.start, nxt.start - timedelta(days=1)
            elif v.end is not None:
                closed_day, closed_end = v.end + timedelta(days=1), v.end
            else:
                continue
            events[closed_day].append(_roster_row(ag, v, closed_end.isoformat(), False))
    return events


def build_week_patterns(rng: random.Random, agent_ids: list) -> dict:
    """Per agent, per week: the set of weekday numbers (0=Mon) worked."""
    patterns = {}
    for aid in agent_ids:
        weeks = []
        for _ in range(WEEKS):
            if rng.random() < 0.25:   # weekend crew: 3 weekdays plus Saturday and Sunday
                weeks.append(set(rng.sample(range(5), 3)) | set(rng.sample((5, 6), 2)))
            else:
                weeks.append(set(range(5)))
        patterns[aid] = weeks
    return patterns


# --------------------------------------------------------------------------
# Random helpers
# --------------------------------------------------------------------------
def weighted_choice(rng: random.Random, pairs):
    items, weights = zip(*pairs)
    return rng.choices(items, weights=weights, k=1)[0]


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 60:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def lognormal_with_mean(rng: random.Random, mean_s: float, sigma: float) -> float:
    return rng.lognormvariate(math.log(mean_s) - sigma * sigma / 2, sigma)


def sample_patience(rng: random.Random) -> float:
    """Seconds a caller will wait. A share give up near the 180 s mark, so abandonment rises there."""
    if rng.random() < ABANDON_THRESHOLD_SHARE:
        return rng.uniform(150, 210)
    return min(1800.0, rng.expovariate(1 / 900))


def surge_multiplier(day: date) -> float:
    if SURGE_START <= day <= POLICY_DEADLINE:
        k = (day - SURGE_START).days           # 0 .. SURGE_DAYS-1
        return 2.0 + k / (SURGE_DAYS - 1)      # ramps 2.0x -> 3.0x into the deadline
    return 1.0


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
@dataclass
class Contact:
    day_index: int
    queue_id: str
    channel: str
    ts_local: datetime
    ts_utc: datetime
    customer_type: str
    reason_code: str
    outcome: str                       # ANSWERED | ABANDONED | SELF
    agent_id: str | None
    handle_seconds: int
    disposition: str
    wait_seconds: float | None
    linked_case_ref: str | None = None
    contact_id: str | None = None
    planted: list = field(default_factory=list)


class Generator:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.agents = build_agents(self.rng)
        self.patterns = build_week_patterns(self.rng, list(self.agents))
        self.roster_by_day = roster_events(self.agents)
        self.contacts: list[Contact] = []
        self.acd_by_day: dict[int, list] = {}
        self.wfm_by_day: dict[int, list] = {}
        self.repeat_contacts = 0
        self.case_count = 0

    def run(self) -> "Generator":
        for d in range(DAYS):
            self._simulate_day(d)
        return self

    def _build_shifts(self, day: date, week0: int) -> dict:
        rng = self.rng
        shortfall = week0 + 1 == SHORTFALL_WEEK and day.weekday() < 5
        shifts = {}
        for aid, ag in self.agents.items():
            team = ag.team_on(day)
            if team is None:
                continue
            off = day.weekday() not in self.patterns[aid][week0]
            if not off and shortfall and team == "CLAIMANT" and rng.random() < 0.40:
                off = True                             # unfilled claimant shifts
            stype, start_h, length = ag.shift
            if off:
                shifts[aid] = Shift(aid, team, True, "OFF", 0, 0, 0, 0.0)
                continue
            late = rng.randint(3, 12) * 60 if rng.random() < 0.15 else 0
            login = start_h * 3600 + late
            logout = (start_h + length) * 3600
            if shortfall and team == "CLAIMANT":
                adh = rng.uniform(74, 86)
            else:
                adh = rng.uniform(88, 97)
            shifts[aid] = Shift(aid, team, False, stype, length * 60, login, logout,
                                round(adh, 1), login)
        return shifts

    def _simulate_day(self, d: int) -> None:
        rng = self.rng
        day = START_DATE + timedelta(days=d)
        shifts = self._build_shifts(day, d // 7)
        surge = surge_multiplier(day)
        dow = DOW_FACTOR[day.weekday()]
        handled = defaultdict(int)
        acd_rows = []
        for q in QUEUES:
            daily = q.base_daily * dow * surge * rng.lognormvariate(0, 0.07)
            arrivals = []
            for i, w in enumerate(INTERVAL_WEIGHTS):
                for _ in range(poisson(rng, daily * w / WEIGHT_TOTAL)):
                    sec = OPEN_SECONDS + i * INTERVAL_SECONDS + rng.random() * INTERVAL_SECONDS
                    arrivals.append((sec, i))
            arrivals.sort()
            pool = [s for s in shifts.values() if q.team and s.team == q.team and not s.off]
            if q.acd:
                outcomes = self._simulate_acd(arrivals, pool, q)
            elif q.channel == "secure_message":
                outcomes = self._simulate_messages(arrivals, pool, q)
            else:
                outcomes = [(sec, i, "SELF", None, 0, None) for sec, i in arrivals]
            for sec, _i, outcome, agent_id, handle, wait in outcomes:
                if agent_id:
                    handled[agent_id] += handle
                self.contacts.append(self._make_contact(d, day, q, sec, outcome, agent_id, handle, wait))
            if q.acd:
                acd_rows.extend(_acd_rows(day, q, outcomes))
        self.acd_by_day[d] = acd_rows
        self.wfm_by_day[d] = _wfm_rows(day, self.agents, shifts, handled)

    def _simulate_acd(self, arrivals, pool, q: Queue):
        rng = self.rng
        out = []
        for sec, i in arrivals:
            patience = sample_patience(rng)
            best, best_key = None, None
            for s in pool:
                start = max(sec, s.free_at, s.login)
                if start < s.logout:
                    key = (start, s.free_at, s.agent_id)   # earliest start, then longest idle
                    if best_key is None or key < best_key:
                        best, best_key = s, key
            if best is None or best_key[0] - sec > patience:
                out.append((sec, i, "ABANDONED", None, 0, None))
                continue
            start = best_key[0]
            handle = max(30, round(lognormal_with_mean(rng, q.aht_seconds, 0.45)))
            best.free_at = start + handle
            out.append((sec, i, "ANSWERED", best.agent_id, handle, float(start - sec)))
        return out

    def _simulate_messages(self, arrivals, pool, q: Queue):
        rng = self.rng
        out = []
        for sec, i in arrivals:
            active = [s.agent_id for s in pool if s.login <= sec < s.logout]
            agent = rng.choice(active) if active else None
            handle = max(60, round(lognormal_with_mean(rng, q.aht_seconds, 0.5)))
            out.append((sec, i, "ANSWERED", agent, handle if agent else 0, None))
        return out

    def _make_contact(self, d, day, q: Queue, sec, outcome, agent_id, handle, wait) -> Contact:
        rng = self.rng
        local = datetime.combine(day, time(0)) + timedelta(seconds=int(sec))
        ts_utc = local.replace(tzinfo=NY).astimezone(UTC)
        ctype = weighted_choice(rng, q.customer_mix)
        reason = weighted_choice(rng, CUSTOMER_REASON_WEIGHTS[ctype])
        if outcome == "ABANDONED":
            dispo = "ABANDONED"
        elif outcome == "SELF":
            dispo = "SUBMITTED" if REASON_CODES[reason][1] else "SELF_RESOLVED"
        else:
            dispo = weighted_choice(rng, ANSWERED_DISPOSITIONS)
        return Contact(day_index=d, queue_id=q.queue_id, channel=q.channel, ts_local=local,
                       ts_utc=ts_utc, customer_type=ctype, reason_code=reason, outcome=outcome,
                       agent_id=agent_id, handle_seconds=handle, disposition=dispo,
                       wait_seconds=wait)

    # ---- pass 2: cases, identifiers, planted issues -------------------------
    def finalize(self) -> "Generator":
        rng = self.rng
        self.contacts.sort(key=lambda c: (c.ts_utc, c.queue_id))
        for n, c in enumerate(self.contacts, start=1):
            c.contact_id = f"CT{n:09d}"
        self._assign_cases()
        self._plant_contact_issues()
        self._plant_acd_issues()
        return self

    def _assign_cases(self) -> None:
        rng = self.rng
        events: list[tuple[datetime, str]] = []   # (contact ts, case) in time order
        head = 0
        seq = 0
        repeats = 0
        for c in self.contacts:
            while head < len(events) and c.ts_utc - events[head][0] > CASE_WINDOW:
                head += 1
            if not REASON_CODES[c.reason_code][1]:
                continue
            if c.reason_code != "CLM-NEW" and head < len(events) and rng.random() < REPEAT_PROBABILITY:
                _, case = events[rng.randrange(head, len(events))]
                repeats += 1
            else:
                seq += 1
                case = f"CASE-2026-{seq:06d}"
            c.linked_case_ref = case
            events.append((c.ts_utc, case))
        self.repeat_contacts = repeats
        self.case_count = seq

    def _plant_contact_issues(self) -> None:
        rng = self.rng
        for c in self.contacts:
            if REASON_CODES[c.reason_code][1] and c.linked_case_ref and rng.random() < CASE_NULL_RATE:
                c.linked_case_ref = None
                c.planted.append("null_linked_case_ref")
        answered = [c for c in self.contacts if c.outcome == "ANSWERED" and c.agent_id]
        picks = rng.sample(answered, 6 + 7)
        for c in picks[:6]:
            c.handle_seconds = -rng.randint(5, 300)
            c.planted.append("negative_handle_seconds")
        for k, c in enumerate(picks[6:]):
            c.agent_id = f"AGT-9{k + 1:03d}"       # not in the roster
            c.planted.append("agent_not_in_roster")

    def _plant_acd_issues(self) -> None:
        rng = self.rng
        candidates = [r for d in sorted(self.acd_by_day) for r in self.acd_by_day[d]
                      if r["calls_offered"] > 0]
        for row in rng.sample(candidates, 4):
            row["service_level_pct"] = round(100 + rng.uniform(0.5, 9.5), 1)
            row["_planted"] = "service_level_above_100"


def _acd_rows(day: date, q: Queue, outcomes) -> list:
    buckets = defaultdict(list)
    for _sec, i, outcome, _agent, _handle, wait in outcomes:
        buckets[i].append((outcome, wait))
    rows = []
    for i in range(N_INTERVALS):
        items = buckets.get(i, [])
        offered = len(items)
        waits = [w for o, w in items if o == "ANSWERED"]
        abandoned = sum(1 for o, _ in items if o == "ABANDONED")
        within = sum(1 for w in waits if w <= SL_SECONDS)
        minutes = 8 * 60 + 30 * i
        rows.append({
            "interval_start_local": f"{day.isoformat()} {minutes // 60:02d}:{minutes % 60:02d}:00",
            "queue_id": q.queue_id,
            "queue_name": q.name,
            "calls_offered": offered,
            "calls_answered": len(waits),
            "calls_abandoned": abandoned,
            "avg_wait_seconds": round(mean(waits)) if waits else 0,
            "max_wait_seconds": round(max(waits)) if waits else 0,
            "service_level_pct": round(100 * within / offered, 1) if offered else "",
        })
    return rows


def _wfm_rows(day: date, agents: dict, shifts: dict, handled: dict) -> list:
    rows = []
    for aid in agents:
        s = shifts.get(aid)
        if s is None:
            continue
        if s.off:
            rows.append({"work_date": day.isoformat(), "agent_id": aid, "team": s.team,
                         "scheduled_minutes": 0, "logged_in_minutes": 0, "adherence_pct": "",
                         "occupancy_pct": "", "shift_type": "OFF"})
            continue
        logged = (s.logout - s.login) // 60
        occ = min(100.0, round(100 * handled.get(aid, 0) / (logged * 60), 1)) if logged else ""
        rows.append({"work_date": day.isoformat(), "agent_id": aid, "team": s.team,
                     "scheduled_minutes": s.scheduled_min, "logged_in_minutes": logged,
                     "adherence_pct": s.adherence, "occupancy_pct": occ, "shift_type": s.shift_type})
    return rows


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def _issue(kind, source, batch, file_name, line, key, detail) -> dict:
    return {"issue_type": kind, "source": source, "batch_id": batch, "file_name": file_name,
            "line_number": line, "record_key": key, "detail": detail,
            "expected_detection_rule": ISSUE_RULES[kind]}


def _write_csv(path: Path, columns, rows, on_row=None) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for line_no, row in enumerate(rows, start=2):   # header is line 1
            w.writerow(row)
            if on_row:
                on_row(line_no, row)


def _contact_record(c: Contact) -> dict:
    return {
        "contact_id": c.contact_id,
        "contact_start_utc": c.ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channel": c.channel,
        "queue_id": c.queue_id,
        "contact_reason_code": c.reason_code,
        "reason_description": REASON_CODES[c.reason_code][0],
        "agent_id": c.agent_id,
        "handle_seconds": c.handle_seconds,
        "linked_case_ref": c.linked_case_ref,
        "disposition": c.disposition,
        "customer_type": c.customer_type,
    }


def write_outputs(gen: Generator, out: Path) -> dict:
    for sub in ("acd", "contacts", "wfm", "roster", "_reference"):
        shutil.rmtree(out / sub, ignore_errors=True)
    (out / "_planted_issues.csv").unlink(missing_ok=True)
    for sub in ("acd", "contacts", "wfm", "roster", "_reference"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    planted: list[dict] = []
    contacts_by_day = defaultdict(list)
    for c in gen.contacts:
        contacts_by_day[c.day_index].append(c)
    rng = gen.rng
    totals = Counter()
    files = []

    for d in range(DAYS):
        day = START_DATE + timedelta(days=d)
        ymd = day.strftime("%Y%m%d")
        batch_no = d + 1
        bid = f"B{batch_no:03d}"

        # ACD (late delivery for one business date)
        acd_batch_no = batch_no + 1 if d == LATE_ACD_DAY_INDEX else batch_no
        acd_name = f"ACD_{ymd}_B{acd_batch_no:03d}.csv"
        if d == LATE_ACD_DAY_INDEX:
            planted.append(_issue("late_acd_file", "acd", f"B{acd_batch_no:03d}", acd_name, "",
                                  day.isoformat(),
                                  f"ACD file for business date {day.isoformat()} delivered in "
                                  f"B{acd_batch_no:03d} instead of {bid}"))

        def on_acd(line_no, row, _name=acd_name, _bid=f"B{acd_batch_no:03d}"):
            if row.get("_planted"):
                planted.append(_issue("service_level_above_100", "acd", _bid, _name, line_no,
                                      f"{row['queue_id']}|{row['interval_start_local']}",
                                      f"service_level_pct={row['service_level_pct']}"))
        _write_csv(out / "acd" / acd_name, ACD_COLUMNS, gen.acd_by_day[d], on_acd)
        files.append(acd_name)
        totals["acd_rows"] += len(gen.acd_by_day[d])

        # Contacts (JSON Lines, with planted duplicates)
        cname = f"CONTACT_{ymd}_B{batch_no:03d}.jsonl"
        line_no = 0
        with (out / "contacts" / cname).open("w", encoding="utf-8") as fh:
            for c in contacts_by_day[d]:
                line = json.dumps(_contact_record(c), ensure_ascii=False)
                line_no += 1
                fh.write(line + "\n")
                totals["contact_lines"] += 1
                for kind in c.planted:
                    planted.append(_issue(kind, "contacts", bid, cname, line_no, c.contact_id,
                                          _planted_detail(kind, c)))
                if rng.random() < DUPLICATE_RATE:
                    line_no += 1
                    fh.write(line + "\n")
                    totals["contact_lines"] += 1
                    totals["duplicate_lines"] += 1
                    planted.append(_issue("duplicate_contact_id", "contacts", bid, cname, line_no,
                                          c.contact_id, "Exact duplicate line written after original"))
        files.append(cname)
        totals["unique_contacts"] += len(contacts_by_day[d])

        # WFM
        wname = f"WFM_{ymd}_B{batch_no:03d}.csv"
        _write_csv(out / "wfm" / wname, WFM_COLUMNS, gen.wfm_by_day[d])
        files.append(wname)
        totals["wfm_rows"] += len(gen.wfm_by_day[d])

        # Roster (delta rows on change dates; initial load in B001)
        roster_rows = sorted(gen.roster_by_day.get(day, []),
                             key=lambda r: (r["agent_id"], r["effective_start"]))
        if roster_rows or d == 0:
            rname = f"ROSTER_{ymd}_B{batch_no:03d}.csv"
            _write_csv(out / "roster" / rname, ROSTER_COLUMNS, roster_rows)
            files.append(rname)
            totals["roster_rows"] += len(roster_rows)

    with (out / "_reference" / "reason_codes.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["reason_code", "reason_description", "requires_case"])
        for code, (desc, req) in REASON_CODES.items():
            w.writerow([code, desc, "true" if req else "false"])

    with (out / "_planted_issues.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PLANTED_COLUMNS)
        w.writeheader()
        for n, issue in enumerate(planted, start=1):
            w.writerow({"issue_id": f"PI{n:05d}", **issue})

    return {"totals": totals, "planted": planted, "files": len(files)}


def _planted_detail(kind: str, c: Contact) -> str:
    if kind == "null_linked_case_ref":
        return f"linked_case_ref null for reason {c.reason_code}"
    if kind == "negative_handle_seconds":
        return f"handle_seconds={c.handle_seconds}"
    if kind == "agent_not_in_roster":
        return f"agent_id={c.agent_id} not in roster"
    return kind


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
def _pattern_stats(gen: Generator) -> dict:
    per_day = Counter(c.day_index for c in gen.contacts)
    days = [START_DATE + timedelta(days=d) for d in range(DAYS)]

    def avg(ds):
        vals = [per_day[d] for d in range(DAYS) if ds(days[d])]
        return mean(vals) if vals else 0.0

    weekday = lambda x: x.weekday() < 5
    weekend = lambda x: x.weekday() >= 5
    base = avg(lambda x: weekday(x) and x < SURGE_START)
    surge = avg(lambda x: weekday(x) and SURGE_START <= x <= POLICY_DEADLINE)
    weekly = {}
    for w in range(WEEKS):
        cs = [c for c in gen.contacts if c.queue_id == "Q_PHONE_CLM" and c.day_index // 7 == w]
        n = len(cs)
        if not n:
            continue
        sl = sum(1 for c in cs if c.outcome == "ANSWERED" and c.wait_seconds <= SL_SECONDS) / n
        ab = sum(1 for c in cs if c.outcome == "ABANDONED") / n
        weekly[w + 1] = (sl * 100, ab * 100)
    return {
        "monday_avg": avg(lambda x: x.weekday() == 0),
        "tue_fri_avg": avg(lambda x: x.weekday() in (1, 2, 3, 4)),
        "weekend_avg": avg(weekend),
        "weekday_base_avg": base,
        "surge_weekday_avg": surge,
        "weekly_clm": weekly,
        "repeat_share": 100 * gen.repeat_contacts / max(1, len(gen.contacts)),
        "cases": gen.case_count,
    }


def print_summary(gen: Generator, res: dict, out: Path) -> None:
    t = res["totals"]
    by_type = Counter(p["issue_type"] for p in res["planted"])
    end = START_DATE + timedelta(days=DAYS - 1)
    st = _pattern_stats(gen)
    print("Synthetic contact center sources generated (all data synthetic)")
    print(f"  Output dir : {out}")
    print(f"  Date range : {START_DATE.isoformat()} to {end.isoformat()} "
          f"({WEEKS} weeks, batches B001-B{DAYS:03d})")
    print(f"  Files      : {res['files']}")
    print()
    print("Row counts")
    print(f"  ACD interval export (acd/)        : {t['acd_rows']:>7,} rows")
    print(f"  Contact records (contacts/)       : {t['contact_lines']:>7,} lines "
          f"({t['unique_contacts']:,} unique contacts + {t['duplicate_lines']:,} duplicate lines)")
    print(f"  Workforce management (wfm/)       : {t['wfm_rows']:>7,} rows")
    print(f"  Agent roster SCD feed (roster/)   : {t['roster_rows']:>7,} rows")
    print()
    print("Planted data quality issues")
    for kind in ISSUE_RULES:
        print(f"  {kind:<26}: {by_type.get(kind, 0):>5,}")
    print(f"  {'TOTAL':<26}: {len(res['planted']):>5,}")
    print(f"  Planted log: {out / '_planted_issues.csv'}")
    print()
    print("Pattern checks")
    print(f"  Monday avg contacts / Tue-Fri avg : {st['monday_avg']:.0f} / {st['tue_fri_avg']:.0f}")
    print(f"  Weekend avg / weekday avg          : {st['weekend_avg']:.0f} / "
          f"{(st['monday_avg'] + st['tue_fri_avg'] * 4) / 5:.0f}")
    print(f"  Surge weekday avg vs pre-surge     : {st['surge_weekday_avg']:.0f} vs "
          f"{st['weekday_base_avg']:.0f} ({st['surge_weekday_avg'] / max(1, st['weekday_base_avg']):.1f}x)")
    print("  Claimant phone by week (SL20 % / abandon %):")
    print("    " + "  ".join(f"W{w}: {sl:.0f}/{ab:.0f}" for w, (sl, ab) in st["weekly_clm"].items()))
    print(f"  Repeat contacts (linked case had contact within 7 days): {st['repeat_share']:.1f}% "
          f"of contacts; {st['cases']:,} cases opened")


def main(argv=None) -> None:
    default_out = Path(__file__).resolve().parents[2] / "data" / "source"
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: %(default)s)")
    p.add_argument("--out", type=Path, default=default_out, help="output directory (default: data/source)")
    args = p.parse_args(argv)

    gen = Generator(args.seed).run().finalize()
    res = write_outputs(gen, args.out)
    print_summary(gen, res, args.out)


if __name__ == "__main__":
    main()

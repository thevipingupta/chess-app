#!/usr/bin/env python3
"""Five9-shaped synthetic source generator (contact-center-medallion-demo).

Column lists and types for the four Five9 sheets are read from the workbook
at run time (the workbook is the source of truth). Nothing is hand-typed.

Writes 14 weeks (2026-03-02 .. 2026-06-07, batches B001..B098):
    data/source/five9/ACD_ALL_<yyyymmdd>_B<nnn>.csv
    data/source/five9/CALLLOG_<yyyymmdd>_B<nnn>.csv
    data/source/five9/CALL_SEGMENT_<yyyymmdd>_B<nnn>.csv
    data/source/five9/VOICEMAIL_LIFECYCLE_<yyyymmdd>_B<nnn>.csv
    data/source/wfm/wfm_daily_<yyyymmdd>_B<nnn>.csv   (ASSUMPTION: not a Five9 export)
    data/source/_planted_issues.csv
    data/source/_reference/header_changes.csv
    docs/source_column_map.csv

Requires openpyxl (to read the workbook). Standard library otherwise.
All identifiers, names, and contact data are synthetic.

Run from the project root:
    python src/generate/generate_sources.py
    python src/generate/generate_sources.py --seed 7 --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_WORKBOOK = PROJECT / "reference" / "Contact_Center.xlsx"
DEFAULT_OUT = PROJECT / "data" / "source"
DEFAULT_COLUMN_MAP = PROJECT / "docs" / "source_column_map.csv"

DEFAULT_SEED = 20260302
START_DATE = date(2026, 3, 2)              # Monday -> B001
WEEKS = 14
DAYS = WEEKS * 7                           # through 2026-06-07
POLICY_DEADLINE = date(2026, 4, 17)        # Friday of week 7
SURGE_DAYS = 10
SURGE_START = POLICY_DEADLINE - timedelta(days=SURGE_DAYS - 1)
SHORTFALL_WEEK = 10                        # 1-based; 2026-05-04 .. 2026-05-10
EVOLUTION_DATE = date(2026, 4, 29)         # week 9: ACD file gains two columns
LATE_ACD_DAY_INDEX = 28                    # 2026-03-30 ACD file lands one batch late
REPEAT_PROBABILITY = 0.15                  # calls that reuse an earlier customer_key
REPEAT_WINDOW = timedelta(days=7)
DUPLICATE_RATE = 0.005                     # duplicate CALLLOG lines
ORPHAN_SEGMENT_RATE = 0.01                 # segments whose CALL ID has no call
VOICEMAIL_RATE = 0.08                      # abandoned calls that leave a voicemail
IVR_SEGMENT_RATE = 0.30
NEG_HANDLE_COUNT = 6
MISSING_AGENT_COUNT = 6
SL_OVER_100_COUNT = 4
LATE_BATCH_NOTE = "ACD file for 2026-03-30 delivered in B030 instead of B029"

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
OPEN_SECONDS = 8 * 3600                    # 08:00 local
N_INTERVALS = 20                           # 08:00-18:00, 30-minute intervals
INTERVAL_SECONDS = 1800
INTERVAL_WEIGHTS = (
    0.55, 0.80, 1.00, 1.10, 1.15, 1.15, 1.10, 0.85, 0.60, 0.60,   # lunch dip 11:30-13:00
    0.80, 1.00, 1.10, 1.10, 1.05, 1.00, 0.90, 0.75, 0.60, 0.45,
)
WEIGHT_TOTAL = sum(INTERVAL_WEIGHTS)
DOW_FACTOR = {0: 1.25, 1: 1.10, 2: 1.05, 3: 1.00, 4: 0.90, 5: 0.35, 6: 0.30}  # Monday peak
DNIS = "15550100001"                       # reserved-style synthetic number

SHEETS = ("API_ACD_ALL", "API_CALLLOG", "API_CALL_SEGMENTREPORTALL", "API_VOICEMAIL_LIFECYCLE_LOG")
FILE_PREFIX = {"API_ACD_ALL": "ACD_ALL", "API_CALLLOG": "CALLLOG",
               "API_CALL_SEGMENTREPORTALL": "CALL_SEGMENT",
               "API_VOICEMAIL_LIFECYCLE_LOG": "VOICEMAIL_LIFECYCLE"}
SILVER_TABLE = {"API_ACD_ALL": "stg_api_acd_all", "API_CALLLOG": "stg_api_calllog",
                "API_CALL_SEGMENTREPORTALL": "stg_api_call_segment",
                "API_VOICEMAIL_LIFECYCLE_LOG": "stg_api_voicemail_lifecycle"}
BRONZE_TABLE = {"API_ACD_ALL": "raw_api_acd_all", "API_CALLLOG": "raw_api_calllog",
                "API_CALL_SEGMENTREPORTALL": "raw_api_call_segment",
                "API_VOICEMAIL_LIFECYCLE_LOG": "raw_api_voicemail_lifecycle"}

# Silver-only renames so Five9 metadata names do not collide with bronze metadata.
SILVER_RENAME = {"FILE_NAME": "five9_file_name", "LOAD_TIMESTAMP": "five9_load_timestamp"}

# CLAUDE.md PII list (masked in silver) and fields that look personal but are not in that list.
PII_CLAUDE = {"ANI", "CUSTOMER NAME", "FIRST_NAME", "LAST_NAME", "EMAIL", "STREET", "ZIP",
              "CITY", "AGENT EMAIL", "AGENT FIRST NAME", "AGENT LAST NAME"}
PII_REVIEW = {"AGENT NAME", "AGENT.FULL_NAME", "AGENT", "LASTAGENT", "DEST AGENT",
              "DEST AGENT EMAIL", "DEST AGENT FIRST NAME", "DEST AGENT LAST NAME",
              "DEST AGENT NAME", "SALESFORCE_ID", "COMPANY", "STATE", "ANI STATE",
              "ANI AREA CODE", "NAME", "NOTES", "CALLING PARTY", "CALLED PARTY",
              "NUMBER1", "NUMBER2", "NUMBER3", "LIST NAME", "AGENT.STATION_ID"}

# --------------------------------------------------------------------------
# Disposition hierarchy (DISPOSITION, GROUP A, GROUP B, GROUP C)
# --------------------------------------------------------------------------
DISP_CLAIM_STATUS = ("Claim status provided", "Resolved", "Claim status", "Information")
DISP_PAYMENT = ("Payment explained", "Resolved", "Payments", "Information")
DISP_DOC = ("Document received", "Resolved", "Documents", "Processed")
DISP_APPEAL = ("Appeal logged", "Escalated", "Appeals", "Logged")
DISP_CALLBACK = ("Callback scheduled", "Follow-up", "Callback", "Scheduled")
DISP_SUPERVISOR = ("Transferred to supervisor", "Escalated", "Supervisor", "Transferred")
DISP_EMPLOYER = ("Employer notice answered", "Resolved", "Employer", "Information")
DISP_GENERAL = ("General info provided", "Resolved", "General", "Information")
DISP_ABANDON = ("Abandoned", "Abandoned", "Abandoned", "Caller ended")


@dataclass(frozen=True)
class Skill:
    name: str
    campaign: str
    team: str
    threshold: int            # SOA threshold in seconds (varies by skill)
    base_daily: int           # calls per weekday before factors
    aht: int                  # average handle time, seconds
    dispositions: tuple       # ((disposition tuple, weight), ...)


SKILLS = (
    Skill("CLAIMANT_PHONE", "CLAIMS_INBOUND", "CLAIMANT", 20, 200, 420,
          ((DISP_CLAIM_STATUS, .30), (DISP_PAYMENT, .20), (DISP_DOC, .15), (DISP_APPEAL, .05),
           (DISP_CALLBACK, .10), (DISP_SUPERVISOR, .05), (DISP_GENERAL, .15))),
    Skill("EMPLOYER_PHONE", "EMPLOYER_INBOUND", "EMPLOYER", 30, 110, 360,
          ((DISP_EMPLOYER, .55), (DISP_DOC, .15), (DISP_CALLBACK, .10), (DISP_SUPERVISOR, .05),
           (DISP_GENERAL, .15))),
    Skill("GENERAL_PHONE", "GENERAL_INBOUND", "GENERAL", 60, 40, 300,
          ((DISP_GENERAL, .60), (DISP_CLAIM_STATUS, .20), (DISP_CALLBACK, .10),
           (DISP_SUPERVISOR, .10))),
)
SKILL_BY_NAME = {s.name: s for s in SKILLS}

# --------------------------------------------------------------------------
# Workbook (source of truth for columns and types)
# --------------------------------------------------------------------------


def load_workbook_schema(path: Path) -> dict:
    """Return {sheet: [(name, type, evolution_json_or_None), ...]} in workbook order."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    schema = {}
    for ws in wb.worksheets:
        cols = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            cols.append((str(row[0]), str(row[1]), row[12] if len(row) > 12 else None))
        schema[ws.title] = cols
    missing = [s for s in SHEETS if s not in schema]
    if missing:
        raise SystemExit(f"Workbook is missing sheets: {missing}")
    return schema


def base_type(t: str) -> str:
    return t.split("(")[0]


def snake(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", " ", name.replace(".", " dot ")).strip().lower()
    return "_".join(s.split())


def default_value(typ: str) -> str:
    b = base_type(typ)
    if b == "NUMBER":
        return "0"
    if b == "TIME":
        return "00:00:00"
    return ""


def put(row: dict, types: dict, name: str, value, duration: bool = False) -> None:
    """Set a column only if this sheet has it. Coerce to the column's type."""
    if name not in types:
        return
    t = base_type(types[name])
    if duration and t == "TIME":
        s = int(round(value))
        row[name] = f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    elif duration:
        row[name] = str(int(round(value)))
    elif t == "NUMBER":
        try:
            row[name] = str(int(str(value)))
        except ValueError:
            row[name] = str(value)
    elif t == "TIME":
        v = str(value)
        row[name] = f"{int(v):02d}:00:00" if v.isdigit() else v
    else:
        row[name] = str(value)


def blank_row(cols: list, types: dict) -> dict:
    return {name: default_value(types[name]) for name, _t, _e in cols}


def time_labels(lt: datetime) -> dict:
    q = lt.replace(minute=(lt.minute // 15) * 15, second=0, microsecond=0)
    h = lt.replace(minute=(lt.minute // 30) * 30, second=0, microsecond=0)
    h_end = h + timedelta(minutes=30)
    labels = {
        "DATE": f"{lt:%Y-%m-%d}",
        "DATE AND HOUR": f"{lt:%Y-%m-%d %H}",
        "DAY OF WEEK": f"{lt:%A}",
        "MONTH": f"{lt:%m}",
        "TIME INTERVAL": f"{h:%H:%M}-{h_end:%H:%M}",
        "YEAR": f"{lt:%Y}",
        "QUARTER HOUR": f"{q:%H:%M}",
        "HALF HOUR": f"{h:%H:%M}",
        "HOUR": f"{lt:%H}",
        "HOUR OF DAY": f"{lt.hour}",
        "DAY OF MONTH": f"{lt:%d}",
        "TIME": f"{lt:%H:%M:%S}",
    }
    prefixed = {f"RECEIVED {k}": v for k, v in labels.items()}
    prefixed["RECEIVED INTERVAL"] = labels["TIME INTERVAL"]
    return {**labels, **prefixed}


def utc_labels(ut: datetime) -> dict:
    ts = f"{ut:%Y-%m-%d %H:%M:%S}"
    ms = f"{ts}.000"
    return {"TIMESTAMP": ts, "TIMESTAMP MILLISECOND": ms, "CONTACT CREATE TIMESTAMP": ts,
            "CONTACTCREATEDATETIME": ts, "CONTACTMODIFIEDDATETIME": ts,
            "CONTACT MODIFIED TIMESTAMP": ts, "LASTDISPOSITIONDATETIME": ts,
            "RECEIVED TIMESTAMP": ts, "RECEIVED TIMESTAMP MILLISECOND": ms,
            "PROCESSED TIMESTAMP": ts, "RETURNED TIMESTAMP": ts}


def surge_multiplier(day: date) -> float:
    if SURGE_START <= day <= POLICY_DEADLINE:
        k = (day - SURGE_START).days
        return 2.0 + k / (SURGE_DAYS - 1)     # 2.0x ramping to 3.0x at the deadline
    return 1.0


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


def sample_patience(rng: random.Random, threshold: int) -> float:
    """Callers give up near the skill's SOA threshold for a share of calls, so abandonment
    rises once queue wait passes the threshold. The rest wait much longer."""
    if rng.random() < 0.15:
        return rng.uniform(0.8 * threshold, 1.2 * threshold)
    return min(1800.0, rng.expovariate(1 / 600))


# --------------------------------------------------------------------------
# Agents, shifts, and the WFM assumption
# --------------------------------------------------------------------------
@dataclass
class Agent:
    agent_id: str
    team: str
    first_day: date
    last_day: date | None
    part_time: bool
    start_hour: int
    first: str
    last: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def email(self) -> str:
        return f"{self.last.lower()}@example.invalid"

    def active(self, day: date) -> bool:
        return self.first_day <= day and (self.last_day is None or day <= self.last_day)

    def shift(self):
        length = 5 if self.part_time else 8
        tag = "PT" if self.part_time else "FT"
        stype = f"{tag}_{self.start_hour:02d}00_{self.start_hour + length:02d}00"
        return stype, self.start_hour, length


def build_agents(rng: random.Random) -> list:
    specs = [(f"AGT-{i:04d}", "CLAIMANT", START_DATE, None) for i in range(1, 10)]
    specs += [(f"AGT-{i:04d}", "EMPLOYER", START_DATE, None) for i in range(10, 15)]
    specs += [(f"AGT-{i:04d}", "GENERAL", START_DATE, None) for i in range(15, 21)]
    specs += [("AGT-0021", "GENERAL", START_DATE, date(2026, 4, 23))]       # leaver
    specs += [("AGT-0022", "GENERAL", date(2026, 5, 18), None)]              # hire
    specs += [("AGT-0023", "CLAIMANT", date(2026, 4, 27), None)]             # hire
    agents = []
    for n, (aid, team, first_day, last_day) in enumerate(specs):
        part_time = rng.random() < 0.20
        start_hour = rng.choice((13, 8)) if part_time else rng.choice((8, 9, 10))
        agents.append(Agent(aid, team, first_day, last_day, part_time, start_hour,
                            "Agent", f"A{n + 1:04d}"))
    return agents


def build_week_patterns(rng: random.Random, n_agents: int) -> list:
    """patterns[agent_index][week0] = set of weekday numbers worked (0 = Monday)."""
    patterns = []
    for _ in range(n_agents):
        weeks = []
        for _w in range(WEEKS):
            if rng.random() < 0.25:
                weeks.append(set(rng.sample(range(5), 3)) | set(rng.sample((5, 6), 2)))
            else:
                weeks.append(set(range(5)))
        patterns.append(weeks)
    return patterns


# --------------------------------------------------------------------------
# Simulation model
# --------------------------------------------------------------------------
@dataclass
class Call:
    day: int
    skill: Skill
    agent: Agent | None
    ts_local: datetime
    ts_utc: datetime
    interval: int
    answered: bool
    wait: float                 # queue wait (answered) or time to abandon (abandoned)
    handle: int
    disp: tuple                 # disposition tuple
    seq: int = 0
    call_id: str = ""
    contact_id: str = ""
    session_id: str = ""
    cust: dict | None = None
    repeat_of: int | None = None
    handle_override: int | None = None
    agent_override: str | None = None
    planted: list = field(default_factory=list)

    @property
    def customer_key(self) -> str | None:
        if not self.cust:
            return None
        return self.cust["sfid"] or self.cust["ani"]


@dataclass
class Shift:
    agent: Agent
    off: bool
    shift_type: str
    scheduled_min: int
    login: int
    logout: int
    adherence: float
    free_at: int = 0


class Generator:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.agents = build_agents(self.rng)
        self.patterns = build_week_patterns(self.rng, len(self.agents))
        self.calls: list[Call] = []
        self.acd_by_day: dict[int, list] = {}
        self.wfm_by_day: dict[int, list] = {}
        self.customers_created = 0
        self.repeat_calls = 0

    def run(self) -> "Generator":
        for d in range(DAYS):
            self._simulate_day(d)
        return self

    def _build_shifts(self, day: date, week0: int) -> dict:
        rng = self.rng
        shortfall = week0 + 1 == SHORTFALL_WEEK and day.weekday() < 5
        shifts = {}
        for idx, ag in enumerate(self.agents):
            if not ag.active(day):
                continue
            off = day.weekday() not in self.patterns[idx][week0]
            if not off and shortfall and ag.team == "CLAIMANT" and rng.random() < 0.40:
                off = True
            stype, start_h, length = ag.shift()
            if off:
                shifts[ag.agent_id] = Shift(ag, True, "OFF", 0, 0, 0, 0.0)
                continue
            late = rng.randint(3, 12) * 60 if rng.random() < 0.15 else 0
            login = start_h * 3600 + late
            logout = (start_h + length) * 3600
            adh = rng.uniform(74, 86) if (shortfall and ag.team == "CLAIMANT") else rng.uniform(88, 97)
            shifts[ag.agent_id] = Shift(ag, False, stype, length * 60, login, logout,
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
        for skill in SKILLS:
            daily = skill.base_daily * dow * surge * rng.lognormvariate(0, 0.07)
            arrivals = []
            for i, w in enumerate(INTERVAL_WEIGHTS):
                for _ in range(poisson(rng, daily * w / WEIGHT_TOTAL)):
                    sec = OPEN_SECONDS + i * INTERVAL_SECONDS + rng.random() * INTERVAL_SECONDS
                    arrivals.append((sec, i))
            arrivals.sort()
            pool = [s for s in shifts.values() if s.agent.team == skill.team and not s.off]
            outcomes = self._simulate_queue(arrivals, pool, skill)
            for sec, interval, answered, agent, wait, handle in outcomes:
                if answered:
                    handled[agent.agent_id] += handle
                local = datetime.combine(day, time(0)) + timedelta(seconds=int(sec))
                ts_utc = local.replace(tzinfo=NY).astimezone(UTC)
                if answered:
                    disp = weighted_choice(rng, skill.dispositions)
                else:
                    disp = DISP_ABANDON
                self.calls.append(Call(d, skill, agent, local, ts_utc, interval, answered,
                                       wait, handle, disp))
            acd_rows.extend(_acd_rows(day, skill, outcomes))
        self.acd_by_day[d] = acd_rows
        self.wfm_by_day[d] = _wfm_rows(day, shifts, self.agents)

    def _simulate_queue(self, arrivals, pool, skill: Skill):
        rng = self.rng
        out = []
        for sec, interval in arrivals:
            patience = sample_patience(rng, skill.threshold)
            best, best_key = None, None
            for s in pool:
                start = max(sec, s.free_at, s.login)
                if start < s.logout:
                    key = (start, s.free_at, s.agent.agent_id)   # earliest start, then longest idle
                    if best_key is None or key < best_key:
                        best, best_key = s, key
            if best is None or best_key[0] - sec > patience:
                out.append((sec, interval, False, None, patience, 0))
                continue
            start = best_key[0]
            handle = max(30, round(lognormal_with_mean(rng, skill.aht, 0.45)))
            best.free_at = start + handle
            out.append((sec, interval, True, best.agent, float(start - sec), handle))
        return out

    # ---- pass 2: customers, identifiers, planted issues -----------------
    def finalize(self) -> "Generator":
        rng = self.rng
        self.calls.sort(key=lambda c: (c.ts_utc, c.skill.name))
        for n, c in enumerate(self.calls, start=1):
            c.seq = n
            c.call_id = f"CL{n:010d}"
            c.contact_id = f"C{n:09d}"
            c.session_id = f"S{n:09d}"
        self._assign_customers()
        answered = [c for c in self.calls if c.answered]
        for c in rng.sample(answered, NEG_HANDLE_COUNT):
            c.handle_override = -rng.randint(5, 300)
            c.planted.append("negative_handle_time")
        remaining = [c for c in answered if not c.planted]
        for k, c in enumerate(rng.sample(remaining, MISSING_AGENT_COUNT), start=1):
            c.agent_override = f"AGT-9{k:03d}"
            c.planted.append("agent_not_in_wfm")
        candidates = [r for d in sorted(self.acd_by_day) for r in self.acd_by_day[d]
                      if r["calls_offered"] > 0]
        for row in rng.sample(candidates, SL_OVER_100_COUNT):
            row["service_level_pct"] = round(100 + rng.uniform(0.5, 9.5), 1)
            row["_planted"] = True
        return self

    def _assign_customers(self) -> None:
        """About 15% of calls reuse the customer of an earlier call within 7 days."""
        rng = self.rng
        window: list[tuple[datetime, int]] = []   # (ts_utc, call seq) in time order
        head = 0
        for c in self.calls:
            while head < len(window) and c.ts_utc - window[head][0] > REPEAT_WINDOW:
                head += 1
            if head < len(window) and rng.random() < REPEAT_PROBABILITY:
                _, src_seq = window[rng.randrange(head, len(window))]
                src = self.calls[src_seq - 1]
                c.cust = src.cust
                c.repeat_of = src.seq
                self.repeat_calls += 1
            else:
                c.cust = self._new_customer()
            window.append((c.ts_utc, c.seq))

    def _new_customer(self) -> dict:
        rng = self.rng
        self.customers_created += 1
        n = self.customers_created
        sfid = f"001{rng.randrange(10**12, 10**13)}" if rng.random() < 0.7 else ""
        return {
            "ani": f"1555{rng.randrange(10**6, 10**7)}",
            "sfid": sfid,
            "n": n,
        }


def _acd_rows(day: date, skill: Skill, outcomes) -> list:
    buckets = defaultdict(list)
    for sec, interval, answered, _agent, wait, handle in outcomes:
        buckets[interval].append((answered, wait, handle))
    rows = []
    for i in range(N_INTERVALS):
        items = buckets.get(i, [])
        offered = len(items)
        ans = [(w, h) for a, w, h in items if a]
        abandoned = [w for a, w, _h in items if not a]
        waits = [w for w, _h in ans]
        within = sum(1 for w in waits if w <= skill.threshold)
        minutes = 8 * 60 + 30 * i
        local = datetime.combine(day, time(minutes // 60, minutes % 60))
        ut = local.replace(tzinfo=NY).astimezone(UTC)
        fcr = 1 if offered and len(ans) >= offered / 2 else 0     # interval-level flag
        rows.append({
            "_skill": skill.name,
            "_interval": i,
            "_local": local,
            "_utc": ut,
            "_threshold": skill.threshold,
            "calls_offered": offered,
            "answered": len(ans),
            "abandoned": len(abandoned),
            "queue_wait_total": round(sum(waits)),
            "handle_total": round(sum(h for _w, h in ans)),
            "avg_wait": round(mean(waits)) if waits else 0,
            "max_wait": round(max(waits)) if waits else 0,
            "avg_abandon": round(mean(abandoned)) if abandoned else 0,
            "service_level_pct": round(100 * within / offered, 1) if offered else "",
            "fcr_flag": fcr,
            "overflow_flag": 1 if waits and max(waits) > 3 * skill.threshold else 0,
        })
    return rows


def _wfm_rows(day: date, shifts: dict, agents: list) -> list:
    rows = []
    for ag in agents:
        s = shifts.get(ag.agent_id)
        if s is None:
            continue
        if s.off:
            rows.append({"work_date": day.isoformat(), "agent_id": ag.agent_id,
                         "scheduled_minutes": 0, "logged_in_minutes": 0, "adherence_pct": ""})
        else:
            logged = (s.logout - s.login) // 60
            rows.append({"work_date": day.isoformat(), "agent_id": ag.agent_id,
                         "scheduled_minutes": s.scheduled_min, "logged_in_minutes": logged,
                         "adherence_pct": s.adherence})
    return rows


# --------------------------------------------------------------------------
# Row builders (one dict per output row, keyed by Five9 column name)
# --------------------------------------------------------------------------
def build_acd_row(r: dict, types: dict, cols: list) -> dict:
    row = blank_row(cols, types)
    labels = {**time_labels(r["_local"]), **utc_labels(r["_utc"])}
    for k, v in labels.items():
        put(row, types, k, v)
    skill = SKILL_BY_NAME[r["_skill"]]
    vals = {
        "CAMPAIGN": skill.campaign, "CAMPAIGN TYPE": "Inbound", "SKILL": skill.name,
        "CALL TYPE": "Inbound", "MEDIA TYPE": "Voice", "SOA MIN/THRESHOLD (SEC)": r["_threshold"],
        "CALLS": r["calls_offered"], "ABANDONED": r["abandoned"],
        "SERVICE LEVEL": r["service_level_pct"],
        "QUEUE WAIT TIME": r["queue_wait_total"], "TOTAL QUEUE TIME": r["queue_wait_total"] + r["abandoned"] * r["avg_abandon"],
        "HANDLE TIME": r["handle_total"], "TALK TIME": round(r["handle_total"] * 0.9),
        "TALK TIME LESS HOLD AND PARK": round(r["handle_total"] * 0.9),
        "HOLD TIME": round(r["handle_total"] * 0.05), "AFTER CALL WORK TIME": 30 * r["answered"],
        "RING TIME": 5 * r["answered"], "SPEED OF ANSWER": r["avg_wait"],
        "TIME TO ABANDON": r["avg_abandon"], "FIRST CALL RESOLUTION": r["fcr_flag"],
        "QUEUE OVERFLOW": r["overflow_flag"], "CONSULTS": 0, "CONSULTS ANSWERED": 0,
        "CONFERENCES": 0, "HOLDS": 0, "TRANSFERS": 0, "IVR TIME": 0, "VOICE IVR": 0,
        "VISUAL IVR": 0, "VOICEMAILS": 0, "VOICEMAILS HANDLED": 0,
    }
    for k, v in vals.items():
        if k in ("HANDLE TIME", "TALK TIME", "TALK TIME LESS HOLD AND PARK", "HOLD TIME",
                 "AFTER CALL WORK TIME", "RING TIME", "SPEED OF ANSWER", "TIME TO ABANDON",
                 "QUEUE WAIT TIME", "TOTAL QUEUE TIME", "IVR TIME"):
            put(row, types, k, v, duration=True)
        else:
            put(row, types, k, v)
    return row


def _identity_fields(cu: dict, agent: Agent | None) -> dict:
    n = cu["n"]
    f = {
        "ANI": cu["ani"], "ANI COUNTRY": "US", "ANI STATE": "ZZ", "ANI AREA CODE": "555",
        "ANI COUNTRY CODE": "1", "CUSTOMER NAME": f"Customer C{n:05d}", "FIRST_NAME": "Customer",
        "LAST_NAME": f"C{n:05d}", "EMAIL": f"c{n:05d}@example.invalid",
        "STREET": f"{n % 999 + 1} Synthetic Way", "CITY": "Synthville", "ZIP": "00000",
        "STATE": "ZZ", "COMPANY": f"Synthetic Org {n % 50:02d}", "SALESFORCE_ID": cu["sfid"],
    }
    if agent is not None:
        f.update({"AGENT": agent.agent_id, "AGENT NAME": agent.name, "AGENT FIRST NAME": agent.first,
                  "AGENT LAST NAME": agent.last, "AGENT EMAIL": agent.email,
                  "AGENT.FULL_NAME": agent.name, "AGENT.STATION_ID": f"STN{agent.agent_id[-4:]}"})
    return f


def build_calllog_row(c: Call, types: dict, cols: list) -> dict:
    row = blank_row(cols, types)
    labels = {**time_labels(c.ts_local), **utc_labels(c.ts_utc)}
    for k, v in labels.items():
        put(row, types, k, v)
    agent_id = c.agent_override or (c.agent.agent_id if c.agent else "")
    agent = c.agent
    for k, v in _identity_fields(c.cust, agent).items():
        put(row, types, k, v)
    d = c.disp
    handle = c.handle_override if c.handle_override is not None else (c.handle if c.answered else 0)
    vals = {
        "CALL ID": c.call_id, "CONTACT ID": c.contact_id, "SESSION ID": c.session_id,
        "PARENT SESSION ID": "", "RECORD COUNTER": c.seq, "CALL TYPE": "Inbound",
        "CAMPAIGN": c.skill.campaign, "CAMPAIGN TYPE": "Inbound", "MEDIA TYPE": "Voice",
        "SKILL": c.skill.name, "SOA MIN/THRESHOLD (SEC)": c.skill.threshold, "DIAL RESULT": "Connected",
        "DNIS": DNIS, "DNIS COUNTRY": "US", "DNIS STATE": "ZZ", "DNIS AREA CODE": "555",
        "DNIS COUNTRY CODE": "1", "AGENT ID": agent_id, "AGENT.ID": agent_id,
        "AGENT GROUP": c.skill.team, "DISPOSITION": d[0], "DISPOSITION GROUP A": d[1],
        "DISPOSITION GROUP B": d[2], "DISPOSITION GROUP C": d[3],
        "DISPOSITION PATH": " > ".join((d[1], d[2], d[3], d[0])),
        "LASTDISPOSITION": d[0], "LASTCAMPAIGN": c.skill.campaign,
        "LASTAGENT": agent_id, "CALLS": 1, "CONTACTED": 1 if c.answered else 0,
        "ABANDONED": 0 if c.answered else 1,
        "SERVICE LEVEL": 1 if c.answered and c.wait <= c.skill.threshold else 0,
        "FIRST CALL RESOLUTION": 1 if d[1] == "Resolved" else 0, "QUEUE OVERFLOW": 0,
        "VOICE IVR": 1, "VISUAL IVR": 0, "CONSULTS": 0, "CONSULTS ANSWERED": 0,
        "LANGUAGE SELECT": "EN", "COST": 0, "RATE": 0, "CALLS TIMED OUT IN IVR": 0,
        "CALLS COMPLETED IN IVR": 1, "LIVE CONNECT": 1, "NO PARTY CONTACT": 0,
        "CALL CENTER EVENTS.QCB": 0, "CALL CENTER EVENTS.TRAINING": 0,
        "CALL CENTER EVENTS.UNFORESEEN CLOSURE": 0, "RECORDINGS": "",
        "NOTES": "", "WORKSHEET ID": "", "LIST NAME": "", "CALL SURVEY RESULT": "",
    }
    if c.answered:
        vals["LASTDISPOSITIONDATETIME"] = labels["TIMESTAMP"]
    for k, v in vals.items():
        put(row, types, k, v)
    durations = {
        "HANDLE TIME": handle, "BILL TIME (ROUNDED)": max(0, handle),
        "TALK TIME": round(max(0, handle) * 0.9), "TALK TIME LESS HOLD AND PARK": round(max(0, handle) * 0.9),
        "HOLD TIME": round(max(0, handle) * 0.05), "AFTER CALL WORK TIME": 30 if c.answered else 0,
        "QUEUE WAIT TIME": c.wait if c.answered else 0, "TOTAL QUEUE TIME": c.wait if c.answered else 0,
        "SPEED OF ANSWER": c.wait if c.answered else 0, "RING TIME": 5 if c.answered else 0,
        "TIME TO ABANDON": 0 if c.answered else c.wait, "IVR TIME": 12, "DIAL TIME": 2,
        "CONSULT TIME": 0, "CONFERENCE TIME": 0, "PARK TIME": 0, "VIDEO TIME": 0,
        "PREVIEW TIME": 0, "MANUAL TIME": 0, "VOICEMAILS HANDLE TIME": 0,
    }
    for k, v in durations.items():
        put(row, types, k, v, duration=True)
    return row


def build_segment_rows(c: Call, types: dict, cols: list, rng: random.Random, seg_seq: list) -> list:
    """One QUEUE segment, an AGENT segment if answered, and an IVR segment for some calls."""
    out = []
    kinds = ["QUEUE"] + (["AGENT"] if c.answered else []) + (["IVR"] if rng.random() < IVR_SEGMENT_RATE else [])
    for n, kind in enumerate(kinds, start=1):
        seg_seq[0] += 1
        row = blank_row(cols, types)
        offset = 0 if kind == "QUEUE" else (c.wait if kind == "AGENT" else 0)
        lt = c.ts_local + timedelta(seconds=offset)
        ut = c.ts_utc + timedelta(seconds=offset)
        for k, v in {**time_labels(lt), **utc_labels(ut)}.items():
            put(row, types, k, v)
        call_id = c.call_id
        if rng.random() < ORPHAN_SEGMENT_RATE:
            call_id = f"CLX{seg_seq[0]:010d}"           # no matching call
            c.planted.append(("orphan_segment_call_id", call_id, seg_seq[0]))
        agent = c.agent if kind == "AGENT" else None
        vals = {
            "CALL ID": call_id, "CALL SEGMENT ID": f"{c.call_id}-{n}", "CONTACT ID": c.contact_id,
            "SESSION ID": c.session_id, "SEGMENT TYPE": kind,
            "RESULT": ("ANSWERED" if c.answered else "ABANDONED") if kind == "QUEUE" else
                      ("COMPLETED" if kind == "AGENT" else "TRANSFERRED"),
            "CALLING PARTY": c.cust["ani"], "CALLED PARTY": DNIS, "DNIS": DNIS,
            "SKILL": c.skill.name, "CAMPAIGN": c.skill.campaign, "CAMPAIGN TYPE": "Inbound",
            "CALL TYPE": "Inbound", "MEDIA TYPE": "Voice", "SOA MIN/THRESHOLD (SEC)": c.skill.threshold,
            "ANI": c.cust["ani"], "ANI COUNTRY": "US", "ANI STATE": "ZZ", "ANI AREA CODE": "555",
            "ANI COUNTRY CODE": "1", "DNIS COUNTRY": "US", "DNIS STATE": "ZZ", "DNIS AREA CODE": "555",
            "DNIS COUNTRY CODE": "1", "CUSTOMER NAME": f"Customer C{c.cust['n']:05d}",
            "DISPOSITION": c.disp[0], "DISPOSITION GROUP A": c.disp[1],
            "DISPOSITION GROUP B": c.disp[2], "DISPOSITION GROUP C": c.disp[3],
            "DISPOSITION PATH": " > ".join((c.disp[1], c.disp[2], c.disp[3], c.disp[0])),
            "SERVICE LEVEL": 1 if c.answered and c.wait <= c.skill.threshold else 0,
            "CALLS": 1 if kind == "QUEUE" else 0, "CONTACTED": 1 if c.answered else 0,
            "ABANDONED": 0 if c.answered else 1, "FIRST CALL RESOLUTION": 1 if c.disp[1] == "Resolved" else 0,
            "QUEUE OVERFLOW": 0, "VOICE IVR": 1 if kind == "IVR" else 0,
        }
        if agent is not None:
            vals.update({"AGENT": agent.agent_id, "AGENT NAME": agent.name,
                         "AGENT FIRST NAME": agent.first, "AGENT LAST NAME": agent.last,
                         "AGENT EMAIL": agent.email, "AGENT GROUP": agent.team})
        for k, v in vals.items():
            put(row, types, k, v)
        durations = {"SEGMENT TIME": c.handle if kind == "AGENT" else (c.wait if kind == "QUEUE" else 12),
                     "HANDLE TIME": c.handle if kind == "AGENT" else 0,
                     "TALK TIME": round(c.handle * 0.9) if kind == "AGENT" else 0,
                     "QUEUE WAIT TIME": c.wait if kind == "QUEUE" else 0,
                     "TOTAL QUEUE TIME": c.wait if kind == "QUEUE" else 0,
                     "IVR TIME": 12 if kind == "IVR" else 0,
                     "TIME TO ABANDON": c.wait if (kind == "QUEUE" and not c.answered) else 0}
        for k, v in durations.items():
            put(row, types, k, v, duration=True)
        out.append(row)
    return out


def build_voicemail_row(c: Call, types: dict, cols: list, rng: random.Random, vm_seq: list) -> dict:
    vm_seq[0] += 1
    row = blank_row(cols, types)
    lt = c.ts_local + timedelta(seconds=int(c.wait))
    ut = c.ts_utc + timedelta(seconds=int(c.wait))
    for k, v in {**time_labels(lt), **utc_labels(ut)}.items():
        put(row, types, k, v)
    returned = rng.random() < 0.40
    vals = {
        "VOICEMAIL ID": f"VM{vm_seq[0]:09d}", "CALL ID": c.call_id, "CONTACT ID": c.contact_id,
        "COUNTER": vm_seq[0], "RECEIVED TIMESTAMP": utc_labels(ut)["TIMESTAMP"],
        "VOICEMAIL STATUS": "RETURNED" if returned else "PROCESSED", "VOICEMAIL TYPE": "Skill VM",
        "PROCESSED": 1, "LISTENED": 1, "ACCEPTED": 1, "QUEUED": 1, "DELETED": 0,
        "SKILL": c.skill.name, "CAMPAIGN": c.skill.campaign, "CAMPAIGN TYPE": "Inbound",
        "CALL TYPE": "Inbound", "MEDIA TYPE": "Voice", "SOA MIN/THRESHOLD (SEC)": c.skill.threshold,
        "ANI": c.cust["ani"], "CUSTOMER NAME": f"Customer C{c.cust['n']:05d}",
        "FIRST_NAME": "Customer", "LAST_NAME": f"C{c.cust['n']:05d}", "EMAIL": f"c{c.cust['n']:05d}@example.invalid",
        "STREET": f"{c.cust['n'] % 999 + 1} Synthetic Way", "CITY": "Synthville", "ZIP": "00000",
        "STATE": "ZZ", "COMPANY": f"Synthetic Org {c.cust['n'] % 50:02d}", "SALESFORCE_ID": c.cust["sfid"],
        "NAME": f"Voicemail {vm_seq[0]}", "DISPOSITION": c.disp[0], "DISPOSITION GROUP A": c.disp[1],
        "DISPOSITION GROUP B": c.disp[2], "DISPOSITION GROUP C": c.disp[3],
        "DISPOSITION PATH": " > ".join((c.disp[1], c.disp[2], c.disp[3], c.disp[0])),
        "FIRST CALL RESOLUTION": 0, "QUEUE OVERFLOW": 0, "SERVICE LEVEL": 0,
        "VOICEMAILS": 1, "VOICEMAILS HANDLED": 1, "VOICEMAILS RETURNED CALL": 1 if returned else 0,
        "PROCESSING AGENT": "", "RETURNED SKILL": c.skill.name if returned else "",
        "RETURNED CALL ID": f"CLR{vm_seq[0]:010d}" if returned else "",
        "RETURNED TIMESTAMP": utc_labels(ut + timedelta(minutes=20))["TIMESTAMP"] if returned else "",
    }
    if returned:
        vals["RETURNING AGENT"] = c.agent.agent_id if c.agent else ""
    for k, v in vals.items():
        put(row, types, k, v)
    for k, v in {"TIME TO PROCESSED": 120, "TIME TO RETURN": 1200 if returned else 0,
                 "HANDLE TIME": 75, "TIME TO ABANDON": c.wait, "QUEUE WAIT TIME": c.wait,
                 "TOTAL QUEUE TIME": c.wait, "SPEED OF ANSWER": c.wait}.items():
        put(row, types, k, v, duration=True)
    return row


# --------------------------------------------------------------------------
# Column map
# --------------------------------------------------------------------------
NOTE_LABELS = {"DATE", "TIME", "TIME INTERVAL", "HOUR", "HALF HOUR", "QUARTER HOUR", "DAY OF WEEK",
               "MONTH", "YEAR", "DAY OF MONTH", "HOUR OF DAY", "DATE AND HOUR"}


def build_column_rows(schema: dict) -> list:
    rows = []
    for sheet in SHEETS:
        seen = Counter()
        for name, typ, evo in schema[sheet]:
            seen[name] += 1
            notes = []
            if seen[name] > 1:
                notes.append("duplicate column name in workbook")
            if name in SILVER_RENAME:
                notes.append(f"Five9 metadata; silver name {SILVER_RENAME[name]} avoids clash with bronze metadata")
            if name == "AGENT ID":
                notes.append("agent key used for WFM join")
            if name == "AGENT.ID":
                notes.append("second agent identifier; near-duplicate of AGENT ID")
            if name == "QUEUE CALLBACK REGISTERED_tmp":
                notes.append("near-duplicate of QUEUE CALLBACK REGISTERED; not an exact duplicate")
            if "TIMESTAMP" in name or "DATETIME" in name:
                notes.append("UTC (assumed)")
            if name in NOTE_LABELS or name.startswith("RECEIVED "):
                notes.append("local report label; account time zone assumed America/New_York (confirm)")
            if name == "CALL ID":
                notes.append("join key to CALL_SEGMENT CALL ID")
            if name == "CONTACT ID":
                notes.append("contact key; joins voicemail and segments")
            if sheet == "API_ACD_ALL" and name in ("FIRST CALL RESOLUTION", "QUEUE OVERFLOW"):
                notes.append(f"added in week 9 (ACD files from {EVOLUTION_DATE.isoformat()}); absent from earlier ACD files")
            if evo:
                try:
                    e = json.loads(evo)
                    notes.append(f"Five9 schema evolution: {e['evolutionType']} at {e['triggeringTime'][:10]} via {e['fileName']}")
                except (ValueError, KeyError, TypeError):
                    notes.append("schema evolution record present")
            pii = "Y" if name in PII_CLAUDE else ("REVIEW" if name in PII_REVIEW else "N")
            if pii == "REVIEW":
                notes.append("personal-looking; not in CLAUDE.md PII list, decision needed")
            rows.append({
                "source_table": sheet,
                "source_column": name,
                "occurrence": seen[name],
                "snake_case_name": SILVER_RENAME.get(name, snake(name)),
                "target_silver_table": SILVER_TABLE[sheet],
                "silver_data_type": typ,
                "is_pii": pii,
                "notes": "; ".join(notes),
            })
    return rows


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
ISSUE_RULES = {
    "duplicate_contact_id": ("Bronze: GROUP BY CONTACT ID HAVING COUNT(*) > 1 in CALLLOG files. "
                             "Silver: keep one row per CONTACT ID (dedupe, then _record_hash)."),
    "negative_handle_time": ("Silver DQ: HANDLE TIME < 0 in CALLLOG. Route to quarantine with a reason code."),
    "agent_not_in_wfm": ("Silver DQ: AGENT ID in CALLLOG has no row in the WFM extract for that call date. "
                         "Route to quarantine or an unknown-agent member."),
    "service_level_above_100": ("Silver DQ: SERVICE LEVEL > 100 or < 0 in ACD. Route to quarantine."),
    "orphan_segment_call_id": ("Silver DQ: CALL ID in CALL_SEGMENT with no matching CALLLOG CALL ID. "
                               "Route to quarantine; report orphan rate."),
    "late_acd_file": ("Batch completeness: each business date's ACD file should land in its own batch. "
                      "Flag B029 (no ACD file) and B030 (ACD file for 2026-03-30 in addition to 2026-03-31)."),
    "duplicate_column_name": ("Bronze load: header with a repeated column name must be rejected or "
                              "renamed. Not planted (no duplicate names exist in the workbook)."),
}

PLANTED_COLUMNS = ["issue_id", "issue_type", "source", "batch_id", "file_name", "line_number",
                   "column", "record_key", "detail", "expected_detection_rule"]
DUPLICATE_COLUMN = "CONTACT ID"


def _issue(kind, source, batch, file_name, line, column, key, detail) -> dict:
    return {"issue_type": kind, "source": source, "batch_id": batch, "file_name": file_name,
            "line_number": line, "column": column, "record_key": key, "detail": detail,
            "expected_detection_rule": ISSUE_RULES[kind]}


def _write_csv(path: Path, header: list, rows, file_meta: dict, on_row=None, preamble=None) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        if preamble:
            for line in preamble:
                fh.write(line + "\n")
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        line_no = 1 + (len(preamble) if preamble else 0)
        for row in rows:
            line_no += 1
            for k, v in file_meta.items():
                if k in header:
                    row[k] = v
            w.writerow(row)
            if on_row:
                on_row(line_no, row)


def write_outputs(gen: Generator, out: Path, schema: dict, rng_seed: int) -> dict:
    five9 = out / "five9"
    wfm_dir = out / "wfm"
    ref_dir = out / "_reference"
    for d_ in (five9, wfm_dir, ref_dir):
        shutil.rmtree(d_, ignore_errors=True)
        d_.mkdir(parents=True, exist_ok=True)
    (out / "_planted_issues.csv").unlink(missing_ok=True)

    types = {s: {n: t for n, t, _e in schema[s]} for s in SHEETS}
    cols = {s: schema[s] for s in SHEETS}
    acd_full = [n for n, _t, _e in cols["API_ACD_ALL"]]
    acd_evo = {"FIRST CALL RESOLUTION", "QUEUE OVERFLOW"}
    seg_rng = random.Random(rng_seed + 1)     # separate stream: segments and voicemail

    planted: list[dict] = []
    totals = Counter()
    header_changes = []
    calls_by_day = defaultdict(list)
    for c in gen.calls:
        calls_by_day[c.day].append(c)
    dup_rng = random.Random(rng_seed + 2)
    seg_seq, vm_seq = [0], [0]

    for d in range(DAYS):
        day = START_DATE + timedelta(days=d)
        ymd = day.strftime("%Y%m%d")
        batch_no = d + 1
        bid = f"B{batch_no:03d}"
        load_ts = f"{day:%Y-%m-%d} 06:00:00"

        # ---- ACD --------------------------------------------------------------
        acd_batch = batch_no + 1 if d == LATE_ACD_DAY_INDEX else batch_no
        acd_bid = f"B{acd_batch:03d}"
        acd_name = f"ACD_ALL_{ymd}_{acd_bid}.csv"
        if d == LATE_ACD_DAY_INDEX:
            planted.append(_issue("late_acd_file", "acd", acd_bid, acd_name, "", "DATE",
                                  day.isoformat(), LATE_BATCH_NOTE))
        acd_header = acd_full if day >= EVOLUTION_DATE else [n for n in acd_full if n not in acd_evo]
        if day == EVOLUTION_DATE:
            added = [n for n in acd_full if n in acd_evo]
            header_changes.append({"batch_id": bid, "file_name": acd_name,
                                   "change": "ADD_COLUMNS", "columns": "; ".join(added)})
        acd_rows = []
        for r in gen.acd_by_day[d]:
            built = build_acd_row(r, types["API_ACD_ALL"], cols["API_ACD_ALL"])
            built["_planted"] = r.get("_planted")
            acd_rows.append(built)
        meta = {"FILE_NAME": acd_name, "LOAD_TIMESTAMP": load_ts}

        def on_acd(line_no, row, _name=acd_name, _bid=acd_bid):
            if row.get("_planted"):
                planted.append(_issue("service_level_above_100", "acd", _bid, _name, line_no,
                                      "SERVICE LEVEL", f"{row['SKILL']}|{row['DATE']} {row['TIME INTERVAL']}",
                                      f"SERVICE LEVEL={row['SERVICE LEVEL']}"))
        _write_csv(five9 / acd_name, acd_header, acd_rows, meta, on_acd)
        totals["acd_rows"] += len(acd_rows)

        # ---- CALLLOG (+ planted duplicates) and CALL_SEGMENT, VOICEMAIL -------
        calllog_name = f"CALLLOG_{ymd}_{bid}.csv"
        seg_name = f"CALL_SEGMENT_{ymd}_{bid}.csv"
        vm_name = f"VOICEMAIL_LIFECYCLE_{ymd}_{bid}.csv"
        calls = calls_by_day[d]

        cl_rows, cl_meta = [], []
        for c in calls:
            r = build_calllog_row(c, types["API_CALLLOG"], cols["API_CALLLOG"])
            cl_rows.append(r)
            cl_meta.append(c)
            if dup_rng.random() < DUPLICATE_RATE:
                cl_rows.append(dict(r))
                cl_meta.append(None)
        cl_meta_base = {"FILE_NAME": calllog_name, "LOAD_TIMESTAMP": load_ts}
        with (five9 / calllog_name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[n for n, _t, _e in cols["API_CALLLOG"]], extrasaction="ignore")
            w.writeheader()
            line_no = 1
            for row, c in zip(cl_rows, cl_meta):
                line_no += 1
                row.update(cl_meta_base)
                w.writerow(row)
                if c is None:
                    totals["duplicate_calllog_rows"] += 1
                    planted.append(_issue("duplicate_contact_id", "calllog", bid, calllog_name, line_no,
                                          DUPLICATE_COLUMN, row["CONTACT ID"],
                                          "Exact duplicate row written after original"))
                    continue
                if c.handle_override is not None:
                    planted.append(_issue("negative_handle_time", "calllog", bid, calllog_name, line_no,
                                          "HANDLE TIME", c.contact_id, f"HANDLE TIME={c.handle_override}"))
                if c.agent_override:
                    planted.append(_issue("agent_not_in_wfm", "calllog", bid, calllog_name, line_no,
                                          "AGENT ID", c.contact_id, f"AGENT ID={c.agent_override}"))
        totals["calllog_rows"] += len(cl_rows)
        totals["unique_calls"] += len(calls)

        seg_cols = [n for n, _t, _e in cols["API_CALL_SEGMENTREPORTALL"]]
        vm_cols = [n for n, _t, _e in cols["API_VOICEMAIL_LIFECYCLE_LOG"]]
        seg_meta = {"FILE_NAME": seg_name, "LOAD_TIMESTAMP": load_ts}
        vm_meta = {"FILE_NAME": vm_name, "LOAD_TIMESTAMP": load_ts}
        seg_count, vm_count = 0, 0
        with (five9 / seg_name).open("w", newline="", encoding="utf-8") as sfh, \
                (five9 / vm_name).open("w", newline="", encoding="utf-8") as vfh:
            sw = csv.DictWriter(sfh, fieldnames=seg_cols, extrasaction="ignore")
            vw = csv.DictWriter(vfh, fieldnames=vm_cols, extrasaction="ignore")
            sw.writeheader()
            vw.writeheader()
            seg_line = 1
            vm_line = 1
            for c in calls:
                c.planted = []
                for srow in build_segment_rows(c, types["API_CALL_SEGMENTREPORTALL"],
                                               cols["API_CALL_SEGMENTREPORTALL"], seg_rng, seg_seq):
                    seg_line += 1
                    srow.update(seg_meta)
                    sw.writerow(srow)
                    seg_count += 1
                for item in c.planted:
                    kind, orphan_id, _n = item
                    planted.append(_issue("orphan_segment_call_id", "call_segment", bid, seg_name,
                                          seg_line, "CALL ID", orphan_id,
                                          f"CALL ID {orphan_id} has no CALLLOG match"))
                if not c.answered and seg_rng.random() < VOICEMAIL_RATE:
                    vrow = build_voicemail_row(c, types["API_VOICEMAIL_LIFECYCLE_LOG"],
                                               cols["API_VOICEMAIL_LIFECYCLE_LOG"], seg_rng, vm_seq)
                    vrow.update(vm_meta)
                    vm_line += 1
                    vw.writerow(vrow)
                    vm_count += 1
        totals["segment_rows"] += seg_count
        totals["voicemail_rows"] += vm_count
        totals["orphan_segments"] += sum(1 for p in planted if p["issue_type"] == "orphan_segment_call_id"
                                         and p["batch_id"] == bid)

        # ---- WFM (assumption) ----------------------------------------------
        wfm_name = f"wfm_daily_{ymd}_{bid}.csv"
        preamble = [
            "# ASSUMPTION: this WFM extract is not a Five9 export. Five9 has no workforce data.",
            "# Synthetic daily agent schedule and adherence, for the demo only.",
        ]
        _write_csv(wfm_dir / wfm_name, ["work_date", "agent_id", "scheduled_minutes",
                                        "logged_in_minutes", "adherence_pct"],
                   gen.wfm_by_day[d], {}, preamble=preamble)
        totals["wfm_rows"] += len(gen.wfm_by_day[d])

    with (ref_dir / "header_changes.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["batch_id", "file_name", "change", "columns"])
        w.writeheader()
        w.writerows(header_changes)

    with (out / "_planted_issues.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PLANTED_COLUMNS)
        w.writeheader()
        for n, issue in enumerate(planted, start=1):
            w.writerow({"issue_id": f"PI{n:05d}", **issue})

    return {"totals": totals, "planted": planted, "header_changes": header_changes}


def write_column_map(schema: dict, path: Path) -> list:
    rows = build_column_rows(schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_table", "source_column", "occurrence",
                                           "snake_case_name", "target_silver_table",
                                           "silver_data_type", "is_pii", "notes"])
        w.writeheader()
        w.writerows(rows)
    return rows


# --------------------------------------------------------------------------
# Checks and summary
# --------------------------------------------------------------------------
def verify_against_workbook(out: Path, schema: dict) -> dict:
    """Every header in every generated file must be a workbook column for that sheet."""
    sheet_cols = {s: {n for n, _t, _e in schema[s]} for s in SHEETS}
    file_sheet = {"ACD_ALL": "API_ACD_ALL", "CALLLOG": "API_CALLLOG",
                  "CALL_SEGMENT": "API_CALL_SEGMENTREPORTALL",
                  "VOICEMAIL_LIFECYCLE": "API_VOICEMAIL_LIFECYCLE_LOG"}
    bad, checked = [], 0
    for path in sorted((out / "five9").glob("*.csv")):
        prefix = next(p for p in file_sheet if path.name.startswith(p + "_"))
        sheet = file_sheet[prefix]
        with path.open(encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        checked += 1
        extra = [h for h in header if h not in sheet_cols[sheet]]
        if extra:
            bad.append((path.name, extra))
    return {"files_checked": checked, "bad": bad}


def _stats(gen: Generator) -> dict:
    per_day = Counter(c.day for c in gen.calls)
    days = [START_DATE + timedelta(days=d) for d in range(DAYS)]

    def avg(pred):
        vals = [per_day[d] for d in range(DAYS) if pred(days[d])]
        return mean(vals) if vals else 0.0

    weekday = lambda x: x.weekday() < 5
    base = avg(lambda x: weekday(x) and x < SURGE_START)
    surge = avg(lambda x: weekday(x) and SURGE_START <= x <= POLICY_DEADLINE)
    weekly = {}
    for w in range(WEEKS):
        cs = [c for c in gen.calls if c.skill.name == "CLAIMANT_PHONE" and c.day // 7 == w]
        if not cs:
            continue
        sl = sum(1 for c in cs if c.answered and c.wait <= c.skill.threshold) / len(cs) * 100
        ab = sum(1 for c in cs if not c.answered) / len(cs) * 100
        weekly[w + 1] = (sl, ab)
    return {
        "monday": avg(lambda x: x.weekday() == 0),
        "tue_fri": avg(lambda x: x.weekday() in (1, 2, 3, 4)),
        "weekend": avg(lambda x: x.weekday() >= 5),
        "base": base, "surge": surge, "weekly": weekly,
        "repeat_share": 100 * gen.repeat_calls / max(1, len(gen.calls)),
    }


def print_summary(gen: Generator, res: dict, check: dict, out: Path, column_rows: list) -> None:
    t = res["totals"]
    by_type = Counter(p["issue_type"] for p in res["planted"])
    end = START_DATE + timedelta(days=DAYS - 1)
    st = _stats(gen)
    print("Five9-shaped synthetic sources generated (all data synthetic)")
    print(f"  Output dir : {out}")
    print(f"  Date range : {START_DATE.isoformat()} to {end.isoformat()} "
          f"({WEEKS} weeks, batches B001-B{DAYS:03d})")
    print()
    print("Row counts")
    print(f"  API_ACD_ALL (five9/ACD_ALL_*)                 : {t['acd_rows']:>7,} rows")
    print(f"  API_CALLLOG (five9/CALLLOG_*)                 : {t['calllog_rows']:>7,} rows "
          f"({t['unique_calls']:,} calls + {t['duplicate_calllog_rows']:,} duplicate rows)")
    print(f"  API_CALL_SEGMENTREPORTALL (five9/CALL_SEGMENT): {t['segment_rows']:>7,} rows")
    print(f"  API_VOICEMAIL_LIFECYCLE_LOG (five9/VOICEMAIL) : {t['voicemail_rows']:>7,} rows")
    print(f"  WFM daily (wfm/, ASSUMPTION)                  : {t['wfm_rows']:>7,} rows")
    print()
    print("Planted data quality issues")
    for kind in ISSUE_RULES:
        n = by_type.get(kind, 0)
        if kind == "duplicate_column_name":
            n = 0
        print(f"  {kind:<26}: {n:>5,}")
    print(f"  {'TOTAL':<26}: {len(res['planted']):>5,}")
    print(f"  Planted log: {out / '_planted_issues.csv'}")
    print()
    print("Schema evolution")
    for hc in res["header_changes"]:
        print(f"  {hc['batch_id']} {hc['file_name']}: {hc['change']} -> {hc['columns']}")
    print()
    print("Column check against workbook")
    print(f"  Files checked: {check['files_checked']}; headers with columns not in workbook: {len(check['bad'])}")
    print(f"  Column map rows: {len(column_rows):,}; PII Y: {sum(r['is_pii'] == 'Y' for r in column_rows)}; "
          f"REVIEW: {sum(r['is_pii'] == 'REVIEW' for r in column_rows)}")
    print()
    print("Pattern checks")
    print(f"  Monday avg calls / Tue-Fri avg: {st['monday']:.0f} / {st['tue_fri']:.0f};"
          f" weekend avg: {st['weekend']:.0f}")
    print(f"  Surge weekday avg vs pre-surge: {st['surge']:.0f} vs {st['base']:.0f}"
          f" ({st['surge'] / max(1, st['base']):.1f}x)")
    print("  Claimant phone by week (SL % / abandon %):")
    print("    " + "  ".join(f"W{w}: {sl:.0f}/{ab:.0f}" for w, (sl, ab) in st["weekly"].items()))
    print(f"  Repeat calls (customer_key seen within 7 days): {st['repeat_share']:.1f}%")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: %(default)s)")
    p.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK, help="Five9 workbook (source of truth)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: data/source)")
    p.add_argument("--column-map", type=Path, default=DEFAULT_COLUMN_MAP, help="column map CSV to write")
    args = p.parse_args(argv)

    schema = load_workbook_schema(args.workbook)
    gen = Generator(args.seed).run().finalize()
    res = write_outputs(gen, args.out, schema, args.seed)
    column_rows = write_column_map(schema, args.column_map)
    check = verify_against_workbook(args.out, schema)
    print_summary(gen, res, check, args.out, column_rows)


if __name__ == "__main__":
    main()

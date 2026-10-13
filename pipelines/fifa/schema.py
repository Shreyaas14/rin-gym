"""Explicit, shared Arrow schemas. Opaque provider IDs are always strings."""

import pyarrow as pa

SCHEMA_VERSION = 1
NORMALIZATION_VERSION = "fifa-v3-1"
UTC = pa.timestamp("us", tz="UTC")
TYPES = {
    "str": pa.string(),
    "int": pa.int32(),
    "long": pa.int64(),
    "float": pa.float64(),
    "bool": pa.bool_(),
    "time": UTC,
    "flags": pa.list_(pa.field("element", pa.string())),
}


def schema(groups):
    fields = []
    for kind, required, names in groups:
        fields.extend(
            pa.field(name, TYPES[kind], nullable=not required) for name in names.split()
        )
    return pa.schema(
        fields,
        metadata={
            b"schema_version": str(SCHEMA_VERSION).encode(),
            b"normalization_version": NORMALIZATION_VERSION.encode(),
        },
    )


MATCHES = schema(
    [
        (
            "str",
            True,
            "source match_id competition_id competition_name season_id season_name",
        ),
        ("str", False, "stage_id stage_name group_id group_name matchday_raw"),
        ("time", False, "scheduled_kickoff_utc"),
        (
            "str",
            False,
            "home_team_id away_team_id home_team_name away_team_name home_placeholder away_placeholder",
        ),
        ("int", False, "match_status_code"),
        ("str", True, "match_status"),
        (
            "int",
            False,
            "calendar_home_score calendar_away_score regulation_home_score regulation_away_score final_home_score final_away_score shootout_home_score shootout_away_score",
        ),
        ("bool", False, "went_to_extra_time went_to_shootout"),
        ("str", False, "winner_team_id"),
        ("int", False, "result_type_code"),
        ("str", False, "ifes_match_id statsperform_match_id"),
        ("str", True, "timeline_status"),
        ("int", False, "event_count located_event_count"),
        ("flags", True, "quality_flags"),
        ("str", True, "calendar_response_sha256"),
        ("str", False, "timeline_response_sha256"),
        ("time", False, "calendar_fetched_at_utc timeline_fetched_at_utc"),
        ("str", True, "dataset_version"),
        ("int", True, "schema_version"),
        ("str", True, "normalization_version source_match_json"),
    ]
)

EVENTS = schema(
    [
        ("str", True, "source match_id competition_id season_id event_uid event_id"),
        ("int", True, "source_index"),
        ("time", False, "event_time_utc"),
        ("int", False, "period_code"),
        ("str", True, "phase"),
        ("str", False, "match_minute_raw"),
        ("int", False, "clock_minute stoppage_minute"),
        ("long", False, "period_elapsed_ms"),
        ("int", False, "event_type_code"),
        ("str", False, "event_type_label"),
        ("str", True, "event_kind"),
        (
            "str",
            False,
            "event_description team_id player_id sub_player_id sub_team_id person_id",
        ),
        ("int", False, "home_score away_score home_shootout_score away_shootout_score"),
        ("float", False, "x_raw y_raw"),
        ("str", True, "location_status"),
        ("float", False, "goal_gate_x_raw goal_gate_y_raw"),
        ("int", False, "foul_party_code"),
        ("str", False, "qualifiers_json var_notification_json"),
        ("flags", True, "quality_flags"),
        ("str", True, "source_response_sha256"),
        ("time", False, "fetched_at_utc"),
        ("str", True, "dataset_version"),
        ("int", True, "schema_version"),
        ("str", True, "normalization_version source_event_json"),
    ]
)

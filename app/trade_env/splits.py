"""Train, validation and test by whole match."""


def is_demo(record, config):
    return record["match_id"] in config["demo_match_ids"]


def split_for(record, config):
    """train | val | test, or None for a competition the config does not place."""
    if is_demo(record, config):
        return "test"
    competition = record.get("competition_id")
    if competition in config["train_competitions"]:
        return "train"
    if competition != config["world_cup_competition"]:
        return None
    stage = record.get("stage_name")
    if stage:
        return "val" if stage == config["group_stage_name"] else "test"
    # no stage on the record: same-format UTC stamps compare chronologically
    return "test" if record["scheduled_utc"] >= config["knockout_from_utc"] else "val"

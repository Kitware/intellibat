from pathlib import Path
import json


CONFIG_SCHEMA = {
    "sample_rate": {
        "type": int, 
        "validators": [], 
        "name": "Sample Rate",
        "placeholder": "Sample rate for recordings",
    },
    "threshold_freq": {
        "type": int, 
        "validators": [], 
        "name": "Threshold Frequency",
        "placeholder": "Minimum frequency to trigger recording",
    },
}
CONFIG_PATH = Path("/etc/intellibat/config.json")


def convert_new_value(setting_name, value):
    expected_type = CONFIG_SCHEMA[setting_name]["type"]
    try:
        return expected_type(value)
    except ValueError:
        return None


def validate_setting_value(setting_name, value) -> None | list[str]:
    errors = []
    for validator in CONFIG_SCHEMA[setting_name]["validators"]:
        result = validator(value)
        if result is not None:
            errors.append(result)
    return errors or None


def load_config(config_path: Path):
    if not config_path.exists():
        raise Exception("File doesn't exist")
    with open(config_path) as f:
        try:
            config = json.load(f)
            return config
        except Exception as e:
            raise Exception("File isn't valid json")


def write_config(config_dict: dict, config_path: Path):
    with open(config_path, "w") as f:
        json.dump(config_dict, f, indent=2)



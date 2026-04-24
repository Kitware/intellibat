import click
from pathlib import Path
import json


CONFIG_SCHEMA = {
    "sample_rate": {"type": int, "validators": []},
    "threshold_freq": {"type": int, "validators": []},
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


def get_available_settings_text():
    available_settings = "Available settings:\n"
    for setting in CONFIG_SCHEMA:
        available_settings = available_settings + f"\t{setting}\n"
    return available_settings


def generate_errors_text(setting: str, value, errors: list[str]):
    errors_text = f"{value} is not valid value for {setting}:\n"
    for idx, error in enumerate(errors):
        errors_text = errors_text + f"\t{idx + 1}. {error}\n"
    return errors_text


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


@click.group()
def intellibat_config():
    pass


@intellibat_config.command("list")
def list_settings():
    click.echo(get_available_settings_text())


@intellibat_config.command("get")
@click.argument("setting-name", type=str)
def get(setting_name):
    if setting_name not in CONFIG_SCHEMA:
        click.echo(f"{setting_name} is not a configuration name\n")
        click.echo(get_available_settings_text())
    else:
        try:
            config = load_config(CONFIG_PATH)
            if setting_name not in config:
                click.echo(message=f"{setting_name} is not in the current configuration")
                return
            value = config[setting_name]
            msg = f"{setting_name}: {value}"
            click.echo(message=msg)
        except Exception as e:
            click.echo(str(e))


@intellibat_config.command("set")
@click.argument("setting-name", type=str)
@click.argument("new-value")
def set(setting_name, new_value):
    if setting_name not in CONFIG_SCHEMA:
        click.echo(get_available_settings_text())
    else:
        value = convert_new_value(setting_name, new_value)
        if value is None:
            expected_type = CONFIG_SCHEMA[setting_name]["type"].__name__
            click.echo(message=f"Invalid value for {setting_name}. Could not convert {new_value} to {expected_type}")
            return
        errors = validate_setting_value(setting_name, value)
        if not errors:
            try:
                config = load_config(CONFIG_PATH)
                config[setting_name] = value
                write_config(config, CONFIG_PATH)
                click.echo(message=f"Updated {setting_name} to {value}")
            except Exception as e:
                click.echo(str(e))
        else:
            click.echo(message=generate_errors_text(setting_name, new_value, errors))


if __name__ == "__main__":
    intellibat_config()

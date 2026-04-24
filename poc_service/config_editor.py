import click
from pathlib import Path
import json


def is_int(input) -> bool | str:
    try:
        _ = int(input)
        return True
    except:
        return f"{input} is not an integer."


CONFIG_SCHEMA = {
    "sample_rate": {"type": "int", "validators": [is_int]},
    "threshold_freq": {"type": "int", "validators": [is_int]},
}
CONFIG_PATH = Path("/etc/intellibat/config.json")


def get_available_settings_text():
    available_settings = "Available settings:\n"
    for setting in CONFIG_SCHEMA:
        available_settings = available_settings + f"\t{setting}\n"
    return available_settings


@click.group()
def intellibat_config():
    pass


@intellibat_config.command("list")
def list_settings():
    click.echo(get_available_settings_text())


@intellibat_config.command("get-setting")
@click.argument("setting-name", type=str)
def get(setting_name):
    if setting_name not in CONFIG_SCHEMA:
        click.echo(get_available_settings_text())
    else:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
            msg = f"{setting_name}: {config[setting_name]}"
            click.echo(message=msg)

@intellibat_config.command("set")
@click.argument("setting-name", type=str)
def set(setting_name):
    if setting_name not in CONFIG_SCHEMA:
        click.echo(get_available_settings_text())
    else:
        # TODO
        pass

if __name__ == "__main__":
    intellibat_config()

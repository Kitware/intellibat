from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Label, Input
from textual.containers import HorizontalGroup
from pathlib import Path

from .config_editor import (
    CONFIG_PATH,
    CONFIG_SCHEMA,
    load_config,
    write_config,
    validate_setting_value,
)

TYPE_MAP = {
    int: "integer"
}

class IntellibatConfigEditorApp(App):
    """A textual app to edit Intellibat config"""
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config_path: Path):
        super().__init__()
        self.config = load_config(config_path)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        for config_key in CONFIG_SCHEMA:
            config_name = CONFIG_SCHEMA[config_key]["name"]
            config_placeholder = CONFIG_SCHEMA[config_key]["placeholder"]
            config_type = TYPE_MAP.get(CONFIG_SCHEMA[config_key]["type"], "text")
            config_value = self.config.get(config_key, None)
            value = str(config_value) if config_value is not None else ""
            yield HorizontalGroup(
                Label(config_name), 
                Input(
                    value, 
                    placeholder=config_placeholder, 
                    type=config_type,
                    id=config_key,
                ),
            )

    def action_quit(self) -> None:
        # TODO check for unsaved edits
        self.exit()


if __name__ == "__main__":
    app = IntellibatConfigEditorApp(CONFIG_PATH)
    app.run()

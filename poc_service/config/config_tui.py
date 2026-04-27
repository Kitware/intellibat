from textual.app import App, ComposeResult
from textual.widgets import Header, Footer

from .config_editor import (
    CONFIG_PATH,
    CONFIG_SCHEMA,
    load_config,
    write_config,
    validate_setting_value,
)


class IntellibatConfigEditorApp(App):
    """A textual app to edit Intellibat config"""
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def quit(self) -> None:
        # TODO check for unsaved edits
        self.exit()


if __name__ == "__main__":
    app = IntellibatConfigEditorApp()
    app.run()

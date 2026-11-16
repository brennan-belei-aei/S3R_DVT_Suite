"""A headless stand-in for tkinter, so GUI-driven collection scripts run unattended under test.

Widgets are inert. When a script calls mainloop() on its setup window, the fake "operator":
  1. types `entries` into the field whose label (grid column 0) sits on the same row (column 1),
  2. sets Radiobutton/Checkbutton choices by their visible text (`choices`),
  3. clicks the button whose text is `start`.
Every other mainloop() (progress windows) returns at once. Message boxes are recorded and answer
"yes". Unknown labels or buttons raise, so a driver cannot silently stop matching the GUI.
"""
from __future__ import annotations

import types
from typing import Any, Callable


class TclError(Exception):
    pass


def _noop(*args, **kwargs):
    return 0


class Operator:
    def __init__(self, entries: dict[str, str], choices: dict[str, Any], start: str | None, directory: str,
                 record: Callable[..., None]):
        self.entries = dict(entries)
        self.choices = dict(choices)
        self.start = start
        self.directory = directory
        self.record = record
        self.widgets: list[Widget] = []
        self.messages: list[tuple[str, str, str]] = []
        self.started = False

    # -- the operator's one interaction with the setup window ------------------------------
    def run_setup(self, root: "Tk") -> None:
        if self.started or self.start is None or not any(w.text == self.start for w in self.widgets if isinstance(w, Button)):
            return
        self.started = True
        rows = {}
        for widget in self.widgets:
            if widget.cell is not None:
                rows.setdefault((id(widget.master), widget.cell[0]), {})[widget.cell[1]] = widget
        filled = set()
        for cells in rows.values():
            label, field = cells.get(0), cells.get(1)
            if isinstance(label, Label) and label.text in self.entries and field is not None:
                field.fill(self.entries[label.text])
                filled.add(label.text)
        missing = set(self.entries) - filled
        if missing:
            raise KeyError(f"no GUI field labelled {sorted(missing)}")
        for text, value in self.choices.items():
            matches = [w for w in self.widgets if isinstance(w, (Radiobutton, Checkbutton)) and w.text == text]
            if not matches:
                raise KeyError(f"no GUI choice labelled {text!r}")
            for widget in matches:
                widget.choose(value)
        [button] = [w for w in self.widgets if isinstance(w, Button) and w.text == self.start]
        button.kw["command"]()

    def message(self, kind: str, title: str = "", message: str = "", **kwargs) -> bool:
        self.messages.append((kind, str(title), str(message)))
        self.record("messagebox", kind, str(title))
        return True


class Variable:
    _default: Any = ""

    def __init__(self, master=None, value=None, name=None):
        self._value = self._default if value is None else value
        self._traces: list[Callable] = []

    def get(self):
        return self._value

    def set(self, value) -> None:
        self._value = value
        for callback in list(self._traces):
            callback("", "", "write")

    def trace_add(self, mode, callback):
        self._traces.append(callback)
        return f"trace{len(self._traces)}"

    trace = trace_variable = trace_add

    def trace_remove(self, *args) -> None:
        pass


class StringVar(Variable):
    def get(self):
        return str(self._value)


class BooleanVar(Variable):
    _default = False

    def get(self):
        return bool(self._value)


class IntVar(Variable):
    _default = 0

    def get(self):
        return int(self._value)


class DoubleVar(Variable):
    _default = 0.0

    def get(self):
        return float(self._value)


class Widget:
    operator: Operator | None = None

    def __init__(self, master=None, *args, **kw):
        self.master = master
        self.kw = dict(kw)
        self.cell: tuple[int, int] | None = None
        self._items: dict[str, Any] = {}
        if Widget.operator is not None:
            Widget.operator.widgets.append(self)

    @property
    def text(self) -> str:
        return str(self.kw.get("text", ""))

    def grid(self, *args, row=0, column=0, **kw):
        self.cell = (int(row), int(column))

    def fill(self, value: str) -> None:
        variable = self.kw.get("textvariable")
        if variable is None:
            raise KeyError(f"field {type(self).__name__} has no textvariable")
        variable.set(value)

    def configure(self, *args, **kw):
        if not args:  # Style.configure("Name.TLabel", ...) styles, it does not reconfigure
            self.kw.update(kw)

    config = configure

    def cget(self, key):
        return self.kw.get(key, "")

    def __setitem__(self, key, value):
        self._items[key] = value

    def __getitem__(self, key):
        return self._items.get(key, self.kw.get(key, ""))

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _noop


class Label(Widget):
    pass


class Button(Widget):
    def invoke(self):
        return self.kw.get("command", _noop)()


class Radiobutton(Widget):
    def choose(self, value) -> None:
        if value:
            self.kw["variable"].set(self.kw.get("value"))


class Checkbutton(Widget):
    def choose(self, value) -> None:
        self.kw["variable"].set(bool(value))


class Text(Widget):
    def __init__(self, master=None, *args, **kw):
        super().__init__(master, *args, **kw)
        self.content = ""

    def insert(self, index, text, *tags):
        self.content += str(text)

    def delete(self, *args):
        self.content = ""

    def get(self, *args):
        return self.content + "\n"

    def fill(self, value: str) -> None:
        self.content = str(value)


class Tk(Widget):
    def __init__(self, *args, **kw):
        super().__init__(None)
        self.destroyed = False

    def after(self, delay, callback=None, *args):
        if callback is not None:  # scheduled UI updates run at once (workers report through after())
            callback(*args)
        return "after#0"

    def mainloop(self, *args):
        if Widget.operator is not None and not self.destroyed:
            Widget.operator.run_setup(self)

    def destroy(self):
        self.destroyed = True


class Toplevel(Tk):
    pass


_WIDGET_TYPES = {cls.__name__: cls for cls in (Widget, Label, Button, Radiobutton, Checkbutton, Text, Tk, Toplevel)}


def _module(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(attrs)

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr.isupper():
            return attr.lower()  # tk constants: tk.END == "end", tk.W == "w", ...
        return _WIDGET_TYPES.get(attr, Widget)

    module.__getattr__ = __getattr__
    return module


def modules(operator: Operator) -> dict[str, types.ModuleType]:
    """sys.modules entries for `tkinter` and the submodules the collection scripts import."""
    Widget.operator = operator
    ttk = _module("tkinter.ttk", Label=Label, Button=Button, Radiobutton=Radiobutton,
                  Checkbutton=Checkbutton, Entry=Widget, Style=Widget)
    filedialog = _module("tkinter.filedialog", askdirectory=lambda *a, **k: operator.directory,
                         askopenfilename=lambda *a, **k: "", asksaveasfilename=lambda *a, **k: "")
    messagebox = _module("tkinter.messagebox", **{
        kind: (lambda kind: lambda *a, **k: operator.message(kind, *a, **k))(kind)
        for kind in ("showerror", "showinfo", "showwarning", "askyesno", "askokcancel", "askyesnocancel")
    })
    tk = _module("tkinter", TclError=TclError, Tk=Tk, Toplevel=Toplevel, Text=Text, Label=Label, Button=Button,
                 Radiobutton=Radiobutton, Checkbutton=Checkbutton, Variable=Variable, StringVar=StringVar,
                 BooleanVar=BooleanVar, IntVar=IntVar, DoubleVar=DoubleVar,
                 ttk=ttk, filedialog=filedialog, messagebox=messagebox)
    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": filedialog, "tkinter.messagebox": messagebox}

import re
from functools import partial
import dataclasses
import inspect
from typing import Optional, Callable, Generic, TypeVar, Literal


from ._message_api import MessageApi
from . import _messages as messages

TComponent = TypeVar("TComponent")


def _default_factory(value):
    return lambda: value


def _apply_default_order(order):
    if order is None:
        return float("inf")
    return order


def _make_unique_id():
    return "unique_id"


def _get_container_id():
    return "container_id"


def _get_param_docstring(docstring, name):
    docstring = docstring or ""
    start = docstring.find("Args:")
    if start == -1:
        return None
    docstring = docstring[start:]
    start = docstring.find(name + ":")
    if start == -1:
        return None
    docstring = docstring[start + len(name) + 1 :].strip()
    return docstring


def _read_field_docstrings():
    docstrings = {}

    # Get current frame
    frame = inspect.currentframe().f_back.f_back

    # Get the source code
    source_code = inspect.getsource(frame)
    source = source_code.splitlines()[frame.f_lineno :]

    # Offset of the current line - blank characters
    offset = source[0][: (len(source[0]) - len(source[0].lstrip()))]

    previous_line = ""
    member_definition_regex = r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:.*"
    for lin in source:
        if lin.strip() != "" and not lin.startswith(offset):
            # End of the class
            break

        lin = lin[len(offset) :]
        if lin[:3] == '"""':
            # This could be a docstring
            # if the previous line is a field definition
            matched = re.match(member_definition_regex, previous_line)
            if matched:
                field = matched.group(1)
                docstrings[field] = lin.strip()[3:-3]
        previous_line = lin
    return docstrings


class GuiApiMixin:
    def __init__(self, api: MessageApi) -> None:
        self._api = api


class ComponentMeta(type):
    @classmethod
    def _build_property(cls, field):
        name = field["name"]

        def fget(self):
            return self._data[name]

        def fset(self, value):
            old_values = self._data.copy()
            if field["setter"] is not None and not getattr(fset, "_called", False):
                try:
                    # Setter can cause chain reaction, so we need to disable tracking changes
                    old_tracking_changes = self._tracking_changes
                    self._tracking_changes = False
                    setattr(fset, "_called", True)
                    field["setter"](self, value)
                finally:
                    delattr(fset, "_called")
                    self._tracking_changes = old_tracking_changes
            else:
                self._data[name] = value

            updates = {
                name: value
                for name, value in self._data.items()
                if value != old_values.get(name)
            }

            # Now, we trigger all changes in a single batched message.
            if updates and self._api is not None:
                self._api._queue(
                    messages.GuiUpdateMessage(
                        id=self.id,
                        updates=updates,
                    )
                )

        if field["readonly"]:
            fset = None
        return property(fget, fset, doc=field["help"])

    @classmethod
    def _build_init(cls, fields, class_docstring):
        init_fields = {k: f for k, f in fields.items() if f["init"]}
        pos_fields = [f for f in init_fields.values() if f["positional"]]
        required_fields = set(f["name"] for f in init_fields.values() if f["required"])

        # Verify that all not-init fields have a default value
        for field in fields.values():
            if not field["init"]:
                # For setter we need setter_default_factory, for non-setter we need default_factory
                if not (
                    (
                        field["setter"] is not None
                        and field["setter_default_factory"] is not None
                    )
                    or (
                        field["setter"] is None and field["default_factory"] is not None
                    )
                ):
                    raise TypeError(
                        f"Non-init field '{field['name']}' must have a default value"
                    )

        # Build init signature
        signature_params = [
            inspect.Parameter("self", inspect.Parameter.POSITIONAL_ONLY)
        ]
        for field in fields.values():
            if not field["init"]:
                continue
            kind = (
                inspect.Parameter.POSITIONAL_OR_KEYWORD
                if field["positional"]
                else inspect.Parameter.KEYWORD_ONLY
            )
            parameter_default = inspect.Parameter.empty
            if (
                field["setter"] is not None
                and field["setter_default_factory"] is not None
            ):
                parameter_default = field["setter_default_factory"]()
            elif field["setter"] is None and field["default_factory"] is not None:
                parameter_default = field["default_factory"]()
            signature_params.append(
                inspect.Parameter(
                    field["name"],
                    kind,
                    annotation=field["setter_annotation"]
                    if field["setter_annotation"] is not None
                    else field["annotation"],
                    default=parameter_default,
                )
            )
        signature = inspect.Signature(parameters=signature_params)

        # Build init docstring
        docstring = class_docstring + "\n\n"
        # If there are any fields in init, add them to the docstring
        if len(signature_params) > 1:
            docstring += "Args:\n"
            for field in fields.values():
                if not field["init"]:
                    continue
                help = (
                    field["setter_help"]
                    if field["setter_help"] is not None
                    else field["help"]
                )
                if help is None:
                    annotation = (
                        field["setter_annotation"]
                        if field["setter_annotation"] is not None
                        else field["annotation"]
                    )
                    help = str(annotation)
                docstring += f"    {field['name']}: {help}\n"

        def __init__(self, *args, **kwargs):
            self._api = None
            self._tracking_changes = True
            self._data = {}
            if len(args) > len(pos_fields):
                raise TypeError(
                    f"__init__() got an unexpected number of positional arguments. {len(args)} were given, expected {len(pos_fields)}"
                )

            field_values = list(zip(pos_fields, args))
            for name, value in kwargs.items():
                if name not in init_fields:
                    raise TypeError(
                        f"__init__() got an unexpected keyword argument '{name}'"
                    )
                field_values.append((init_fields[name], value))

            # Check if all required fields are present
            current_fields = set(f[0]["name"] for f in field_values)
            missing = required_fields - current_fields
            if missing:
                raise TypeError(
                    f"__init__() missing {len(missing)} required arguments: {', '.join(missing)}"
                )

            # Fill all fields with default values
            for field in fields.values():
                if field["name"] not in current_fields:
                    if field["setter_default_factory"] is not None:
                        field["setter"](self, field["setter_default_factory"]())
                    else:
                        self._data[field["name"]] = field["default_factory"]()

            # Set all fields passed to the constructor
            for field, value in field_values:
                setter = field["setter"]
                name = field["name"]
                if setter is None:
                    setattr(self, name, value)
                else:
                    setter(self, value)

        __init__.__signature__ = signature
        __init__.__doc__ = docstring
        return __init__

    @classmethod
    def _build_install(cls, class_name):
        message_name = f"GuiAdd{class_name}Message"
        add_component_message = getattr(messages, message_name)

        def install(self, api: MessageApi) -> None:
            self._api = api
            self._api._queue(add_component_message(**self._data))

        return install

    @classmethod
    def _build_register_callback(cls, function):
        def register_callback(self, callback) -> None:
            assert isinstance(self._api, MessageApi)
            self._api

        return register_callback

    def __new__(cls, name, bases, namespace, abstract: bool = False):
        # Create fields from annotations
        fields = {}
        annotations = namespace.get("__annotations__", {})
        field_docstrings = _read_field_docstrings()
        kw_only_default = False
        for name, value in annotations.items():
            if value is dataclasses.KW_ONLY:
                if kw_only_default:
                    raise TypeError("Cannot use KW_ONLY more than once")
                kw_only_default = True
                continue
            fields[name] = field = {
                "name": name,
                "annotation": value,
                "positional": not kw_only_default,
                "setter": None,
                "setter_annotation": None,
                "required": True,
                "default_factory": None,
                "setter_default_factory": None,
                "init": True,
                "help": field_docstrings.get(name),
                "setter_help": None,
                "readonly": False,
            }
            field_default = namespace.get(name, inspect._empty)
            if field_default is not inspect._empty:
                if isinstance(field_default, dataclasses.Field):
                    # Remove the field from the namespace
                    namespace.pop(name)

                    if field_default.default_factory is not dataclasses.MISSING:
                        field["default_factory"] = field_default.default_factory
                        field["required"] = False
                    elif field_default.default is not dataclasses.MISSING:
                        field["default_factory"] = _default_factory(
                            field_default.default
                        )
                        field["required"] = False

                        # For simple default values, we can return the field to the namespace
                        namespace[name] = field_default.default
                    if field_default.init is False:
                        field["init"] = False
                    if field_default.kw_only is not dataclasses.MISSING:
                        field["positional"] = not field_default.kw_only
                    if field_default.metadata.get("readonly", False):
                        field["readonly"] = True
                else:
                    field["default_factory"] = _default_factory(field_default)
                    field["required"] = False

            if f"__set_{name}__" in namespace:
                field["setter"] = setter = namespace.pop(f"__set_{name}__")
                params = inspect.signature(setter).parameters
                assert len(params) == 2, f"Setter for '{name}' must have 2 parameters"
                param = list(params.values())[1]
                if param.annotation is not inspect._empty:
                    field["setter_annotation"] = param.annotation
                if param.default is not inspect._empty:
                    field["required"] = False
                    field["setter_default_factory"] = _default_factory(param.default)

                # Parse setter docstring
                if setter.__doc__ is not None:
                    field["setter_help"] = setter.__doc__

        # Combine fields from all bases
        for base in bases:
            if not hasattr(base, "__fields__"):
                raise TypeError("All base classes must be created with ComponentMeta")
            fields.update(base.__fields__)

        namespace["__fields__"] = fields

        # We do not define the rest for the abstract class
        if abstract:
            return super().__new__(cls, name, bases, namespace)

        # Create all properties
        for name, field in fields.items():
            namespace[name] = cls._build_property(field)

        # Add the init method
        class_name = namespace.get("__qualname__", name)
        class_docstring = namespace.get(
            "__doc__", f"Creates a new {class_name} instance"
        )
        namespace["__init__"] = cls._build_init(fields, class_docstring)

        # Add install method
        namespace["install"] = cls._build_install(class_name)

        # Build the type
        return super().__new__(cls, name, bases, namespace)

    def _build_add_component_method(cls, name):
        name_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        add_component_method_name = f"add_gui_{name_snake}"

        def add_component(self, *args, **kwargs):
            component = cls(*args, **kwargs)
            component.install(self._api)
            return component

        add_component.__doc__ = cls.__init__.__doc__
        add_component.__signature__ = inspect.signature(cls.__init__)
        add_component.__name__ = add_component_method_name
        return add_component

    def __init__(cls, name, bases, namespace, abstract: bool = False):
        super().__init__(name, bases, namespace)

        # Register the component with the API
        add_component = cls._build_add_component_method(name)
        setattr(GuiApiMixin, add_component.__name__, add_component)


@dataclasses.dataclass
class GuiEvent(Generic[TComponent]):
    client_id: str
    target: TComponent


# class Test(metaclass=ComponentMeta):
#     ok: bool
#     """The ok field"""
#
#     ok2: int = 1
#     """The ok2 field"""
#     ok3: str = dataclasses.field(default=3)
#     """The ok3 field"""
#
#     _: dataclasses.KW_ONLY
#     ok4: int = 1
#
#     def test(self) -> None:
#         pass
#
#     def __set_ok__(self, val: Optional[int] = 9) -> None:
#         """Optional
#          Args:
#             val: The value to set"""
#         self.ok = val + 2


Icon = str
Color = Literal[
    "dark",
    "gray",
    "red",
    "pink",
    "grape",
    "violet",
    "indigo",
    "blue",
    "cyan",
    "green",
    "lime",
    "yellow",
    "orange",
    "teal",
]


# a = Button()
# print(inspect.signature(Button))
# print(Button.__init__.__doc__)
# print(a.ok)
# print(a.ok2)
# print(a._data)


class Component(metaclass=ComponentMeta, abstract=True):
    id: str = dataclasses.field(
        init=False, default_factory=_make_unique_id, metadata={"readonly": True}
    )
    container_id: str = dataclasses.field(
        init=False, default_factory=_get_container_id, metadata={"readonly": True}
    )
    order: float
    """Optional ordering, smallest values will be displayed first."""

    def __set_order__(self, order: Optional[float] = None) -> None:
        """Optional ordering, smallest values will be displayed first."""
        self.order = _apply_default_order(order)


class Button(Component, metaclass=ComponentMeta):
    label: str
    """The text of the button"""

    color: Optional[Color] = None
    icon_base64: Optional[str] = dataclasses.field(init=False, default=None)

    value: str = ""
    """Initial value of the text input."""
    disabled: bool = False
    """Whether the text input is disabled."""
    visible: bool = True
    """Whether the text input is visible."""
    hint: Optional[str] = None
    """Optional hint to display on hover."""

    def __set_icon__(self, icon: Optional[Icon] = None) -> None:
        """
        Args:
            icon: The icon to set
        """
        # Convert the icon to base64
        self.icon_base64 = "base64:" + icon

    def on_click(self, callback: Callable[[GuiEvent["Button"]], None]) -> None:
        ...


class Text(Component, metaclass=ComponentMeta):
    label: str
    """Label to display on the text input."""
    value: str = ""
    """Initial value of the text input."""
    disabled: bool = False
    """Whether the text input is disabled."""
    visible: bool = True
    """Whether the text input is visible."""
    hint: Optional[str] = None
    """Optional hint to display on hover."""


class T:
    pass


dummy_api = T()
dummy_api._queue = lambda x: print(x)
api = GuiApiMixin(dummy_api)
component = api.add_gui_text(label="test", visible=False)
component.value = "test2"
component.visible = True

button = api.add_gui_button(label="test button")

# Test readonly attributes
# component.id = 2  # Cannot set id
